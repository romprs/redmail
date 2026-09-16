from __future__ import annotations

import json
import mailbox
import zipfile
from email import message_from_bytes
from email.policy import default as default_policy
from pathlib import Path
from unittest.mock import patch

import pytest

from redmail import archive_store, cache_store, cli, mail_export, profile, profile_transfer
from redmail.imap_client import Attachment, MessageContent, MessageSummary

ACCOUNT = "mail.example.com:ivanov@example.com"


@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    """Отдельный каталог настроек: и на Linux (XDG), и на Windows (APPDATA)."""
    config_root = tmp_path / "home" / ".config"
    config_root.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setenv("APPDATA", str(config_root))
    profile.set_profile_dir_override(None)
    with patch("redmail.branding.brand_dirs", return_value=[tmp_path / "etc-brands", config_root / "redmail" / "brands"]):
        yield tmp_path
    profile.set_profile_dir_override(None)


def _summary(uid: int, subject: str, date: str = "2026-09-01 10:00") -> MessageSummary:
    return MessageSummary(
        uid=uid, subject=subject, sender="Петров", sender_email="petrov@example.com", date=date,
        message_id=f"<m{uid}@example.com>", to="ivanov@example.com",
    )


def _fill_mailbox() -> Path:
    cache_store.upsert_summaries(ACCOUNT, "INBOX", [_summary(1, "Отчёт"), _summary(2, "Без тела"), _summary(3, "В архиве")])
    cache_store.save_message_content(ACCOUNT, "INBOX", 1, MessageContent(
        text="Текст\nFrom here on\n", html="<p>Текст <img src=\"cid:logo\"></p>",
        attachments=[Attachment("отчёт.pdf", "application/pdf", b"%PDF-1.4")],
        inline_images={"logo": ("image/png", b"\x89PNG")},
        from_="Петров <petrov@example.com>", to="ivanov@example.com",
    ))
    archive = profile.archives_dir() / "2025.rmarchive"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive_store.create_archive(archive)
    raw = b"From: a@example.com\r\nSubject: Old\r\nDate: Mon, 1 Sep 2025 10:00:00 +0300\r\n\r\nold body\r\n"
    archive_uid = archive_store.append_raw_message(archive, "INBOX", raw)
    cache_store.mark_archived(ACCOUNT, "INBOX", 3, str(archive), archive_uid)
    return archive


def test_build_message_restores_parts_and_blocks_header_injection() -> None:
    raw = mail_export.build_message(
        subject="Тема\r\nBcc: evil@example.com", sender="Петров", sender_email="petrov@example.com",
        date="2026-09-01 10:00", message_id="abc@example.com", importance="high", body="Текст",
        html="<p>x</p>", attachments=[("a.txt", "text/plain", b"hello")],
        inline_images=[("logo", "image/png", b"\x89PNG")],
    )
    message = message_from_bytes(raw, policy=default_policy)
    assert message["Bcc"] is None
    assert message["Message-ID"] == "<abc@example.com>"
    assert message["X-Priority"] == "1"
    assert message["Date"]
    assert message.get_body(("plain",)).get_content().strip() == "Текст"
    assert [part.get_filename() for part in message.iter_attachments()] == ["a.txt"]
    assert any(part["Content-ID"] == "<logo>" for part in message.walk())


def test_export_mail_mbox_includes_cache_and_archives_without_duplicates(home: Path) -> None:
    _fill_mailbox()
    result = mail_export.export_mail(home / "out", mail_export.FORMAT_MBOX)
    assert (result.messages, result.headers_only, result.archives) == (3, 1, 1)
    account_dir = home / "out" / "ivanov@example.com (mail.example.com)"
    box = mailbox.mbox(str(account_dir / "INBOX.mbox"))
    subjects = sorted(str(message["Subject"]) for message in box)
    box.close()
    assert len(subjects) == 2  # письмо из архива не повторяется
    archived = mailbox.mbox(str(home / "out" / mail_export.ARCHIVES_DIR_NAME / "2025" / "INBOX.mbox"))
    assert [message["Subject"] for message in archived] == ["Old"]
    archived.close()
    text = (account_dir / "INBOX.mbox").read_bytes()
    assert b"\n>From here on" in text  # строка From в теле экранирована


def test_export_mail_eml_and_refuses_non_empty_target(home: Path) -> None:
    _fill_mailbox()
    result = mail_export.export_mail(home / "eml", mail_export.FORMAT_EML)
    files = sorted(path.name for path in (home / "eml").rglob("*.eml"))
    assert len(files) == result.messages == 3
    with pytest.raises(FileExistsError):
        mail_export.export_mail(home / "eml", mail_export.FORMAT_EML)


def test_export_mail_can_be_stopped(home: Path) -> None:
    _fill_mailbox()
    with pytest.raises(mail_export.ExportCancelled):
        mail_export.export_mail(home / "stop", stop=lambda: True)


