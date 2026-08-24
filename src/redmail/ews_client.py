from __future__ import annotations

import zlib
from dataclasses import dataclass
from email import message_from_bytes

from exchangelib import BASIC, DELEGATE, GSSAPI, NTLM
from exchangelib import Account as ExchangeAccount
from exchangelib import Configuration, Credentials, FileAttachment, Folder, Mailbox
from exchangelib.items import Message as EwsMessage

from redmail.imap_client import (
    UNKNOWN_MARKER,
    FolderInfo,
    MessageContent,
    MessageSummary,
    extract_content,
)
from redmail.smtp_client import OutgoingMessage

_AUTH_TYPE_MAP = {"basic": BASIC, "ntlm": NTLM, "kerberos": GSSAPI}
_IMPORTANCE_MAP = {"High": "high", "Normal": "normal", "Low": "low"}

# EWS не поддерживает произвольные keyword-флаги как IMAP — ближайший
# аналог для цветного маркера здесь это "категории" Outlook (произвольный
# список текстовых меток, тоже показываются цветными плашками в самом
# Outlook, если завести категорию с тем же именем и назначить ей цвет).
MARKER_CATEGORIES: dict[str, str] = {
    "red": "RedMail Red",
    "orange": "RedMail Orange",
    "yellow": "RedMail Yellow",
    "green": "RedMail Green",
    "blue": "RedMail Blue",
    "purple": "RedMail Purple",
}
_COLOR_BY_CATEGORY = {v: k for k, v in MARKER_CATEGORIES.items()}


@dataclass
class EwsAccount:
    email: str
    username: str = ""  # для NTLM: DOMAIN\пользователь; для basic: обычно = email; для kerberos не используется
    password: str = ""
    server: str = ""  # явный адрес EWS-сервера; пусто = автообнаружение (autodiscover)
    auth_type: str = "basic"  # "basic" | "ntlm" | "kerberos"

    def __post_init__(self) -> None:
        if not self.username:
            # Для Kerberos/SSO логин не нужен для входа (билет и так
            # привязан к пользователю ОС), но username всё равно
            # используется как часть ключа кэша сообщений
            # (CachedMailbox._account_key = f"{host}:{username}") — без
            # этого два разных Kerberos-пользователя на одном домене
            # (одинаковый host, пустой username у обоих) получили бы один
            # и тот же ключ и делили бы кэш чужих писем.
            self.username = self.email

    @property
    def host(self) -> str:
        # Используется только как ключ кэша/учётной записи (см.
        # CachedMailbox._account_key) — реальный адрес сервера, если он не
        # указан явно, приложение узнаёт через autodiscover само.
        return self.server or self.email.rsplit("@", 1)[-1]


class EwsConnectionError(Exception):
    """Не удалось подключиться или авторизоваться на сервере Exchange."""


