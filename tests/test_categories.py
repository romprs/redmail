from __future__ import annotations

from pathlib import Path

from redmail.imap_client import extract_content
from redmail.plugins import available_plugins
from redmail.plugins.categories import (
    ADVERTISING,
    NOTIFICATIONS,
    REQUESTS,
    SOURCE_HEADERS,
    SOURCE_MODEL,
    SOURCE_RULE,
    SOURCE_USER,
    Category,
    CategoryStore,
    MessageFacts,
)


def _store(tmp_path: Path) -> CategoryStore:
    return CategoryStore(tmp_path / "categories.sqlite3")


def test_standard_categories_are_created_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    CategoryStore(store.path)  # повторное открытие не дублирует
    assert [c.id for c in store.categories()] == [NOTIFICATIONS, REQUESTS, ADVERTISING]


def test_noreply_sender_is_a_notification(tmp_path: Path) -> None:
    store = _store(tmp_path)
    label = store.classify_and_store("acc", "INBOX", 1, MessageFacts(sender_email="noreply@gosuslugi.ru", subject="Статус заявления"))
    assert (label.category_id, label.source) == (NOTIFICATIONS, SOURCE_RULE)


def test_bulk_headers_mean_newsletter_and_ad_words_mean_advertising(tmp_path: Path) -> None:
    store = _store(tmp_path)
    newsletter = MessageFacts(sender_email="news@portal.ru", subject="Новости недели", headers={"List-Unsubscribe": "<mailto:u@x>"})
    ad = MessageFacts(sender_email="shop@market.ru", subject="Только сегодня скидка 30%", headers={"List-Unsubscribe": "<x>"})
    assert store.classify_and_store("acc", "INBOX", 1, newsletter).category_id == NOTIFICATIONS
    label = store.classify_and_store("acc", "INBOX", 2, ad)
    assert (label.category_id, label.source) == (ADVERTISING, SOURCE_HEADERS)


def test_newsletter_with_request_word_in_subject_stays_newsletter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    facts = MessageFacts(sender_email="survey@portal.ru", subject="Запрос обратной связи", headers={"Precedence": "bulk"})
    assert store.classify_and_store("acc", "INBOX", 1, facts).category_id == NOTIFICATIONS


def test_personal_request_by_subject_word(tmp_path: Path) -> None:
    store = _store(tmp_path)
    facts = MessageFacts(sender_email="komarova@amurgpz.ru", subject="Прошу согласовать график")
    assert store.classify_and_store("acc", "INBOX", 1, facts).category_id == REQUESTS


def test_own_category_by_addressees(tmp_path: Path) -> None:
    """Своя категория с описанием по адресатам: домен, маска, часть имени."""
    store = _store(tmp_path)
    store.save_category(Category(id="", name="Налоговая", color="#8E24AA", senders=["nalog.ru"]))
    store.save_category(Category(id="", name="Руководство", color="#43A047", senders=["*director*@amurgpz.ru", "Смородин"]))

    tax = store.classify_and_store("acc", "INBOX", 1, MessageFacts(sender_email="inspector@r28.nalog.ru", subject="Требование"))
    boss = store.classify_and_store("acc", "INBOX", 2, MessageFacts(sender_email="msmorodin@amurgpz.ru", sender_name="Смородин М.П.", subject="Совещание"))

    assert tax.category_id == "налоговая"
    assert boss.category_id == "руководство"