def test_profile_round_trip_moves_old_data_to_backup(home: Path) -> None:
    archive = _fill_mailbox()
    settings = profile._settings_path()
    settings.write_text(json.dumps({"theme": "dark", "profile_dir": str(profile.profile_dir()), "open_archives": [str(archive)]}), encoding="utf-8")
    dest = home / "transfer" / "ivanov"
    manifest = profile_transfer.export_profile(dest)
    exported = dest.with_name("ivanov.rmprofile")
    assert exported.is_file()
    assert "profile/mail.sqlite3" in manifest["files"]
    with zipfile.ZipFile(exported) as zipped:
        assert "profile_dir" not in json.loads(zipped.read("config/settings.json"))
        assert not any("secrets" in name for name in zipped.namelist())

    # «Новый компьютер»: другие данные, которые должны уйти в резервную копию.
    cache_store.upsert_summaries("other:account", "INBOX", [_summary(9, "Чужое")])
    profile_transfer.schedule_import(exported)
    assert profile_transfer.pending_import() == exported.resolve()
    backup = profile_transfer.apply_pending_import()
    assert profile_transfer.pending_import() is None
    assert (backup / "settings.json").is_file()

    new_archive = profile.archives_dir() / "2025.rmarchive"
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["theme"] == "dark" and data["open_archives"] == [str(new_archive)]
    assert cache_store.get_archive_ref(ACCOUNT, "INBOX", 3)[0] == str(new_archive)
    assert cache_store.list_folders("other:account") == []
    assert cache_store.get_message_content(ACCOUNT, "INBOX", 1).attachments[0].filename == "отчёт.pdf"


def test_read_manifest_rejects_foreign_and_tampered_files(home: Path) -> None:
    bad = home / "bad.rmprofile"
    with zipfile.ZipFile(bad, "w") as zipped:
        zipped.writestr("manifest.json", json.dumps({"format": 1, "files": {"../evil": {"size": 1, "sha256": "x"}}}))
        zipped.writestr("../evil", "x")
    with pytest.raises(profile_transfer.TransferError):
        profile_transfer.read_manifest(bad)

    tampered = home / "tampered.rmprofile"
    with zipfile.ZipFile(tampered, "w") as zipped:
        zipped.writestr("manifest.json", json.dumps({"format": 1, "files": {"config/settings.json": {"size": 2, "sha256": "0" * 64}}}))
        zipped.writestr("config/settings.json", "{}")
    profile._settings_path().parent.mkdir(parents=True, exist_ok=True)
    profile._settings_path().write_text('{"theme": "light"}', encoding="utf-8")
    with pytest.raises(profile_transfer.TransferError):
        profile_transfer.import_profile(tampered)
    assert json.loads(profile._settings_path().read_text(encoding="utf-8")) == {"theme": "light"}


def test_policy_follows_admin_brands(home: Path) -> None:
    assert profile_transfer.export_policy() == (profile_transfer.EXPORT_BY_USER, None)
    brands = home / "etc-brands"
    brands.mkdir()
    (brands / "gpp.json").write_text(json.dumps({"id": "gpp", "name": "ГПП"}), encoding="utf-8")
    mode, brand = profile_transfer.export_policy()
    assert mode == profile_transfer.EXPORT_BY_USER and brand.id == "gpp"
    (brands / "gpp.json").write_text(json.dumps({"id": "gpp", "name": "ГПП", "data_export": "admin"}), encoding="utf-8")
    assert profile_transfer.export_policy()[0] == profile_transfer.EXPORT_BY_ADMIN
    with patch("redmail.profile_transfer.is_admin", return_value=False):
        assert not profile_transfer.allowed_here()
        assert cli.run(["redmail", "--export-mail", str(home / "cli-out")]) == 2
        assert not (home / "cli-out").exists()
    with patch("redmail.profile_transfer.is_admin", return_value=True):
        assert profile_transfer.allowed_here()


def test_cli_exports_mail(home: Path, capsys) -> None:
    _fill_mailbox()
    assert cli.wants_cli(["redmail", "--export-mail", "x"]) and not cli.wants_cli(["redmail", "-style", "fusion"])
    assert cli.run(["redmail", "--export-mail", str(home / "cli"), "--format", "eml"]) == 0
    assert "3 писем" in capsys.readouterr().out


def test_build_message_survives_exchange_style_addresses() -> None:
    # Реальные адреса Exchange, на которых разборщик Python падает с AttributeError.
    raw = mail_export.build_message(
        subject="Тема", sender="", sender_email="", date="2026-09-01 10:00", message_id="",
        body="Текст", content_from="Иванов И.И.<ivanov@example.com>",
        content_to='"me@example.com" <me@example.com>,Петров П.П.<petrov@example.com>',
    )
    message = message_from_bytes(raw, policy=default_policy)
    assert "ivanov@example.com" in str(message["From"])
    assert "me@example.com" in str(message["To"]) and "petrov@example.com" in str(message["To"])
