"""Доверие к корпоративному центру сертификации для HTTPS.

Зачем: IMAP/SMTP идут через ssl и берут доверенные корни из системного
хранилища (OpenSSL), поэтому корпоративный ЦС, добавленный через
update-ca-trust, для них «свой». А CalDAV, Exchange (EWS) и подписка на
календарь работают через requests, который по умолчанию проверяет
сертификат по собственному набору certifi — корпоративного ЦС там нет, и
подключение падает с «CERTIFICATE_VERIFY_FAILED: unable to get local
issuer certificate» (жалобы: «календарь не цепляется», «даёт ошибку при
попытке подключить Exchange»).

Решение: при старте указываем requests на СИСТЕМНОЕ хранилище через
переменные окружения REQUESTS_CA_BUNDLE/SSL_CERT_FILE — их читают и
requests, и exchangelib, и caldav. Если пользователь уже задал их сам или
системного набора нет, ничего не меняем.
"""
from __future__ import annotations

import os
from pathlib import Path

from redmail.applog import get_logger

_log = get_logger("tls")

#: Системные наборы доверенных корней: RED OS/RHEL, затем Debian/Ubuntu.
SYSTEM_CA_BUNDLES = (
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/certs/ca-certificates.crt",
)

_ENV_VARS = ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")


def system_ca_bundle(candidates: tuple[str, ...] | None = None) -> str | None:
    # Список берём в момент вызова, а не в момент объявления функции:
    # иначе его нельзя подменить (тесты, будущая настройка).
    for candidate in candidates if candidates is not None else SYSTEM_CA_BUNDLES:
        path = Path(candidate)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return str(path)
        except OSError:
            continue
    return None


def use_system_ca_bundle(environ: dict | None = None, candidates: tuple[str, ...] | None = None) -> str | None:
    """Прописать системный набор корней в окружение процесса. Возвращает
    путь к набору или None, если менять нечего."""
    env = os.environ if environ is None else environ
    if any(env.get(name) for name in _ENV_VARS):
        return None  # пользователь/администратор уже задал свой набор — не трогаем
    bundle = system_ca_bundle(candidates)
    if bundle is None:
        return None
    for name in _ENV_VARS:
        env[name] = bundle
    _log.info("HTTPS: доверенные корни из системного хранилища %s", bundle)
    return bundle


def use_ca_file(ca_file: str, environ: dict | None = None) -> str | None:
    """Явно указанный файл корневого сертификата организации (Параметры →
    Общие). Корпоративный ЦС часто стоит только в браузере/в Windows, а на
    рабочей станции с RED OS его в системном хранилище нет — тогда CalDAV и
    Exchange падают с CERTIFICATE_VERIFY_FAILED, хотя браузер тот же адрес
    открывает. Возвращает путь или None, если файла нет."""
    env = os.environ if environ is None else environ
    path = Path(ca_file) if ca_file else None
    if path is None or not path.is_file():
        if ca_file:
            _log.warning("HTTPS: файл сертификата %s не найден — остаюсь на системном хранилище", ca_file)
        return None
    for name in _ENV_VARS:
        env[name] = str(path)
    _log.info("HTTPS: доверенные корни из файла %s", path)
    return str(path)


def apply_trust(ca_file: str = "", environ: dict | None = None) -> str | None:
    """Общая точка: сначала файл из настроек, иначе системное хранилище."""
    return use_ca_file(ca_file, environ) or use_system_ca_bundle(environ)