def test_unknown_message_stays_without_category(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.classify_and_store("acc", "INBOX", 1, MessageFacts(sender_email="petrov@amurgpz.ru", subject="Обед")) is None


def test_manual_choice_is_kept_and_teaches_the_model(tmp_path: Path) -> None:
    """Самообучение: после нескольких ручных разметок похожие письма
    получают категорию сами."""
    store = _store(tmp_path)
    store.save_category(Category(id="", name="Ремонт", color="#6D4C41"))
    for uid in range(1, 7):
        store.set_user_label("acc", "INBOX", uid, "ремонт", MessageFacts(
            sender_email=f"master{uid}@stroy.ru", subject="Смета на ремонт кровли", text="смета ремонт кровля подрядчик"))
        store.set_user_label("acc", "INBOX", 100 + uid, ADVERTISING, MessageFacts(
            sender_email=f"promo{uid}@shop.ru", subject="Новая коллекция одежды", text="коллекция одежда магазин"))

    guess = store.classify_and_store("acc", "INBOX", 50, MessageFacts(
        sender_email="brigada@stroy.ru", subject="Уточнённая смета кровли", text="подрядчик кровля смета"))
    manual = store.classify_and_store("acc", "INBOX", 1, MessageFacts(sender_email="noreply@x.ru"))

    assert (guess.category_id, guess.source) == ("ремонт", SOURCE_MODEL)
    assert (manual.category_id, manual.source) == ("ремонт", SOURCE_USER)


def test_changing_manual_choice_unlearns_previous(tmp_path: Path) -> None:
    store = _store(tmp_path)
    facts = MessageFacts(sender_email="a@b.ru", subject="Смета кровли")
    store.set_user_label("acc", "INBOX", 1, REQUESTS, facts)
    store.set_user_label("acc", "INBOX", 1, ADVERTISING, facts)

    model = store.model()
    assert model.totals[REQUESTS][0] == 0
    assert model.totals[ADVERTISING][0] == 1


def test_deleting_category_removes_its_labels(tmp_path: Path) -> None:
    store = _store(tmp_path)
    own = store.save_category(Category(id="", name="Проект", color="#000000", senders=["project.ru"]))
    store.classify_and_store("acc", "INBOX", 1, MessageFacts(sender_email="pm@project.ru"))
    store.delete_category(own.id)
    assert store.label("acc", "INBOX", 1) is None


def test_classification_headers_are_read_from_message() -> None:
    raw = (
        b"From: news@portal.ru\r\nSubject: News\r\nList-Unsubscribe: <mailto:u@portal.ru>\r\n"
        b"Precedence: bulk\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nhello"
    )
    from email import message_from_bytes

    content = extract_content(message_from_bytes(raw))
    assert content.mail_headers == {"List-Unsubscribe": "<mailto:u@portal.ru>", "Precedence": "bulk"}


def test_categories_module_is_listed() -> None:
    assert "categories" in {plugin.id for plugin in available_plugins()}


def test_category_column_sorts_names_uncategorized_last_newest_first() -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QTableWidget

    from redmail.ui import main_window as mw

    QApplication.instance() or QApplication([])
    table = QTableWidget(0, mw.MAIL_COLUMN_COUNT)
    rows = [("", "2026-09-17 10:00"), ("Реклама", "2026-09-15 10:00"), ("Обращения", "2026-09-16 10:00"),
            ("Реклама", "2026-09-17 09:00"), ("Обращения", "2026-09-10 10:00")]
    for row, (name, date) in enumerate(rows):
        table.insertRow(row)
        key = mw._category_sort_key(name)
        table.setItem(row, mw.COL_CATEGORY, mw._ThreadSortItem(name, group_value=key, group_key="", rank=0, own=key, date=date))
    table.horizontalHeader().setSortIndicator(mw.COL_CATEGORY, Qt.SortOrder.AscendingOrder)
    table.sortItems(mw.COL_CATEGORY, Qt.SortOrder.AscendingOrder)
    order = [(table.item(r, mw.COL_CATEGORY).text(), table.item(r, mw.COL_CATEGORY).date[:10]) for r in range(len(rows))]
    assert order == [("Обращения", "2026-09-16"), ("Обращения", "2026-09-10"), ("Реклама", "2026-09-17"),
                     ("Реклама", "2026-09-15"), ("", "2026-09-17")]
    assert ("Категория", mw.COL_CATEGORY, Qt.SortOrder.AscendingOrder) in mw.MainWindow._SORT_CHOICES
