"""Приглашения к общим папкам и календарям — разбор писем.

Зачем: и VK, и Exchange сообщают о выданном доступе обычным письмом, а
подключать общую папку или календарь приходилось руками (жалоба: «папки
не появляются и календари тоже — только ручное подключение; может можно
как-то автоматом цеплять?»). В письме есть всё, что нужно для
сопоставления: кто поделился и чем.

- VK, папки: «Вам предоставили доступ к папкам» со ссылкой
  account.<узел>/share/mailbox?share-folders=confirm&…&owner=<адрес>.
- VK, календарь: «… предлагает вам совместный доступ к календарю» со
  ссылкой на веб-календарь; владелец — отправитель письма.
- Exchange: служебное письмо класса IPM.Sharing, в нём вложение
  application/x-sharing-metadata-xml с адресом ящика владельца и типом
  папки (календарь, контакты, почта).

Принять доступ по-прежнему можно только там, где это решает сервер (у VK
— в веб-интерфейсе по ссылке из письма). Здесь мы лишь узнаём, кто и чем
поделился, чтобы подключить это самим, не заставляя человека вводить
адреса вручную.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree

from redmail.applog import get_logger

_log = get_logger("invites")

KIND_MAIL = "mail"
KIND_CALENDAR = "calendar"
KIND_CONTACTS = "contacts"

SOURCE_VK = "vk"
SOURCE_EXCHANGE = "exchange"


@dataclass(frozen=True)
class SharingInvite:
    """Кто и чем поделился."""

    owner_email: str
    owner_name: str
    kind: str
    source: str
    #: Ссылка на подтверждение (VK) или адрес ресурса, если он известен.
    url: str = ""
    #: Идентификатор папки у Exchange — по нему папка открывается напрямую.
    folder_id: str = ""

    @property
    def title(self) -> str:
        who = self.owner_name or self.owner_email
        return {
            KIND_MAIL: f"Папки: {who}",
            KIND_CALENDAR: f"Календарь: {who}",
            KIND_CONTACTS: f"Контакты: {who}",
        }.get(self.kind, who)


_VK_FOLDERS_RE = re.compile(r"https?://[^\s<>\"']*?/share/mailbox\?[^\s<>\"']+", re.IGNORECASE)
#: Ссылка без схемы — VK кладёт её в текстовую часть письма именно так.
_VK_FOLDERS_BARE_RE = re.compile(r"(?<![\w/])[\w.-]+/share/mailbox\?[^\s<>\"']+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

_CALENDAR_SUBJECT_MARKERS = ("совместный доступ к календар", "доступ к календар", "календар")
_FOLDERS_SUBJECT_MARKERS = ("доступ к папк", "общие папки")


def _owner_from_share_link(link: str) -> tuple[str, str]:
    """(адрес владельца, ссылка подтверждения) из ссылки VK."""
    url = link if link.lower().startswith(("http://", "https://")) else f"https://{link}"
    query = parse_qs(urlparse(url).query)
    owner = ""
    for value in query.get("owner", []):
        candidate = unquote(value).strip()
        if _EMAIL_RE.fullmatch(candidate):
            owner = candidate.lower()
            break
    confirm = "confirm" in " ".join(query.get("share-folders", []))
    return owner, (url if confirm else "")


def parse_vk_message(subject: str, sender_name: str, sender_email: str, body: str) -> list[SharingInvite]:
    """Приглашения VK из текста письма."""
    invites: list[SharingInvite] = []
    text = body or ""
    for match in list(_VK_FOLDERS_RE.finditer(text)) + list(_VK_FOLDERS_BARE_RE.finditer(text)):
        owner, confirm_url = _owner_from_share_link(match.group(0))
        if not confirm_url:
            continue  # ссылка «отказаться» — не приглашение
        owner = owner or (sender_email or "").strip().lower()
        if not owner:
            continue
        invite = SharingInvite(
            owner_email=owner, owner_name=sender_name.strip(), kind=KIND_MAIL,
            source=SOURCE_VK, url=confirm_url,
        )
        if invite not in invites:
            invites.append(invite)
    lowered = (subject or "").casefold()
    if any(marker in lowered for marker in _CALENDAR_SUBJECT_MARKERS) and (sender_email or "").strip():
        invites.append(SharingInvite(
            owner_email=sender_email.strip().lower(), owner_name=sender_name.strip(),
            kind=KIND_CALENDAR, source=SOURCE_VK,
        ))
    return invites


_SHARING_NS = {
    "x": "http://schemas.microsoft.com/sharing/2008",
}

#: Что за папка — по типу данных в приглашении Exchange.
_EXCHANGE_KINDS = {
    "calendar": KIND_CALENDAR,
    "contacts": KIND_CONTACTS,
    "mail": KIND_MAIL,
    "inbox": KIND_MAIL,
}


def parse_exchange_metadata(xml_bytes: bytes, sender_name: str = "", sender_email: str = "") -> list[SharingInvite]:
    """Приглашение Exchange из вложения sharing_metadata.xml."""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        _log.info("Приглашение Exchange: разметка не разобрана: %s", exc)
        return []
    invites: list[SharingInvite] = []
    for provider in root.iter():
        tag = provider.tag.rsplit("}", 1)[-1]
        if tag != "Provider":
            continue
        values = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in provider}
        owner = ""
        for key in ("MailboxId", "SmtpAddress", "InitiatorSmtpAddress"):
            candidate = values.get(key, "")
            found = _EMAIL_RE.search(candidate)
            if found:
                owner = found.group(0).lower()
                break
        owner = owner or (sender_email or "").strip().lower()
        if not owner:
            continue
        data_type = (values.get("FolderType") or values.get("DataType") or "").casefold()
        kind = next((value for marker, value in _EXCHANGE_KINDS.items() if marker in data_type), KIND_CALENDAR)
        invite = SharingInvite(
            owner_email=owner, owner_name=sender_name.strip(), kind=kind,
            source=SOURCE_EXCHANGE, folder_id=values.get("FolderId", ""),
        )
        if invite not in invites:
            invites.append(invite)
    return invites


def invites_from_message(
    *, subject: str, sender_name: str, sender_email: str, body: str = "",
    sharing_metadata: bytes = b"", item_class: str = "",
) -> list[SharingInvite]:
    """Все приглашения из одного письма — и VK, и Exchange."""
    invites: list[SharingInvite] = []
    if sharing_metadata or item_class.lower().startswith("ipm.sharing"):
        invites += parse_exchange_metadata(sharing_metadata, sender_name, sender_email)
    invites += parse_vk_message(subject, sender_name, sender_email, body)
    # Одно письмо — одно приглашение на каждый ресурс: у служебного письма
    # Exchange тема тоже похожа на «доступ к календарю», и разбор по теме
    # добавлял второй такой же (на стенде приглашение Орлова распозналось
    # дважды). Разметка точнее темы, поэтому она и остаётся.
    unique: list[SharingInvite] = []
    seen: set[tuple[str, str]] = set()
    for invite in sorted(invites, key=lambda i: 0 if i.source == SOURCE_EXCHANGE else 1):
        key = (invite.owner_email, invite.kind)
        if key in seen:
            continue
        seen.add(key)
        unique.append(invite)
    return unique


def looks_like_invite(subject: str, item_class: str = "") -> bool:
    """Стоит ли вообще разбирать это письмо — чтобы не скачивать тела всех
    писем ящика ради поиска приглашений."""
    if item_class.lower().startswith("ipm.sharing"):
        return True
    lowered = (subject or "").casefold()
    markers = _FOLDERS_SUBJECT_MARKERS + _CALENDAR_SUBJECT_MARKERS + ("sharing invitation", "shared with you")
    return any(marker in lowered for marker in markers)


def invites_from_raw(raw: bytes, *, subject: str = "", sender_name: str = "", sender_email: str = "") -> list[SharingInvite]:
    """Приглашения из готового письма (RFC 822). Текст берём и из
    текстовой, и из html-части: VK кладёт ссылку в обе."""
    import email
    from email import policy

    try:
        message = email.message_from_bytes(raw, policy=policy.default)
    except Exception as exc:
        _log.info("Приглашение: письмо не разобрано: %s", exc)
        return []
    subject = subject or str(message.get("Subject") or "")
    if not sender_email:
        sender = str(message.get("From") or "")
        found = _EMAIL_RE.search(sender)
        sender_email = found.group(0) if found else ""
        sender_name = sender_name or sender.split("<", 1)[0].strip().strip('"')
    body_parts: list[str] = []
    metadata = b""
    for part in message.walk():
        content_type = (part.get_content_type() or "").lower()
        if content_type in ("text/plain", "text/html"):
            try:
                body_parts.append(part.get_content())
            except Exception:
                continue
        elif "sharing-metadata" in content_type or (part.get_filename() or "").lower().endswith("sharing_metadata.xml"):
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                payload = None
            if payload:
                metadata = payload
    return invites_from_message(
        subject=subject, sender_name=sender_name, sender_email=sender_email,
        body="\n".join(body_parts), sharing_metadata=metadata,
        item_class="IPM.Sharing" if metadata else "",
    )


def scan_mailbox(mailbox, folders: list[str], *, limit: int = 300) -> list[SharingInvite]:
    """Приглашения из писем ящика. Тела скачиваем только у писем, чья тема
    похожа на приглашение — иначе пришлось бы тянуть весь ящик."""
    found: list[SharingInvite] = []
    for folder in folders:
        try:
            summaries = mailbox.folder_summaries(folder, limit=limit)
        except Exception as exc:
            _log.info("Приглашения: папка %s не прочитана: %s", folder, exc)
            continue
        for summary in summaries:
            subject = getattr(summary, "subject", "") or ""
            if not looks_like_invite(subject):
                continue
            try:
                raw = mailbox.message_raw(folder, summary.uid, background=True)
            except Exception as exc:
                _log.info("Приглашения: письмо «%s» не получено: %s", subject, exc)
                continue
            for invite in invites_from_raw(
                raw, subject=subject,
                sender_name=getattr(summary, "sender", "") or "",
                sender_email=getattr(summary, "sender_email", "") or "",
            ):
                if invite not in found:
                    found.append(invite)
    if found:
        _log.info("Приглашения найдены: %s", "; ".join(invite.title for invite in found))
    return found