class EwsSession:
    """То же назначение, что у ImapSession (src/redmail/imap_client.py), но
    поверх Exchange Web Services (библиотека exchangelib) — для серверов
    Exchange/Office 365, где IMAP отключён политикой безопасности или где
    нужен вход через SSO (Kerberos) без ввода пароля в приложении.

    Публичный интерфейс намеренно повторяет ImapSession метод-в-метод
    (list_folders/create_folder/rename_folder/trash_folder/
    folder_message_count/fetch_summaries/search_uids/
    fetch_message_content/fetch_message_raw/set_read/set_marker/
    move_messages/delete_messages/close) — CachedMailbox (mailbox.py)
    работает с "session" по duck typing, не зная, IMAP это или EWS, и
    поэтому весь остальной UI (дерево папок, таблица писем, кэш) подходит
    без изменений для обоих протоколов.
    """

    def __init__(self, account: EwsAccount):
        self.account = account
        auth_type = _AUTH_TYPE_MAP.get(account.auth_type, BASIC)
        credentials = None
        if account.auth_type != "kerberos":
            # Kerberos/SSO: билет берётся из окружения ОС (см. GSSAPI в
            # exchangelib) — пароль в приложении не нужен и не хранится.
            credentials = Credentials(account.username or account.email, account.password)
        config_kwargs: dict = {"auth_type": auth_type, "credentials": credentials}
        if account.server:
            config_kwargs["server"] = account.server
        try:
            config = Configuration(**config_kwargs)
            self._account = ExchangeAccount(
                primary_smtp_address=account.email,
                config=config,
                autodiscover=not bool(account.server),
                access_type=DELEGATE,
            )
        except Exception as exc:
            raise EwsConnectionError(str(exc)) from exc

        self._folders_by_path: dict[str, Folder] = {}
        self._selected_folder: str | None = None
        self._selected_folder_obj: Folder | None = None
        # Наш синтетический int-uid (по образцу IMAP UID) -> (id, changekey)
        # реального письма в EWS. EWS адресует письма строковой парой
        # id+changekey, а не одним числом — этот кэш позволяет остальному
        # приложению (кэш сообщений, таблица писем, отметка "прочитано",
        # цветной маркер, перемещение/удаление) работать с тем же типом
        # uid, что и для IMAP, без переделки всего приложения под другой
        # тип идентификатора письма. crc32 — детерминированный, не зависит
        # от PYTHONHASHSEED (в отличие от встроенного hash()).
        self._id_map: dict[int, tuple[str, str]] = {}

    def close(self) -> None:
        pass  # exchangelib сам управляет пулом HTTP-соединений, отдельно закрывать нечего

    def __enter__(self) -> "EwsSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def list_folders(self) -> list[FolderInfo]:
        self._folders_by_path = {}
        result: list[FolderInfo] = []

        def walk(folder: Folder, prefix: str) -> None:
            for child in folder.children:
                path = f"{prefix}/{child.name}" if prefix else child.name
                self._folders_by_path[path] = child
                result.append(FolderInfo(name=path, delimiter="/"))
                walk(child, path)

        try:
            walk(self._account.msg_folder_root, "")
        except Exception as exc:
            raise EwsConnectionError(str(exc)) from exc
        return result

    def create_folder(self, name: str) -> None:
        parent_path, _, short_name = name.rpartition("/")
        parent = self._folders_by_path.get(parent_path) if parent_path else self._account.msg_folder_root
        if parent is None:
            raise ValueError(f"Родительская папка не найдена: {parent_path}")
        Folder(parent=parent, name=short_name).save()

    def rename_folder(self, old_name: str, new_name: str) -> None:
        folder = self._folders_by_path.get(old_name)
        if folder is None:
            raise ValueError(f"Папка не найдена: {old_name}")
        _, _, short_new_name = new_name.rpartition("/")
        folder.name = short_new_name
        folder.save(update_fields=["name"])

    def trash_folder(self) -> str | None:
        return self._special_folder_path(lambda account: account.trash)

    def sent_folder(self) -> str | None:
        # На практике не используется для реальной отправки — EWS сам
        # сохраняет копию в "Отправленные" при Message.send(save_copy=True)
        # (умолчание exchangelib), в отличие от IMAP/SMTP, где сервер не
        # всегда это делает сам. Но метод даёт единый интерфейс с
        # ImapSession — остальному коду (например, определению, что
        # текущая папка — Черновики) не нужно знать про протокол.
        return self._special_folder_path(lambda account: account.sent)

    def drafts_folder(self) -> str | None:
        return self._special_folder_path(lambda account: account.drafts)

    def _special_folder_path(self, get_special_folder) -> str | None:
        try:
            special = get_special_folder(self._account)
        except Exception:
            return None
        for path, folder in self._folders_by_path.items():
            if folder.id == special.id:
                return path
        return None

    def _folder(self, path: str) -> Folder:
        folder = self._folders_by_path.get(path)
        if folder is None:
            raise ValueError(f"Папка не найдена: {path}")
        return folder

    def folder_message_count(self, folder: str) -> int:
        folder_obj = self._folder(folder)
        self._selected_folder = folder
        self._selected_folder_obj = folder_obj
        return folder_obj.total_count

    def fetch_summaries(self, limit: int = 50) -> list[MessageSummary]:
        if self._selected_folder_obj is None:
            return []
        items = self._selected_folder_obj.all().order_by("-datetime_received")[:limit]
        return [self._to_summary(item) for item in items]

    def fetch_folder_summaries(self, folder: str, limit: int = 50) -> list[MessageSummary]:
        self.folder_message_count(folder)
        return self.fetch_summaries(limit)

    def search_uids(self, folder: str, *, before=None) -> list[int]:
        folder_obj = self._folder(folder)
        items = folder_obj.all()
        if before is not None:
            items = items.filter(datetime_received__lt=before)
        return [self._register(item) for item in items]

    def fetch_message_content(self, folder: str, uid: int) -> MessageContent:
        return extract_content(message_from_bytes(self.fetch_message_raw(folder, uid)))

    def fetch_message_raw(self, folder: str, uid: int) -> bytes:
        item = self._get_item(uid)
        return item.mime_content

    def set_read(self, folder: str, uid: int, read: bool) -> None:
        item = self._get_item(uid)
        item.is_read = read
        item.save(update_fields=["is_read"])

    def set_marker(self, folder: str, uid: int, color: str | None, *, previous_color=UNKNOWN_MARKER) -> None:
        if previous_color is not UNKNOWN_MARKER and previous_color == color:
            return
        item = self._get_item(uid)
        categories = [c for c in (item.categories or []) if c not in MARKER_CATEGORIES.values()]
        if color is not None:
            categories.append(MARKER_CATEGORIES[color])
        item.categories = categories
        item.save(update_fields=["categories"])

    def move_messages(self, folder: str, uids: list[int], target_folder: str) -> None:
        if not uids:
            return
        target = self._folder(target_folder)
        for uid in uids:
            self._get_item(uid).move(target)

    def delete_messages(self, folder: str, uids: list[int]) -> None:
        if not uids:
            return
        for uid in uids:
            self._get_item(uid).delete()

    def _register(self, item) -> int:
        # & 0x7FFFFFFF — держим uid в диапазоне обычного 32-битного
        # положительного int, как настоящие IMAP UID (некоторый код в
        # приложении сортирует/сравнивает uid как числа).
        uid = zlib.crc32(item.id.encode("utf-8")) & 0x7FFFFFFF
        self._id_map[uid] = (item.id, item.changekey)
        return uid

    def _get_item(self, uid: int):
        entry = self._id_map.get(uid)
        if entry is None:
            raise ValueError("Письмо не найдено в этой сессии — обновите папку и повторите")
        ews_id, changekey = entry
        (item,) = self._account.fetch(ids=[(ews_id, changekey)])
        return item

    def _to_summary(self, item) -> MessageSummary:
        uid = self._register(item)
        sender_name = ""
        sender_email = ""
        if item.sender is not None:
            sender_name = item.sender.name or ""
            sender_email = item.sender.email_address or ""
        marker_color = None
        for category in item.categories or []:
            if category in _COLOR_BY_CATEGORY:
                marker_color = _COLOR_BY_CATEGORY[category]
                break
        return MessageSummary(
            uid=uid,
            subject=item.subject or "",
            sender=sender_name or sender_email or "(неизвестно)",
            sender_email=sender_email,
            date=item.datetime_received.strftime("%Y-%m-%d %H:%M") if item.datetime_received else "",
            message_id=item.message_id or "",
            has_attachments=bool(item.has_attachments),
            marker_color=marker_color,
            importance=_IMPORTANCE_MAP.get(item.importance, "normal"),
            is_read=bool(item.is_read),
        )


