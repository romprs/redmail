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


def system_ca_bundle(candidates: tuple[str, ...] = SYSTEM_CA_BUNDLES) -> str | None:
    for candidate in candidates:
        path = Path(candidate)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return str(path)
        except OSError:
            continue
    return None


def use_system_ca_bundle(environ: dict | None = None, candidates: tuple[str, ...] = SYSTEM_CA_BUNDLES) -> str | None:
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
