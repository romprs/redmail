from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from redmail.config_store import (
    MailRule,
    load_account,
    load_accounts,
    load_caldav_url,
    load_font_scale,
    load_mail_columns_state,
    load_mail_rules,
    load_open_archives,
    load_pane_orientation,
    load_poll_interval_minutes,
    load_window_geometry,
    save_account,
    save_accounts,
    save_caldav_url,
    save_font_scale,
    save_mail_columns_state,
    save_mail_rules,
    save_open_archives,
    save_pane_orientation,
    save_poll_interval_minutes,
    save_window_geometry,
)
from redmail.config_store import load_ews_accounts, save_ews_accounts
from redmail.ews_client import EwsAccount
from redmail.imap_client import Account
from redmail.smtp_client import SmtpAccount


def _fake_keyring(store: dict[str, str]):
    set_patch = patch(
        "redmail.config_store.keyring.set_password",
        side_effect=lambda _service, user, pw: store.__setitem__(user, pw),
    )
    get_patch = patch(
        "redmail.config_store.keyring.get_password",
        side_effect=lambda _service, user: store.get(user),
    )
    return set_patch, get_patch


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    config_file = tmp_path / "account.json"
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)

    with patch("redmail.config_store._config_path", return_value=config_file), set_patch, get_patch:
        account = Account(host="imap.example.com", username="ivan@example.com", password="secret", port=993, use_ssl=True)
        smtp = SmtpAccount(host="smtp.example.com", username="ivan@example.com", password="secret", port=587, use_ssl=False)
        save_account(account, smtp)

        loaded = load_account()

    assert loaded is not None
    loaded_account, loaded_smtp = loaded
    assert loaded_account.host == "imap.example.com"
    assert loaded_account.username == "ivan@example.com"
    assert loaded_account.password == "secret"
    assert loaded_account.port == 993
    assert loaded_smtp is not None
    assert loaded_smtp.host == "smtp.example.com"
    assert loaded_smtp.port == 587
    assert loaded_smtp.password == "secret"


def test_save_without_smtp_round_trips_to_none(tmp_path: Path) -> None:
    config_file = tmp_path / "account.json"
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)

    with patch("redmail.config_store._config_path", return_value=config_file), set_patch, get_patch:
        account = Account(host="imap.example.com", username="ivan", password="secret")
        save_account(account, None)
        loaded = load_account()

    assert loaded is not None
    _account, smtp = loaded
    assert smtp is None


def test_save_and_load_account_kerberos_does_not_store_password(tmp_path: Path) -> None:
    # SSO для IMAP/SMTP-аккаунта: пароль не отправляется в keyring вовсе —
    # аутентификация идёт по Kerberos-билету из ОС (см. gssapi_sasl.py).
    config_file = tmp_path / "account.json"
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)

    with patch("redmail.config_store._config_path", return_value=config_file), set_patch, get_patch:
        account = Account(host="imap.corp.local", username="ivan", password="", auth_type="kerberos")
        smtp = SmtpAccount(host="smtp.corp.local", username="ivan", password="", auth_type="kerberos")
        save_account(account, smtp)
        loaded = load_account()

    assert store == {}  # пароль не попал в keyring
    assert loaded is not None
    loaded_account, loaded_smtp = loaded
    assert loaded_account.auth_type == "kerberos"
    assert loaded_account.password == ""
    assert loaded_smtp is not None
    assert loaded_smtp.auth_type == "kerberos"


def test_load_account_returns_none_when_no_config_file(tmp_path: Path) -> None:
    config_file = tmp_path / "account.json"
    with patch("redmail.config_store._config_path", return_value=config_file):
        assert load_account() is None


def test_load_account_returns_none_when_password_missing_from_keyring(tmp_path: Path) -> None:
    # Файл настроек сохранился, но пароль в системном хранилище недоступен
    # (например, отозван или это другая машина) — не должны падать, а
    # попросить войти заново.
    config_file = tmp_path / "account.json"
    with patch("redmail.config_store._config_path", return_value=config_file), \
         patch("redmail.config_store.keyring.set_password"), \
         patch("redmail.config_store.keyring.get_password", return_value=None):
        account = Account(host="imap.example.com", username="ivan", password="secret")
        save_account(account, None)
        assert load_account() is None


