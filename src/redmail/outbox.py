"""«Исходящие»: письма, которые ещё не ушли или не смогли уйти.

Раньше отправка шла в фоне без следа: окно письма закрывалось, об успехе
пять секунд говорила строка состояния, а при ошибке письмо пропадало
вместе с текстом (жалоба: «ушло или нет — гадаем, пока не появится в
Отправленных»). Теперь письмо до отправки кладётся сюда, после отправки
убирается, а при ошибке остаётся с текстом ошибки — его можно отправить
ещё раз или открыть для правки, в том числе после перезапуска.

Хранится в профиле: один JSON на письмо, вложения — base64. Не pickle:
файл читается при запуске, и подложенный в профиль файл не должен
исполнять код. Права — только владелец (в письме бывает что угодно).
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from redmail.applog import get_logger
from redmail.smtp_client import OutgoingAttachment, OutgoingMessage

_log = get_logger("outbox")

OUTBOX_DIR = "outbox"

SENDING = "sending"
FAILED = "failed"


@dataclass
class OutboxItem:
    id: str
    account_key: str
    message: OutgoingMessage
    status: str = SENDING
    error: str = ""
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    #: Для «Отправленных» и черновиков после отправки — как в _after_send.
    source_draft: tuple[str, int] | None = None
    #: Текст письма без колонтитула организации — для «Открыть для правки»:
    #: колонтитул добавляется при отправке, иначе он задвоился бы.
    edit_html: str | None = None
    edit_text: str = ""

    @property
    def title(self) -> str:
        return self.message.subject or "(без темы)"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _message_to_dict(message: OutgoingMessage) -> dict:
    return {
        "sender": message.sender,
        "to": list(message.to),
        "cc": list(message.cc),
        "bcc": list(message.bcc),
        "subject": message.subject,
        "body": message.body,
        "html_body": message.html_body,
        "in_reply_to": message.in_reply_to,
        "references": list(message.references),
        "attachments": [
            {
                "filename": a.filename,
                "content_type": a.content_type,
                "payload": _b64(a.payload),
                "params": dict(a.content_type_params),
            }
            for a in message.attachments
        ],
        "inline_images": {cid: [ctype, _b64(payload)] for cid, (ctype, payload) in message.inline_images.items()},
    }


def _str_list(value) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _message_from_dict(data: dict) -> OutgoingMessage:
    attachments = []
    for raw in data.get("attachments") or []:
        attachments.append(OutgoingAttachment(
            filename=str(raw.get("filename") or "attachment"),
            content_type=str(raw.get("content_type") or "application/octet-stream"),
            payload=base64.b64decode(raw.get("payload") or ""),
            content_type_params={str(k): str(v) for k, v in (raw.get("params") or {}).items()},
        ))
    inline = {}
    for cid, pair in (data.get("inline_images") or {}).items():
        if isinstance(pair, list) and len(pair) == 2:
            inline[str(cid)] = (str(pair[0]), base64.b64decode(pair[1] or ""))
    html = data.get("html_body")
    reply_to = data.get("in_reply_to")
    return OutgoingMessage(
        sender=str(data.get("sender") or ""),
        to=_str_list(data.get("to")),
        cc=_str_list(data.get("cc")),
        bcc=_str_list(data.get("bcc")),
        subject=str(data.get("subject") or ""),
        body=str(data.get("body") or ""),
        html_body=str(html) if isinstance(html, str) else None,
        in_reply_to=str(reply_to) if isinstance(reply_to, str) else None,
        references=_str_list(data.get("references")),
        attachments=attachments,
        inline_images=inline,
    )


class Outbox:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def _path(self, item_id: str) -> Path:
        # id создаём сами (uuid4 hex); чужое имя файла из каталога сюда не
        # попадает — list() берёт id из имени, проверяя его вид.
        return self._dir / f"{item_id}.json"

    def add(
        self, account_key: str, message: OutgoingMessage, *, source_draft=None,
        edit_html: str | None = None, edit_text: str = "",
    ) -> OutboxItem:
        item = OutboxItem(
            id=uuid.uuid4().hex, account_key=account_key, message=message, source_draft=source_draft,
            edit_html=edit_html, edit_text=edit_text,
        )
        self.save(item)
        return item

    def save(self, item: OutboxItem) -> None:
        data = {
            "account_key": item.account_key,
            "status": item.status,
            "error": item.error,
            "created": item.created,
            "source_draft": list(item.source_draft) if item.source_draft else None,
            "edit_html": item.edit_html,
            "edit_text": item.edit_text,
            "message": _message_to_dict(item.message),
        }
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self._dir, 0o700)
        except OSError:
            pass
        path = self._path(item.id)
        tmp = path.with_suffix(".tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as exc:
            _log.warning("Исходящие: письмо «%s» не сохранено: %s", item.title, exc)

    def mark_failed(self, item: OutboxItem, error: str) -> None:
        item.status = FAILED
        item.error = error
        self.save(item)

    def mark_sending(self, item: OutboxItem) -> None:
        item.status = SENDING
        item.error = ""
        self.save(item)

    def remove(self, item: OutboxItem) -> None:
        try:
            self._path(item.id).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            _log.warning("Исходящие: письмо «%s» не убрано: %s", item.title, exc)

    def items(self) -> list[OutboxItem]:
        result = []
        try:
            paths = sorted(self._dir.glob("*.json"))
        except OSError:
            return []
        for path in paths:
            item_id = path.stem
            if len(item_id) != 32 or not all(ch in "0123456789abcdef" for ch in item_id):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                draft = data.get("source_draft")
                item = OutboxItem(
                    id=item_id,
                    account_key=str(data.get("account_key") or ""),
                    message=_message_from_dict(data.get("message") or {}),
                    status=FAILED if data.get("status") == FAILED else SENDING,
                    error=str(data.get("error") or ""),
                    created=str(data.get("created") or ""),
                    source_draft=(str(draft[0]), int(draft[1])) if isinstance(draft, list) and len(draft) == 2 else None,
                    edit_html=data["edit_html"] if isinstance(data.get("edit_html"), str) else None,
                    edit_text=str(data.get("edit_text") or ""),
                )
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                _log.warning("Исходящие: %s не прочитан: %s", path.name, exc)
                continue
            result.append(item)
        return sorted(result, key=lambda item: item.created)

    def recover_interrupted(self) -> list[OutboxItem]:
        """После перезапуска «отправляется» значит «прервано»: программа
        закрылась посреди отправки. Ушло ли письмо — неизвестно, поэтому
        само не переотправляем, а показываем как ошибку с объяснением."""
        items = self.items()
        for item in items:
            if item.status == SENDING:
                self.mark_failed(item, "Отправка прервана: программа была закрыта. Проверьте «Отправленные» и при необходимости отправьте ещё раз.")
        return items