def send_message(session: EwsSession, message: OutgoingMessage) -> None:
    """Отправляет письмо через тот же EWS-аккаунт, что и чтение — для
    Exchange не нужен отдельный SMTP-релей и его отдельные логин/пароль,
    в отличие от IMAP-аккаунтов (см. smtp_client.send_message).

    ВАЖНО: .ics-вложения (приглашения на встречу) уходят как обычные
    файловые вложения — EWS не даёт пронести MIME-параметр method=REQUEST
    у вложения, как это делает smtp_client через email.message. Получатель
    сможет открыть .ics вручную, но встроенная кнопка "Принять/Отклонить"
    в почтовом клиенте получателя может не появиться, в отличие от
    отправки через SMTP. Полноценные встречи Exchange (CalendarItem) —
    отдельная, более крупная задача, здесь не реализована.
    """
    ews_message = EwsMessage(
        account=session._account,
        subject=message.subject,
        body=message.body,
        to_recipients=[Mailbox(email_address=addr) for addr in message.to],
        cc_recipients=[Mailbox(email_address=addr) for addr in message.cc] if message.cc else None,
        bcc_recipients=[Mailbox(email_address=addr) for addr in message.bcc] if message.bcc else None,
    )
    for attachment in message.attachments:
        ews_message.attach(
            FileAttachment(name=attachment.filename, content=attachment.payload, content_type=attachment.content_type)
        )
    ews_message.send()
