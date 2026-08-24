from __future__ import annotations

import base64
import sys
import types
from unittest.mock import MagicMock

import pytest


class _FakeGSSError(Exception):
    pass


class _FakeSecurityContext:
    """Имитирует gssapi.SecurityContext: два вызова step() до готовности
    контекста (реалистично для одностороннего Kerberos-обмена), затем
    unwrap()/wrap() работают как identity + префикс — этого достаточно,
    чтобы проверить логику GssapiSaslContext, не поднимая настоящий KDC."""

    def __init__(self, name, usage) -> None:
        self.name = name
        self.usage = usage
        self.complete = False
        self._steps = 0

    def step(self, token):
        self._steps += 1
        if self._steps >= 2:
            self.complete = True
            return None
        return f"client-token-{self._steps}".encode()

    def unwrap(self, message: bytes):
        return types.SimpleNamespace(message=message)

    def wrap(self, message: bytes, confidential: bool):
        return types.SimpleNamespace(message=b"wrapped:" + message)


@pytest.fixture
def fake_gssapi_sasl(monkeypatch):
    fake_gssapi = types.ModuleType("gssapi")
    fake_gssapi.Name = lambda name, name_type: name
    fake_gssapi.NameType = types.SimpleNamespace(hostbased_service="hostbased_service")
    fake_gssapi.SecurityContext = _FakeSecurityContext
    fake_gssapi.exceptions = types.SimpleNamespace(GSSError=_FakeGSSError)
    monkeypatch.setitem(sys.modules, "gssapi", fake_gssapi)
    sys.modules.pop("redmail.gssapi_sasl", None)
    import redmail.gssapi_sasl as module

    yield module

    # "import redmail.gssapi_sasl" выше — не только ключ в sys.modules, но
    # и побочный эффект: Python сам выставляет атрибут gssapi_sasl на
    # пакете redmail. `from redmail import gssapi_sasl` в другом месте
    # сперва пробует getattr(redmail, "gssapi_sasl") и только потом
    # sys.modules — если не подчистить именно атрибут, следующий тест,
    # подменяющий redmail.gssapi_sasl только через sys.modules (как в
    # test_imap_client.py/test_smtp_client.py), получит в обход подмены
    # этот — уже собранный на фейковом gssapi текущего теста — модуль.
    import redmail as _redmail_pkg

    if "gssapi_sasl" in vars(_redmail_pkg):
        delattr(_redmail_pkg, "gssapi_sasl")
    sys.modules.pop("redmail.gssapi_sasl", None)


def test_context_negotiates_then_wraps_security_layer_response(fake_gssapi_sasl) -> None:
    ctx = fake_gssapi_sasl.GssapiSaslContext(service="imap", host="mail.corp.local", authzid="ivan")

    first = ctx.step(b"")
    assert first == b"client-token-1"

    second = ctx.step(b"server-challenge")
    assert second == b""  # gssapi.SecurityContext.step() вернул None -> контекст готов

    final = ctx.step(b"\x01\x00\x00\x10")
    assert final.startswith(b"wrapped:")
    assert b"ivan" in final
    assert final[len(b"wrapped:")] == 1  # байт 0 отклика — маска "без слоя защиты"


def test_context_returns_empty_after_security_layer_already_negotiated(fake_gssapi_sasl) -> None:
    ctx = fake_gssapi_sasl.GssapiSaslContext(service="imap", host="mail.corp.local")
    ctx.step(b"")
    ctx.step(b"server-challenge")
    ctx.step(b"\x01\x00\x00\x10")
    assert ctx.step(b"anything-else") == b""


def test_context_wraps_unwrap_errors_as_gssapi_sasl_error(fake_gssapi_sasl, monkeypatch) -> None:
    ctx = fake_gssapi_sasl.GssapiSaslContext(service="imap", host="mail.corp.local")
    ctx.step(b"")
    ctx.step(b"server-challenge")

    def _raise(*_args, **_kwargs):
        raise _FakeGSSError("билет просрочен")

    ctx._ctx.unwrap = _raise
    with pytest.raises(fake_gssapi_sasl.GssapiSaslError):
        ctx.step(b"\x01\x00\x00\x10")


def test_imap_sasl_login_authenticates_with_gssapi_mechanism(fake_gssapi_sasl) -> None:
    client = MagicMock()
    fake_gssapi_sasl.imap_sasl_login(client, "mail.corp.local", "ivan")

    args, _kwargs = client.sasl_login.call_args
    assert args[0] == "GSSAPI"
    assert callable(args[1])


def test_smtp_sasl_login_completes_full_auth_exchange(fake_gssapi_sasl) -> None:
    client = MagicMock()
    client.docmd.side_effect = [
        (334, base64.b64encode(b"")),
        (334, base64.b64encode(b"server-tok")),
        (334, base64.b64encode(b"\x01\x00\x00\x10")),
        (235, b"Authentication successful"),
    ]

    fake_gssapi_sasl.smtp_sasl_login(client, "smtp.corp.local", "ivan")

    assert client.docmd.call_count == 4
    client.docmd.assert_any_call("AUTH", "GSSAPI")


def test_smtp_sasl_login_raises_on_rejected_final_code(fake_gssapi_sasl) -> None:
    import smtplib

    client = MagicMock()
    client.docmd.side_effect = [
        (334, base64.b64encode(b"")),
        (334, base64.b64encode(b"server-tok")),
        (334, base64.b64encode(b"\x01\x00\x00\x10")),
        (535, b"Authentication failed"),
    ]

    with pytest.raises(smtplib.SMTPAuthenticationError):
        fake_gssapi_sasl.smtp_sasl_login(client, "smtp.corp.local", "ivan")
