from __future__ import annotations

from redmail import contact_store
from redmail.ui.main_window import (
    _contact_candidates,
    _format_recipient_candidate,
    _normalize_subject,
    _parse_recipient_list,
)


def test_normalize_subject_strips_single_prefix() -> None:
    assert _normalize_subject("Re: Вопрос по счёту") == "Вопрос по счёту"


def test_normalize_subject_strips_repeated_prefixes() -> None:
    assert _normalize_subject("Re: Fwd: Re: Вопрос по счёту") == "Вопрос по счёту"


def test_normalize_subject_strips_russian_prefixes_case_insensitive() -> None:
    assert _normalize_subject("ОТВЕТ: Вопрос") == "Вопрос"
    assert _normalize_subject("Пересыл: Вопрос") == "Вопрос"


def test_normalize_subject_no_prefix_unchanged() -> None:
    assert _normalize_subject("Вопрос по счёту") == "Вопрос по счёту"


def test_format_recipient_candidate_plain_name() -> None:
    assert _format_recipient_candidate("Иван Иванов", "ivan@example.com") == "Иван Иванов <ivan@example.com>"


def test_format_recipient_candidate_quotes_name_with_comma() -> None:
    # Частый формат "Фамилия, Имя" — без кавычек запятая внутри имени
    # ломала бы разбор списка адресов по запятой.
    assert _format_recipient_candidate("Иванов, Иван", "ivan@example.com") == '"Иванов, Иван" <ivan@example.com>'


def test_format_recipient_candidate_no_name_falls_back_to_email() -> None:
    assert _format_recipient_candidate("", "ivan@example.com") == "ivan@example.com"


def test_format_recipient_candidate_does_not_rfc2047_encode() -> None:
    # email.utils.formataddr() кодирует не-ASCII имя в =?utf-8?...?=,
    # что годится для реального заголовка письма, но не для текстового
    # поля интерфейса, где пользователь должен видеть своё же имя как
    # есть, а не закодированную кашу.
    result = _format_recipient_candidate("Иван Иванов", "ivan@example.com")
    assert "=?" not in result
    assert "Иван Иванов" in result


def test_contact_candidates_uses_quoted_names() -> None:
    contacts = [contact_store.Contact(display_name="Иванов, Иван", emails=["ivan@example.com"])]
    assert _contact_candidates(contacts) == ['"Иванов, Иван" <ivan@example.com>']


def test_parse_recipient_list_plain_emails() -> None:
    assert _parse_recipient_list("a@example.com, b@example.com") == ["a@example.com", "b@example.com"]


def test_parse_recipient_list_with_names() -> None:
    assert _parse_recipient_list("Иван Иванов <a@example.com>, b@example.com") == ["a@example.com", "b@example.com"]


def test_parse_recipient_list_respects_quoted_comma_in_name() -> None:
    # Раньше naive text.split(",") резал бы "Иванов, Иван" пополам, и
    # второй адрес в списке не распознавался бы без ручного добавления
    # запятой (реальная жалоба пользователя).
    text = '"Иванов, Иван" <ivan@example.com>, Петров Пётр <petr@example.com>'
    assert _parse_recipient_list(text) == ["ivan@example.com", "petr@example.com"]


def test_parse_recipient_list_empty() -> None:
    assert _parse_recipient_list("") == []
    assert _parse_recipient_list("   ") == []
