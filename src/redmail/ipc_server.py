from __future__ import annotations

"""Локальный канал управления приложением (IPC).

Зачем: внешняя программа (в первую очередь — голосовой ассистент, который
будет жить отдельным проектом) должна уметь попросить уже запущенный
почтовый клиент открыть окно нового письма/встречи с готовыми полями,
разложить почту по правилам и т.п.

Транспорт — ТОЛЬКО QLocalServer/QLocalSocket (на Linux это unix-сокет, на
Windows — именованный канал), намеренно НЕ TCP: канал управления не должен
быть доступен по сети ни при какой конфигурации, иначе любой, кто дотянулся
до порта, смог бы открывать окна и двигать чужую почту. Дополнительно
сокет создаётся с UserAccessOption — доступ только тому же пользователю ОС,
а не всем локальным пользователям машины.

Протокол — построчный JSON ("jsonl"): один запрос = одна строка
{"action": "...", "args": {...}}, один ответ = одна строка
{"ok": true, ...} либо {"ok": false, "error": "..."}. Соединение может
переиспользоваться под несколько запросов подряд.

ВАЖНО про поток и вложенный цикл событий: сигналы QLocalServer приходят в
том потоке, которому принадлежит объект сервера, — то есть в GUI-потоке,
раз сервер создаётся в MainWindow.__init__. Поэтому обработчики можно без
опаски трогать виджеты. Но модальный диалог (QDialog.exec()) НЕЛЬЗЯ
открывать прямо из обработчика readyRead: exec() запускает вложенный цикл
событий и не вернётся, пока пользователь не закроет окно, — ответ клиенту
всё это время не был бы записан, а внутри вложенного цикла пришли бы
следующие readyRead и обработались бы рекурсивно. Поэтому команды,
открывающие диалог, только СОЗДАЮТ его синхронно (чтобы ошибку вроде «нет
учётной записи» вернуть клиенту сразу) и откладывают exec() через
QTimer.singleShot(0, ...) — он выполнится, когда мы уже вышли из
обработчика сокета и ответ ушёл.
"""

import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from redmail.paths import app_dir

#: Версия формата обмена — печатается в ответе на ping, чтобы клиент мог
#: отличить несовместимый протокол от «просто другой сборки приложения».
PROTOCOL = "jsonl-v1"

#: Имя локального сокета. Фиксированное — клиенту не нужно ничего искать.
#: Переопределяется REDMAIL_IPC_NAME: нужно для тестов (каждый тест берёт
#: своё имя, чтобы не драться за общее) и для запуска двух сборок рядом.
DEFAULT_SERVER_NAME = "redmail-ipc"

#: Ограничение на длину одной строки запроса. Без него сломанный или
#: недоброжелательный клиент, который шлёт байты и никогда не шлёт "\n",
#: заставил бы буфер расти до исчерпания памяти.
MAX_LINE_BYTES = 1 << 20

#: Сколько ждать ответа от УЖЕ занятого имени сокета, проверяя, живой там
#: сервер или осталась «мёртвая» запись от упавшего прошлого запуска.
PROBE_TIMEOUT_MS = 300


def server_name() -> str:
    return os.environ.get("REDMAIL_IPC_NAME") or DEFAULT_SERVER_NAME


def endpoint_file_path() -> Path:
    """Куда сервер кладёт фактический адрес сокета.

    Полный путь сокета зависит от платформы (Linux — файл unix-сокета в
    $XDG_RUNTIME_DIR или /tmp, Windows — \\\\.\\pipe\\<имя>), и угадывать его
    в клиенте — лишний источник ошибок. Сервер просто пишет то, что вернул
    QLocalServer.fullServerName(), рядом с остальными настройками.
    """
    return app_dir() / "ipc-endpoint.json"


# --------------------------------------------------------------------------
# Разбор аргументов
# --------------------------------------------------------------------------


def _text(value, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} должен быть строкой")
    return value.strip()


