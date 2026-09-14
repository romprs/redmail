from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from redmail import tls_trust


def test_system_ca_bundle_picks_first_existing_file(tmp_path: Path) -> None:
    missing = tmp_path / "нет.crt"
    empty = tmp_path / "пустой.crt"
    empty.write_text("", encoding="utf-8")
    real = tmp_path / "ca-bundle.crt"
    real.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    assert tls_trust.system_ca_bundle((str(missing), str(empty), str(real))) == str(real)
    assert tls_trust.system_ca_bundle((str(missing),)) is None


def test_use_system_ca_bundle_sets_env_for_requests(tmp_path: Path) -> None:
    # CalDAV/EWS ходят через requests и по умолчанию не знают корпоративный
    # ЦС (жалоба: "календарь не цепляется", CERTIFICATE_VERIFY_FAILED).
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    env: dict[str, str] = {}
    assert tls_trust.use_system_ca_bundle(env, (str(bundle),)) == str(bundle)
    assert env["REQUESTS_CA_BUNDLE"] == str(bundle)
    assert env["SSL_CERT_FILE"] == str(bundle)
    assert env["CURL_CA_BUNDLE"] == str(bundle)


def test_use_system_ca_bundle_keeps_explicit_choice(tmp_path: Path) -> None:
    bundle = tmp_path / "ca-bundle.crt"
    bundle.write_text("x", encoding="utf-8")
    env = {"REQUESTS_CA_BUNDLE": "/своё/хранилище.pem"}
    assert tls_trust.use_system_ca_bundle(env, (str(bundle),)) is None
    assert env == {"REQUESTS_CA_BUNDLE": "/своё/хранилище.pem"}


def test_use_system_ca_bundle_without_system_store_changes_nothing(tmp_path: Path) -> None:
    env: dict[str, str] = {}
    assert tls_trust.use_system_ca_bundle(env, (str(tmp_path / "нет.crt"),)) is None
    assert env == {}


def test_apply_trust_prefers_configured_file(tmp_path: Path) -> None:
    # Файл из настроек важнее системного хранилища: корпоративный корень
    # часто стоит только в браузере, а в системе RED OS его нет.
    corporate = tmp_path / "corp-root.pem"
    corporate.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    system = tmp_path / "ca-bundle.crt"
    system.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    env: dict[str, str] = {}
    assert tls_trust.apply_trust(str(corporate), env) == str(corporate)
    assert env["REQUESTS_CA_BUNDLE"] == str(corporate)


def test_apply_trust_falls_back_to_system_when_file_missing(tmp_path: Path) -> None:
    system = tmp_path / "ca-bundle.crt"
    system.write_text("x", encoding="utf-8")
    env: dict[str, str] = {}
    with patch.object(tls_trust, "SYSTEM_CA_BUNDLES", (str(system),)):
        assert tls_trust.apply_trust(str(tmp_path / "нет.pem"), env) == str(system)
    assert env["SSL_CERT_FILE"] == str(system)