def test_poll_interval_defaults_to_five_minutes(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_poll_interval_minutes() == 5


def test_poll_interval_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_poll_interval_minutes(15)
        assert load_poll_interval_minutes() == 15


def test_poll_interval_survives_corrupt_file(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("not json", encoding="utf-8")
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_poll_interval_minutes() == 5


def test_pane_orientation_defaults_to_vertical(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_pane_orientation() == "vertical"


def test_pane_orientation_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_pane_orientation("horizontal")
        assert load_pane_orientation() == "horizontal"


def test_pane_orientation_rejects_unknown_value(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"pane_orientation": "diagonal"}', encoding="utf-8")
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_pane_orientation() == "vertical"


def test_font_scale_defaults_to_one(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_font_scale() == 1.0


def test_font_scale_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_font_scale(1.25)
        assert load_font_scale() == 1.25


def test_font_scale_out_of_range_falls_back_to_default(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"font_scale": 9.0}', encoding="utf-8")
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_font_scale() == 1.0


def test_saving_one_setting_does_not_clobber_another(tmp_path: Path) -> None:
    # Раньше save_* полностью перезаписывал файл настроек — второй вызов
    # стирал бы всё, что сохранил первый.
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_poll_interval_minutes(20)
        save_pane_orientation("horizontal")
        save_font_scale(1.5)

        assert load_poll_interval_minutes() == 20
        assert load_pane_orientation() == "horizontal"
        assert load_font_scale() == 1.5


def test_window_geometry_defaults_to_none(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_window_geometry() is None


def test_window_geometry_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_window_geometry(b"\x00\x01\xff\xfe binary geometry blob")
        assert load_window_geometry() == b"\x00\x01\xff\xfe binary geometry blob"


def test_mail_columns_state_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_mail_columns_state() is None
        save_mail_columns_state(b"\x00columns\xff")
        assert load_mail_columns_state() == b"\x00columns\xff"


def test_caldav_url_defaults_to_empty(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_caldav_url() == ""


def test_caldav_url_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_caldav_url("https://calendar.example.corp/caldav/")
        assert load_caldav_url() == "https://calendar.example.corp/caldav/"


def test_load_accounts_returns_empty_when_nothing_saved(tmp_path: Path) -> None:
    accounts_file = tmp_path / "accounts.json"
    config_file = tmp_path / "account.json"
    with patch("redmail.config_store._accounts_path", return_value=accounts_file), \
         patch("redmail.config_store._config_path", return_value=config_file):
        assert load_accounts() == []


def test_save_and_load_accounts_round_trip(tmp_path: Path) -> None:
    accounts_file = tmp_path / "accounts.json"
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)

    a1 = Account(host="imap1.example.com", username="ivan@example.com", password="p1", port=993, use_ssl=True)
    smtp1 = SmtpAccount(host="smtp1.example.com", username="ivan@example.com", password="p1", port=587, use_ssl=False)
    a2 = Account(host="imap2.example.com", username="other@example.com", password="p2", port=993, use_ssl=True)

    with patch("redmail.config_store._accounts_path", return_value=accounts_file), set_patch, get_patch:
        save_accounts([(a1, smtp1), (a2, None)])
        loaded = load_accounts()

    assert len(loaded) == 2
    assert loaded[0][0].username == "ivan@example.com"
    assert loaded[0][0].password == "p1"
    assert loaded[0][1].host == "smtp1.example.com"
    assert loaded[1][0].username == "other@example.com"
    assert loaded[1][1] is None


def test_save_and_load_accounts_kerberos_entry_round_trip(tmp_path: Path) -> None:
    accounts_file = tmp_path / "accounts.json"
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)

    a1 = Account(host="imap1.example.com", username="ivan@example.com", password="p1")
    a2 = Account(host="imap2.corp.local", username="sso-user", password="", auth_type="kerberos")

    with patch("redmail.config_store._accounts_path", return_value=accounts_file), set_patch, get_patch:
        save_accounts([(a1, None), (a2, None)])
        loaded = load_accounts()

    assert "sso-user" not in store  # для kerberos пароль не сохраняется
    assert len(loaded) == 2
    assert loaded[1][0].username == "sso-user"
    assert loaded[1][0].auth_type == "kerberos"
    assert loaded[1][0].password == ""


def test_load_accounts_migrates_from_old_single_account_file(tmp_path: Path) -> None:
    accounts_file = tmp_path / "accounts.json"  # не существует
    config_file = tmp_path / "account.json"
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)

    with patch("redmail.config_store._accounts_path", return_value=accounts_file), \
         patch("redmail.config_store._config_path", return_value=config_file), set_patch, get_patch:
        old_account = Account(host="imap.example.com", username="legacy@example.com", password="secret")
        save_account(old_account, None)
        migrated = load_accounts()

    assert len(migrated) == 1
    assert migrated[0][0].username == "legacy@example.com"


def test_load_accounts_skips_entry_with_missing_password(tmp_path: Path) -> None:
    accounts_file = tmp_path / "accounts.json"
    store: dict[str, str] = {}
    set_patch, get_patch = _fake_keyring(store)

    a1 = Account(host="imap1.example.com", username="has-password@example.com", password="p1")
    a2 = Account(host="imap2.example.com", username="no-password@example.com", password="p2")

    with patch("redmail.config_store._accounts_path", return_value=accounts_file), set_patch, get_patch:
        save_accounts([(a1, None), (a2, None)])
        del store["no-password@example.com"]  # как будто пароль отозвали/другая машина
        loaded = load_accounts()

    assert [a.username for a, _ in loaded] == ["has-password@example.com"]


def test_save_and_load_ews_accounts_round_trip(tmp_path: Path) -> None:
    ews_file = tmp_path / "ews_accounts.json"
    store: dict[str, str] = {}
    set_patch = patch(
        "redmail.config_store.keyring.set_password",
        side_effect=lambda _service, user, pw: store.__setitem__(user, pw),
    )
    get_patch = patch(
        "redmail.config_store.keyring.get_password",
        side_effect=lambda _service, user: store.get(user),
    )

    a1 = EwsAccount(email="ivan@corp.example", username="ivan@corp.example", password="p1", auth_type="basic")
    a2 = EwsAccount(email="kerberos-user@corp.example", auth_type="kerberos")

    with patch("redmail.config_store._ews_accounts_path", return_value=ews_file), set_patch, get_patch:
        save_ews_accounts([a1, a2])
        loaded = load_ews_accounts()

    assert len(loaded) == 2
    assert loaded[0].email == "ivan@corp.example"
    assert loaded[0].password == "p1"
    assert loaded[1].email == "kerberos-user@corp.example"
    assert loaded[1].auth_type == "kerberos"
    assert loaded[1].password == ""  # для kerberos пароль не хранится вовсе


def test_load_ews_accounts_skips_entry_with_missing_password(tmp_path: Path) -> None:
    ews_file = tmp_path / "ews_accounts.json"
    store: dict[str, str] = {}
    set_patch = patch(
        "redmail.config_store.keyring.set_password",
        side_effect=lambda _service, user, pw: store.__setitem__(user, pw),
    )
    get_patch = patch(
        "redmail.config_store.keyring.get_password",
        side_effect=lambda _service, user: store.get(user),
    )

    a1 = EwsAccount(email="has-password@corp.example", username="a", password="p1", auth_type="basic")
    a2 = EwsAccount(email="no-password@corp.example", username="b", password="p2", auth_type="basic")

    with patch("redmail.config_store._ews_accounts_path", return_value=ews_file), set_patch, get_patch:
        save_ews_accounts([a1, a2])
        del store["no-password@corp.example"]
        loaded = load_ews_accounts()

    assert [a.email for a in loaded] == ["has-password@corp.example"]


def test_mail_rules_default_to_empty(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_mail_rules() == []


def test_mail_rules_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    rules = [
        MailRule(field="from", contains="ozon.ru", target_folder="Реклама"),
        MailRule(field="subject", contains="счёт", target_folder="Бухгалтерия"),
    ]
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_mail_rules(rules)
        assert load_mail_rules() == rules


def test_mail_rules_skips_corrupt_entries(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        '{"mail_rules": [{"field": "from", "contains": "x"}, {"field": "subject", "contains": "y", "target_folder": "Z"}]}',
        encoding="utf-8",
    )
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        rules = load_mail_rules()
    assert rules == [MailRule(field="subject", contains="y", target_folder="Z")]


def test_open_archives_defaults_to_empty(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_open_archives() == []


def test_open_archives_round_trip(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        save_open_archives(["C:/a.rmarchive", "C:/b.rmarchive"])
        assert load_open_archives() == ["C:/a.rmarchive", "C:/b.rmarchive"]


def test_open_archives_survives_corrupt_or_wrong_shaped_value(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"open_archives": "not-a-list"}', encoding="utf-8")
    with patch("redmail.config_store._settings_path", return_value=settings_file):
        assert load_open_archives() == []