def _recipients_text(value, field: str) -> str:
    """"to"/"cc"/"bcc" принимаем и строкой через запятую, и списком строк —
    голосовой стороне удобнее список, человеку в тестовом скрипте — строка.
    Поля ComposeDialog всё равно текстовые, поэтому сводим к одной строке."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_text(item, field) for item in value]
        return ", ".join(part for part in parts if part)
    raise ValueError(f"{field} должен быть строкой или списком строк")


def _email_list(value, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [item for item in (_text(entry, field) for entry in value) if item]
    raise ValueError(f"{field} должен быть списком строк")


def _positive_int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} должен быть целым числом минут")
    if value <= 0:
        raise ValueError(f"{field} должен быть больше нуля")
    return value


def parse_iso_datetime(value: str, field: str = "start") -> datetime:
    """ISO 8601 → aware datetime в UTC (в таком виде время хранит
    calendar_store.Event). Разбора «завтра в три» здесь сознательно нет —
    это задача голосовой стороны, сюда приходит уже готовая дата.

    Суффикс "Z" datetime.fromisoformat() понимает только начиная с Python
    3.11, а проект заявлен с 3.9 — подменяем его на "+00:00" вручную.
    Время без зоны считаем местным (человек говорит о своём времени).
    """
    text = _text(value, field)
    if not text:
        raise ValueError(f"{field} обязателен: дата и время в формате ISO 8601")
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{field}: не разобрать дату/время ISO 8601: {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def parse_iso_date(value, field: str = "date") -> date:
    """"YYYY-MM-DD" → date. Отдельно от parse_iso_datetime: find_events ищет
    события ЗА ДЕНЬ, а не в конкретный момент времени, и голосовая сторона
    для темы вроде «перенеси встречу ... на завтра» знает только дату, не час."""
    text = _text(value, field)
    if not text:
        raise ValueError(f"{field} обязателен: дата в формате YYYY-MM-DD")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{field}: не разобрать дату (ожидается YYYY-MM-DD): {value!r}") from None


# --------------------------------------------------------------------------
# Команды
# --------------------------------------------------------------------------


def _app_version() -> str:
    # Ленивый импорт: redmail.__main__ импортирует main_window, а main_window
    # импортирует этот модуль — импорт на уровне файла замкнул бы кольцо.
    try:
        from redmail.__main__ import app_version

        return app_version()
    except Exception:
        return "?"


def _handle_ping(_controller, _args) -> dict:
    return {"pong": True, "version": _app_version(), "protocol": PROTOCOL}


def _handle_focus(controller, _args) -> dict:
    controller.ipc_focus()
    return {"focused": True}


def _handle_compose_email(controller, args) -> dict:
    to = _recipients_text(args.get("to"), "to")
    if not to:
        raise ValueError("to обязателен: хотя бы один получатель")
    controller.ipc_compose_email(
        to=to,
        subject=_text(args.get("subject"), "subject"),
        body=_text(args.get("body"), "body"),
        cc=_recipients_text(args.get("cc"), "cc"),
        bcc=_recipients_text(args.get("bcc"), "bcc"),
    )
    # "opened", а не "sent": письмо только показано пользователю в обычном
    # окне «Новое письмо», отправку он подтверждает сам кнопкой «Отправить».
    return {"opened": "compose_email"}


def _handle_create_event(controller, args) -> dict:
    subject = _text(args.get("subject"), "subject")
    if not subject:
        raise ValueError("subject обязателен: название встречи")
    controller.ipc_create_event(
        summary=subject,
        start=parse_iso_datetime(args.get("start"), "start"),
        duration_minutes=_positive_int(args.get("duration_minutes", 60), "duration_minutes"),
        participants=_email_list(args.get("participants"), "participants"),
        description=_text(args.get("description"), "description"),
        location=_text(args.get("location"), "location"),
    )
    return {"opened": "create_event"}


def _handle_update_event(controller, args) -> dict:
    uid = _text(args.get("uid"), "uid")
    if not uid:
        raise ValueError("uid обязателен: идентификатор встречи")
    # Меняем ТОЛЬКО те поля, которые реально пришли: отсутствие ключа и
    # пустая строка — разные вещи (второе = «стереть описание»).
    changes: dict = {}
    if "subject" in args:
        changes["summary"] = _text(args.get("subject"), "subject")
    if "start" in args:
        changes["start"] = parse_iso_datetime(args.get("start"), "start")
    if "duration_minutes" in args:
        changes["duration_minutes"] = _positive_int(args.get("duration_minutes"), "duration_minutes")
    if "participants" in args:
        changes["participants"] = _email_list(args.get("participants"), "participants")
    if "description" in args:
        changes["description"] = _text(args.get("description"), "description")
    if "location" in args:
        changes["location"] = _text(args.get("location"), "location")
    controller.ipc_update_event(uid, **changes)
    return {"opened": "update_event", "uid": uid}


def _handle_find_events(controller, args) -> dict:
    subject = _text(args.get("subject"), "subject")
    date_arg = args.get("date")
    # Дата опущена — берём сегодняшнюю (по местному времени этой машины):
    # так реализуется "Если дата опущена - берём текущую" из голосовой
    # команды "перенеси встречу <тема> на ...", не заставляя каждый вызов
    # с голосовой стороны самому подставлять сегодняшнее число.
    on_date = parse_iso_date(date_arg, "date") if date_arg else datetime.now().astimezone().date()
    events = controller.ipc_find_events(subject=subject or None, on_date=on_date)
    return {"events": events}


def _handle_cancel_event(controller, args) -> dict:
    uid = _text(args.get("uid"), "uid")
    if not uid:
        raise ValueError("uid обязателен: идентификатор встречи")
    controller.ipc_cancel_event(uid)
    return {"opened": "cancel_event", "uid": uid}


def _handle_apply_mail_rules(controller, args) -> dict:
    folder = _text(args.get("folder"), "folder") or None
    return controller.ipc_apply_mail_rules(folder)


def _handle_list_mail_rules(controller, _args) -> dict:
    return {"rules": controller.ipc_list_mail_rules()}


# --------------------------------------------------------------------------
# Пошаговая форма встречи — голосовое заполнение «на открытом окне»
#
# create_event/update_event открывают диалог с готовыми полями и на этом
# заканчиваются. Голосовому помощнику нужен другой режим: открыть окно
# встречи и дальше по одной фразе менять поля («Тема планёрка», «Дата
# пятнадцатое сентября», «Участники Шилкин, Пономарёв»), пока человек
# смотрит на окно, а в конце сказать «Сохранить» или «Отменить». Для этого:
#
#   event_form_open   — открыть форму (новую или по uid своей встречи),
#                       поля из args применяются сразу;
#   event_form_set    — изменить поля уже открытой формы;
#   event_form_state  — что сейчас в полях;
#   event_form_save   — нажать «Сохранить» (те же проверки и та же рассылка
#                       приглашений, что у кнопки в окне);
#   event_form_cancel — нажать «Отмена»;
#   find_contacts     — контакты адресной книги по фамилии/имени, как их
#                       слышно в речи (с падежным окончанием).
#
# Поля event_form_open/event_form_set: subject, date (YYYY-MM-DD, время
# остаётся), time (HH:MM, дата остаётся), start (ISO — и дата, и время),
# duration_minutes, recurrence (none/daily/weekly/monthly/yearly или
# FREQ=…), participants (список адресов — заменить), add_participants
# (добавить), location, description, all_day.
# --------------------------------------------------------------------------

_RECURRENCE_ALIASES: dict[str, str | None] = {
    "none": None,
    "no": None,
    "daily": "FREQ=DAILY",
    "weekly": "FREQ=WEEKLY",
    "monthly": "FREQ=MONTHLY",
    "yearly": "FREQ=YEARLY",
}


def _recurrence(value, field: str = "recurrence") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} должен быть строкой: none/daily/weekly/monthly/yearly или FREQ=...")
    text = value.strip()
    if not text:
        return None
    if text.upper().startswith("FREQ="):
        return text.upper()
    key = text.lower()
    if key not in _RECURRENCE_ALIASES:
        raise ValueError(f"{field}: неизвестное повторение {value!r} (none/daily/weekly/monthly/yearly)")
    return _RECURRENCE_ALIASES[key]


def parse_iso_time(value, field: str = "time") -> tuple[int, int]:
    """"HH:MM" → (часы, минуты)."""
    text = _text(value, field)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"{field}: ожидается время HH:MM, получено {value!r}")
    return int(m.group(1)), int(m.group(2))


def _form_changes(args: dict) -> dict:
    """Поля формы из args — только те, что реально переданы (как в
    update_event: отсутствие ключа и пустая строка — разные вещи)."""
    changes: dict = {}
    if "subject" in args:
        changes["summary"] = _text(args.get("subject"), "subject")
    if "start" in args:
        changes["start"] = parse_iso_datetime(args.get("start"), "start")
    if "date" in args:
        changes["date"] = parse_iso_date(args.get("date"), "date")
    if "time" in args:
        changes["time"] = parse_iso_time(args.get("time"), "time")
    if "duration_minutes" in args:
        changes["duration_minutes"] = _positive_int(args.get("duration_minutes"), "duration_minutes")
    if "recurrence" in args:
        changes["recurrence"] = _recurrence(args.get("recurrence"))
    if "participants" in args:
        changes["participants"] = _email_list(args.get("participants"), "participants")
    if "add_participants" in args:
        changes["add_participants"] = _email_list(args.get("add_participants"), "add_participants")
    if "location" in args:
        changes["location"] = _text(args.get("location"), "location")
    if "description" in args:
        changes["description"] = _text(args.get("description"), "description")
    if "all_day" in args:
        changes["all_day"] = bool(args.get("all_day"))
    return changes


def _handle_event_form_open(controller, args) -> dict:
    uid = _text(args.get("uid"), "uid") or None
    form = controller.ipc_event_form_open(uid=uid, **_form_changes(args))
    return {"opened": "event_form", "form": form}


def _handle_event_form_set(controller, args) -> dict:
    changes = _form_changes(args)
    if not changes:
        raise ValueError("нечего менять: в args нет ни одного поля формы")
    return {"form": controller.ipc_event_form_set(**changes)}


def _handle_event_form_state(controller, _args) -> dict:
    return {"form": controller.ipc_event_form_state()}


def _handle_event_form_save(controller, _args) -> dict:
    # "saving", а не "saved": форма только что нажала «Сохранить» — само
    # сохранение и рассылка приглашений произойдут, когда диалог закроется
    # (после того, как этот ответ уже ушёл клиенту).
    return {"saving": True, "form": controller.ipc_event_form_save()}


def _handle_event_form_cancel(controller, _args) -> dict:
    controller.ipc_event_form_cancel()
    return {"cancelled": True}


# Сравнение фамилий "на слух": в речи фамилия почти всегда в косвенном
# падеже («пригласить Шилкина, Пономарёва»), в адресной книге — в
# именительном («Шилкин»). Сравниваем основы: без ё/е-различия и без
# хвоста из гласных/й/ь (до трёх букв), а основы считаем совпавшими, если
# равны или одна начинается с другой (не короче 4 букв — чтобы «Ли» не
# совпадало со всеми).
_STEM_TAIL = set("аеёийоуыьюя")


def _stem(word: str) -> str:
    stem = word.lower().replace("ё", "е")
    stripped = 0
    while len(stem) > 3 and stripped < 3 and stem[-1] in _STEM_TAIL:
        stem = stem[:-1]
        stripped += 1
    return stem


def _word_matches(query_word: str, name_word: str) -> bool:
    q, w = _stem(query_word), _stem(name_word)
    if not q or not w:
        return False
    if q == w:
        return True
    return min(len(q), len(w)) >= 4 and (q.startswith(w) or w.startswith(q))


def _contact_words(contact) -> list[str]:
    words = re.split(r"[\s,;()]+", contact.display_name or "")
    for email in contact.emails:
        words.extend(re.split(r"[._\-+]+", email.split("@", 1)[0]))
    return [w for w in words if w]


def match_contacts(contacts, query: str) -> list:
    """Контакты (с адресом), у которых КАЖДОЕ слово запроса совпало с
    каким-то словом имени или локальной части адреса — см. _stem."""
    query_words = [w for w in re.split(r"[\s,;]+", query) if w]
    if not query_words:
        return []
    result = []
    for contact in contacts:
        if not contact.emails:
            continue
        words = _contact_words(contact)
        if all(any(_word_matches(qw, nw) for nw in words) for qw in query_words):
            result.append(contact)
    return result


def _handle_find_contacts(controller, args) -> dict:
    query = _text(args.get("query"), "query")
    if not query:
        raise ValueError("query обязателен: фамилия или имя")
    matches = match_contacts(controller.ipc_contacts(), query)
    return {
        "contacts": [
            {"name": c.display_name, "email": c.emails[0], "emails": list(c.emails)} for c in matches
        ]
    }


_HANDLERS = {
    "ping": _handle_ping,
    "focus": _handle_focus,
    "compose_email": _handle_compose_email,
    "create_event": _handle_create_event,
    "update_event": _handle_update_event,
    "find_events": _handle_find_events,
    "cancel_event": _handle_cancel_event,
    "event_form_open": _handle_event_form_open,
    "event_form_set": _handle_event_form_set,
    "event_form_state": _handle_event_form_state,
    "event_form_save": _handle_event_form_save,
    "event_form_cancel": _handle_event_form_cancel,
    "find_contacts": _handle_find_contacts,
    "apply_mail_rules": _handle_apply_mail_rules,
    "list_mail_rules": _handle_list_mail_rules,
}

ACTIONS = tuple(sorted(_HANDLERS))


def handle_request(controller, request) -> dict:
    """Разбор одного уже распарсенного запроса.

    Отдельно от транспорта нарочно: так команды тестируются с подставным
    контроллером, без сокетов и без единого виджета. Исключение любой
    команды превращается в {"ok": false, ...}, а не роняет сервер — один
    кривой запрос не должен обрывать канал и тем более валить приложение.
    """
    if not isinstance(request, dict):
        return {"ok": False, "error": "запрос должен быть JSON-объектом"}
    action = request.get("action")
    if not isinstance(action, str) or not action:
        return {"ok": False, "error": "не указано поле action"}
    args = request.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {"ok": False, "error": "args должен быть JSON-объектом"}
    handler = _HANDLERS.get(action)
    if handler is None:
        return {"ok": False, "error": f"unknown action: {action}"}
    try:
        result = handler(controller, args)
    except Exception as exc:  # noqa: BLE001 — любую ошибку команды отдаём клиенту текстом
        return {"ok": False, "error": str(exc) or exc.__class__.__name__}
    response = {"ok": True}
    if result:
        response.update(result)
    return response


# --------------------------------------------------------------------------
# Транспорт
# --------------------------------------------------------------------------


def _is_alive(name: str) -> bool:
    """Отвечает ли кто-то живой по этому имени сокета."""
    probe = QLocalSocket()
    probe.connectToServer(name)
    connected = probe.waitForConnected(PROBE_TIMEOUT_MS)
    probe.abort()
    return connected


def focus_running_instance(name: str | None = None) -> bool:
    """Один экземпляр программы: если по имени сокета уже отвечает живой
    redmail, попросить его поднять окно и вернуть True — тогда новый
    процесс не стартует (жалоба: "смог открыть 2 окна почты — это
    неправильно"). Ответа не ждём дольше PROBE_TIMEOUT_MS."""
    name = name or server_name()
    probe = QLocalSocket()
    probe.connectToServer(name)
    if not probe.waitForConnected(PROBE_TIMEOUT_MS):
        probe.abort()
        return False
    probe.write(json.dumps({"action": "focus", "args": {}}).encode("utf-8") + b"\n")
    probe.waitForBytesWritten(PROBE_TIMEOUT_MS)
    probe.waitForReadyRead(PROBE_TIMEOUT_MS)
    # Штатное отключение, а не abort(): abort может сбросить ещё не
    # прочитанные сервером данные вместе с соединением.
    probe.disconnectFromServer()
    if probe.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        probe.waitForDisconnected(PROBE_TIMEOUT_MS)
    return True


class IpcServer(QObject):
    """QLocalServer + построчный JSON поверх него.

    controller — объект с методами ipc_* (в приложении это MainWindow, в
    тестах — заглушка).
    """

    def __init__(self, controller, parent: QObject | None = None, name: str | None = None):
        super().__init__(parent)
        self._controller = controller
        self._name = name or server_name()
        self._server = QLocalServer(self)
        # Только текущий пользователь ОС. По умолчанию (NoOptions) на Linux
        # права unix-сокета зависят от umask и запросто оказываются
        # доступными на запись всей группе/всем — то есть соседний
        # пользователь машины мог бы дёргать чужой почтовый клиент.
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_new_connection)
        self._buffers: dict[QLocalSocket, bytearray] = {}
        self._listening = False

    # -- свойства ---------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    def full_server_name(self) -> str:
        return self._server.fullServerName()

    def is_listening(self) -> bool:
        return self._listening

    # -- жизненный цикл ---------------------------------------------------

    def start(self) -> bool:
        """Занять имя сокета. False — канал не поднялся (приложение при этом
        обязано работать дальше как ни в чём не бывало).

        Имя может быть занято двумя очень разными способами:

        * рядом УЖЕ РАБОТАЕТ живой экземпляр redmail — тогда отбирать канал
          нельзя: команды пошли бы не в то окно, и человек не понял бы,
          почему «письмо открылось не там». Проверяем это пробным
          подключением ДО listen() — на Windows именованный канал допускает
          несколько экземпляров одного имени, и одна лишь ошибка listen()
          такой случай не поймала бы вовсе;
        * от прошлого УПАВШЕГО запуска остался файл unix-сокета, который
          никто не слушает (сам он на Linux не исчезает). Вот его и убирает
          removeServer() — иначе listen() навсегда упирался бы в
          AddressInUseError.
        """
        if self._listening:
            return True
        if _is_alive(self._name):
            return False
        QLocalServer.removeServer(self._name)
        if not self._server.listen(self._name):
            return False
        self._listening = True
        self._write_endpoint_file()
        return True

    def stop(self) -> None:
        for socket in list(self._buffers):
            # Сигналы отцепляем ДО abort(): иначе abort() тут же испустит
            # disconnected, наш обработчик закажет deleteLater(), а сам
            # QLocalServer (родитель этих сокетов) уже закрывается — и
            # отложенное удаление придётся на объект, которого нет.
            for signal in (socket.readyRead, socket.disconnected):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            self._forget(socket)
            socket.abort()
        self._buffers.clear()
        if self._listening:
            self._server.close()
            # close() на Linux файл сокета убирает, но повтор ничего не
            # стоит и страхует от платформенных отличий.
            QLocalServer.removeServer(self._name)
            self._listening = False
        self._remove_endpoint_file()

    # -- файл с адресом ---------------------------------------------------

    def _write_endpoint_file(self) -> None:
        try:
            path = endpoint_file_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "protocol": PROTOCOL,
                        "name": self._name,
                        "full_server_name": self.full_server_name(),
                        "pid": os.getpid(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # адрес можно собрать и по имени — не повод не поднимать канал

    def _remove_endpoint_file(self) -> None:
        try:
            endpoint_file_path().unlink(missing_ok=True)
        except OSError:
            pass

    # -- соединения -------------------------------------------------------

    def _on_new_connection(self) -> None:
        while True:
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))

    def _on_disconnected(self, socket: QLocalSocket) -> None:
        self._forget(socket)
        socket.deleteLater()

    def _forget(self, socket: QLocalSocket) -> None:
        self._buffers.pop(socket, None)

    def _on_ready_read(self, socket: QLocalSocket) -> None:
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer += bytes(socket.readAll().data())
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                if len(buffer) > MAX_LINE_BYTES:
                    self._respond(socket, {"ok": False, "error": "запрос слишком длинный"})
                    del buffer[:]
                    socket.disconnectFromServer()
                return
            line = bytes(buffer[:newline])
            del buffer[: newline + 1]
            if not line.strip():
                continue
            self._respond(socket, self._handle_line(line))

    def _handle_line(self, line: bytes) -> dict:
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "error": "запрос не в UTF-8"}
        try:
            request = json.loads(text)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"некорректный JSON: {exc}"}
        return handle_request(self._controller, request)

    def _respond(self, socket: QLocalSocket, payload: dict) -> None:
        if socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            return
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        socket.write(data)
        socket.flush()
