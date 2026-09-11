from __future__ import annotations

from pathlib import Path

from redmail import profile


def test_default_profile_dir_and_db_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(profile, "app_dir", lambda: tmp_path)
    profile.set_profile_dir_override(None)
    try:
        assert profile.profile_dir() == tmp_path / "profile"
        assert profile.mail_db_path() == tmp_path / "profile" / "mail.sqlite3"
        assert profile.calendar_db_path().name == "calendar.rmcal"
        assert profile.contacts_db_path().name == "contacts.rmcontacts"
        assert profile.archives_dir() == tmp_path / "profile" / "archives"
    finally:
        profile.set_profile_dir_override(None)


def test_profile_dir_comes_from_settings(monkeypatch, tmp_path: Path) -> None:
    # Путь к профилю настраивается (инцидент с диском на тестовой машине):
    # ключ profile_dir в settings.json.
    monkeypatch.setattr(profile, "app_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text('{"profile_dir": "%s"}' % (tmp_path / "elsewhere").as_posix(), encoding="utf-8")
    profile.set_profile_dir_override(None)
    assert profile.profile_dir() == tmp_path / "elsewhere"


def test_ensure_profile_moves_legacy_databases_once(monkeypatch, tmp_path: Path) -> None:
    # Старые базы из ~/.config/redmail переезжают в профиль — данные
    # пользователя (кэш, календарь, контакты) не теряются при переходе.
    monkeypatch.setattr(profile, "app_dir", lambda: tmp_path)
    profile.set_profile_dir_override(None)
    (tmp_path / "cache.sqlite3").write_bytes(b"mail")
    (tmp_path / "calendar.rmcal").write_bytes(b"cal")
    (tmp_path / "contacts.rmcontacts").write_bytes(b"contacts")

    target = profile.ensure_profile()

    assert target == tmp_path / "profile"
    assert (target / "mail.sqlite3").read_bytes() == b"mail"
    assert (target / "calendar.rmcal").read_bytes() == b"cal"
    assert (target / "contacts.rmcontacts").read_bytes() == b"contacts"
    assert not (tmp_path / "cache.sqlite3").exists()

    # Повторный запуск: новый «старый» файл не затирает уже живущую в профиле базу.
    (tmp_path / "cache.sqlite3").write_bytes(b"stale")
    profile.ensure_profile()
    assert (target / "mail.sqlite3").read_bytes() == b"mail"
