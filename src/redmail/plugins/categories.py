"""Категории писем: уведомления и рассылки, обращения и запросы, реклама и
категории, которые человек заводит сам.

Как письмо получает категорию (первое сработавшее):
1. Ручная разметка — человек выбрал категорию письму. Она не
   перезаписывается и сразу идёт в обучение.
2. Адресаты из описания категории («*@nalog.ru», «noreply@*», «nalog.ru»,
   часть имени отправителя).
3. Признаки массовых писем в заголовках (List-Unsubscribe, Precedence: bulk,
   Auto-Submitted) и явные слова рекламы, затем слова темы из описания
   категории.
4. Модель, обученная на ручной разметке этого пользователя (наивный
   байесовский классификатор по словам темы, отправителя и текста). Пока
   примеров мало, модель молчит: лучше без категории, чем наугад.

Всё хранится локально, в отдельной базе профиля, и на сервер не уходит.
"""
from __future__ import annotations

import fnmatch
import math
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

NOTIFICATIONS = "notifications"
REQUESTS = "requests"
ADVERTISING = "advertising"

SOURCE_USER = "user"
SOURCE_RULE = "rule"
SOURCE_HEADERS = "headers"
SOURCE_MODEL = "model"

#: Сколько размеченных вручную писем нужно в категории, чтобы модель начала
#: предлагать её сама.
MIN_EXAMPLES_PER_CATEGORY = 5
#: Насколько модель должна быть уверена (доля вероятности лучшей категории).
MIN_MODEL_CONFIDENCE = 0.75

_SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    senders TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '',
    builtin INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS labels (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,
    category_id TEXT,
    source TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 1,
    tokens TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (account, folder, uid)
);
CREATE TABLE IF NOT EXISTS word_counts (
    category_id TEXT NOT NULL,
    word TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (category_id, word)
);
CREATE TABLE IF NOT EXISTS category_totals (
    category_id TEXT PRIMARY KEY,
    documents INTEGER NOT NULL DEFAULT 0,
    words INTEGER NOT NULL DEFAULT 0
);
"""

_DEFAULTS = (
    (
        NOTIFICATIONS, "Уведомления и рассылки", "#607D8B",
        "Автоматические письма: уведомления систем, рассылки, дайджесты, ответы роботов.",
        "noreply@*\nno-reply@*\nnotification*@*\nnotify@*\nmailer-daemon@*\npostmaster@*\nrobot@*\n*-noreply@*",
        "уведомление\nрассылка\nдайджест\nnotification",
    ),
    (
        REQUESTS, "Обращения и запросы", "#1E88E5",
        "Письма от людей с просьбой, вопросом или поручением: прошу, согласуйте, подпишите, направьте.",
        "",
        "прошу\nпросьба\nзапрос\nсогласовать\nсогласование\nподписать\nпредоставить\nнаправить",
    ),
    (
        ADVERTISING, "Реклама", "#E53935",
        "Коммерческие предложения, акции, скидки, приглашения на вебинары продавцов.",
        "",
        "скидка\nакция\nраспродажа\nпромокод\nспецпредложение\nкоммерческое предложение\nвебинар",
    ),
)

_BULK_HEADERS = ("list-unsubscribe", "list-id", "x-campaign", "x-mc-user", "feedback-id", "x-sg-eid", "x-mailgun-tag")
_AD_WORDS = ("скидк", "акци", "распродаж", "промокод", "спецпредложен", "выгодн", "бесплатн", "купит", "закаж")

_WORD_RE = re.compile(r"[a-zа-яё]{3,}", re.IGNORECASE)


@dataclass
class Category:
    id: str
    name: str
    color: str
    description: str = ""
    senders: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    builtin: bool = False
    position: int = 0


@dataclass
class MessageFacts:
    """То, по чему решается категория: без интерфейса и без сети."""

    sender_email: str = ""
    sender_name: str = ""
    subject: str = ""
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    to: str = ""


@dataclass
class Label:
    category_id: str | None
    source: str
    score: float = 1.0


def _lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


class CategoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
                for position, (cid, name, color, description, senders, keywords) in enumerate(_DEFAULTS):
                    conn.execute(
                        "INSERT INTO categories (id, name, color, description, senders, keywords, builtin, position) "
                        "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                        (cid, name, color, description, senders, keywords, position),
                    )
                conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=60)
        conn.executescript(_SCHEMA)
        return conn

    # -- категории ---------------------------------------------------------

    def categories(self) -> list[Category]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, name, color, description, senders, keywords, builtin, position FROM categories "
                "ORDER BY position, name"
            ).fetchall()
        return [
            Category(id=r[0], name=r[1], color=r[2], description=r[3], senders=_lines(r[4]),
                     keywords=_lines(r[5]), builtin=bool(r[6]), position=r[7])
            for r in rows
        ]

    def save_category(self, category: Category) -> Category:
        if not category.id:
            base = re.sub(r"[^0-9a-zа-я]+", "-", category.name.casefold()).strip("-") or "category"
            existing = {c.id for c in self.categories()}
            candidate, number = base, 2
            while candidate in existing:
                candidate, number = f"{base}-{number}", number + 1
            category.id = candidate
            category.position = len(existing)
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO categories (id, name, color, description, senders, keywords, builtin, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "color = excluded.color, description = excluded.description, senders = excluded.senders, "
                "keywords = excluded.keywords, position = excluded.position",
                (category.id, category.name, category.color, category.description, "\n".join(category.senders),
                 "\n".join(category.keywords), int(category.builtin), category.position),
            )
            conn.commit()
        return category

    def delete_category(self, category_id: str) -> None:
        """Удаляет категорию, её обучение и метки писем с ней."""
        with closing(self._connect()) as conn:
            for table in ("categories", "word_counts", "category_totals"):
                column = "id" if table == "categories" else "category_id"
                conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (category_id,))
            conn.execute("DELETE FROM labels WHERE category_id = ?", (category_id,))
            conn.commit()

    # -- метки писем ---------------------------------------------------------

    def labels_for(self, account: str, folder: str) -> dict[int, Label]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT uid, category_id, source, score FROM labels WHERE account = ? AND folder = ?",
                (account, folder),
            ).fetchall()
        return {r[0]: Label(category_id=r[1], source=r[2], score=r[3]) for r in rows}

    def label(self, account: str, folder: str, uid: int) -> Label | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT category_id, source, score FROM labels WHERE account = ? AND folder = ? AND uid = ?",
                (account, folder, uid),
            ).fetchone()
        return Label(category_id=row[0], source=row[1], score=row[2]) if row else None

    def _store_label(self, conn, account: str, folder: str, uid: int, label: Label, tokens: list[str]) -> None:
        conn.execute(
            "INSERT INTO labels (account, folder, uid, category_id, source, score, tokens) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account, folder, uid) DO UPDATE SET category_id = excluded.category_id, "
            "source = excluded.source, score = excluded.score, tokens = excluded.tokens",
            (account, folder, uid, label.category_id, label.source, label.score, " ".join(tokens)),
        )

    # -- обучение -----------------------------------------------------------

    def _adjust_counts(self, conn, category_id: str, tokens: list[str], sign: int) -> None:
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
        for word, count in counts.items():
            conn.execute(
                "INSERT INTO word_counts (category_id, word, count) VALUES (?, ?, ?) "
                "ON CONFLICT(category_id, word) DO UPDATE SET count = MAX(0, count + excluded.count)",
                (category_id, word, sign * count),
            )
        conn.execute(
            "INSERT INTO category_totals (category_id, documents, words) VALUES (?, ?, ?) "
            "ON CONFLICT(category_id) DO UPDATE SET documents = MAX(0, documents + excluded.documents), "
            "words = MAX(0, words + excluded.words)",
            (category_id, sign, sign * len(tokens)),
        )

    def set_user_label(self, account: str, folder: str, uid: int, category_id: str | None, facts: MessageFacts) -> None:
        """Человек сам выбрал категорию (или «без категории»). Прежний ручной
        выбор этого письма из обучения убирается, новый — добавляется."""
        tokens = tokenize(facts)
        with closing(self._connect()) as conn:
            previous = conn.execute(
                "SELECT category_id, source, tokens FROM labels WHERE account = ? AND folder = ? AND uid = ?",
                (account, folder, uid),
            ).fetchone()
            if previous and previous[1] == SOURCE_USER and previous[0]:
                self._adjust_counts(conn, previous[0], previous[2].split(), -1)
            if category_id:
                self._adjust_counts(conn, category_id, tokens, +1)
            self._store_label(conn, account, folder, uid, Label(category_id, SOURCE_USER, 1.0), tokens)
            conn.commit()

    def model(self) -> "NaiveBayes":
        with closing(self._connect()) as conn:
            totals = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT category_id, documents, words FROM category_totals")}
            counts: dict[str, dict[str, int]] = {}
            for category_id, word, count in conn.execute("SELECT category_id, word, count FROM word_counts WHERE count > 0"):
                counts.setdefault(category_id, {})[word] = count
        return NaiveBayes(totals, counts)

    # -- разметка -------------------------------------------------------------

    def classify_and_store(self, account: str, folder: str, uid: int, facts: MessageFacts, *, model=None) -> Label | None:
        """Категория письма без ручной разметки: считается и запоминается.
        Ручную разметку не трогает. Возвращает итоговую метку."""
        existing = self.label(account, folder, uid)
        if existing is not None and existing.source == SOURCE_USER:
            return existing
        categories = self.categories()
        label = classify(facts, categories, model if model is not None else self.model())
        with closing(self._connect()) as conn:
            self._store_label(conn, account, folder, uid, label or Label(None, SOURCE_MODEL, 0.0), tokenize(facts))
            conn.commit()
        return label

    def forget_automatic(self) -> None:
        """Сбросить автоматическую разметку (ручная остаётся) — перед разметкой заново."""
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM labels WHERE source != ?", (SOURCE_USER,))
            conn.commit()


# ---------------------------------------------------------------------------


def tokenize(facts: MessageFacts) -> list[str]:
    """Слова письма для модели: тема весомее текста, домен отправителя — отдельным словом."""
    def stems(text: str) -> list[str]:
        return [word.casefold().replace("ё", "е")[:7] for word in _WORD_RE.findall(text or "")]

    tokens = stems(facts.subject) * 2 + stems((facts.text or "")[:3000])
    domain = (facts.sender_email or "").rpartition("@")[2].casefold()
    if domain:
        tokens.append(f"@{domain}")
    return tokens


def _sender_matches(pattern: str, facts: MessageFacts) -> bool:
    pattern = pattern.strip().casefold()
    if not pattern:
        return False
    email = (facts.sender_email or "").casefold()
    if "@" not in pattern and "*" not in pattern:
        # «nalog.ru» — домен целиком; «Иванов» — часть имени отправителя
        if "." in pattern:
            return email.endswith("@" + pattern) or email.endswith("." + pattern)
        return pattern in (facts.sender_name or "").casefold()
    return fnmatch.fnmatchcase(email, pattern)


def _keyword_matches(keyword: str, subject: str) -> bool:
    keyword = keyword.strip().casefold().replace("ё", "е")
    return bool(keyword) and keyword in subject


def classify(facts: MessageFacts, categories: list[Category], model: "NaiveBayes | None" = None) -> Label | None:
    by_id = {category.id: category for category in categories}
    subject = (facts.subject or "").casefold().replace("ё", "е")

    # 2. Адресаты из описания категории — самое точное, что сказал человек.
    for category in categories:
        if any(_sender_matches(pattern, facts) for pattern in category.senders):
            return Label(category.id, SOURCE_RULE, 1.0)

    # 3. Признаки массовой рассылки в заголовках.
    headers = {key.casefold(): value for key, value in (facts.headers or {}).items()}
    bulk = any(name in headers for name in _BULK_HEADERS) or headers.get("precedence", "").casefold() in ("bulk", "list", "junk")
    automatic = headers.get("auto-submitted", "no").casefold() not in ("", "no")
    if bulk or automatic:
        text = subject + " " + (facts.text or "")[:2000].casefold()
        if ADVERTISING in by_id and bulk and any(word in text for word in _AD_WORDS):
            return Label(ADVERTISING, SOURCE_HEADERS, 0.8)
        if NOTIFICATIONS in by_id:
            return Label(NOTIFICATIONS, SOURCE_HEADERS, 0.8)

    # Слова темы из описания категории — после заголовков: рассылка с темой
    # «Запрос обратной связи» — это рассылка, а не обращение.
    for category in categories:
        if any(_keyword_matches(keyword, subject) for keyword in category.keywords):
            return Label(category.id, SOURCE_RULE, 0.9)

    # 4. Модель, обученная на ручной разметке.
    if model is not None:
        guess = model.predict(tokenize(facts), allowed=set(by_id))
        if guess is not None:
            return Label(guess[0], SOURCE_MODEL, guess[1])
    return None


class NaiveBayes:
    """Мультиномиальный наивный байес по словам — считается на лету из
    таблиц счётчиков, без сторонних библиотек."""

    def __init__(self, totals: dict[str, tuple[int, int]], counts: dict[str, dict[str, int]]) -> None:
        self.totals = totals
        self.counts = counts
        self.vocabulary = len({word for words in counts.values() for word in words}) or 1

    def predict(self, tokens: list[str], *, allowed: set[str] | None = None) -> tuple[str, float] | None:
        trained = [
            cid for cid, (documents, _words) in self.totals.items()
            if documents >= MIN_EXAMPLES_PER_CATEGORY and (allowed is None or cid in allowed)
        ]
        if len(trained) < 2 or not tokens:
            return None
        all_documents = sum(self.totals[cid][0] for cid in trained)
        scores: dict[str, float] = {}
        for cid in trained:
            documents, words = self.totals[cid]
            words_in_category = self.counts.get(cid, {})
            score = math.log(documents / all_documents)
            denominator = words + self.vocabulary
            for token in tokens:
                score += math.log((words_in_category.get(token, 0) + 1) / denominator)
            scores[cid] = score
        best = max(scores, key=scores.get)
        top = scores[best]
        probability = 1.0 / sum(math.exp(value - top) for value in scores.values())
        if probability < MIN_MODEL_CONFIDENCE:
            return None
        return best, probability
