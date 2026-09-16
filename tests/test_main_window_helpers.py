from __future__ import annotations

from redmail import contact_store
from redmail.imap_client import MessageSummary
from redmail.ui.main_window import (
    _contact_candidates,
    _exception_text,
    _recipients_tooltip,
    _format_recipient_candidate,
    _html_to_preview_text,
    _needs_another_bodies_round,
    _pick_calendar_account,
    _shared_domain_labels,
    _normalize_subject,
    _parse_recipient_list,
    _safe_attachment_filename,
    _thread_infos,
    _thread_subject_text,
)


def _summary(uid: int, date: str, subject: str) -> MessageSummary:
    return MessageSummary(uid=uid, subject=subject, sender="Ivan", sender_email="ivan@example.com", date=date, message_id=f"<{uid}@x>")


def test_error_text_adds_hint_for_known_server_answers() -> None:
    # Сырые ответы серверов, которые реально видел пользователь: пароль
    # приложения (VK Mail), корпоративный сертификат (CalDAV/EWS).
    text = _exception_text(Exception(b"[AUTHENTICATIONFAILED] NEOBHODIM parol prilozheniya / Application password is REQUIRED"))
    assert "пароль приложения" in text and "NEOBHODIM" in text
    cert = _exception_text(Exception("SSLError([SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate)"))
    assert "update-ca-trust" in cert
    assert _exception_text(Exception("что-то своё")) == "что-то своё"


def test_contact_candidates_person_single_address_and_group_reference() -> None:
    # Человек — один адрес; группа — ссылка «[Имя]», которая раскрывается
    # в адреса участников по адресной книге (в поле остаётся читаемое имя).
    person = contact_store.Contact(display_name="Иванов Иван", emails=["ivan@x.ru", "ivan2@x.ru"])
    group = contact_store.Contact(display_name="Все получатели", emails=["a@x.ru", "b@x.ru"], is_group=True)
    contacts = [person, group]
    candidates = _contact_candidates(contacts)
    assert candidates == ["Иванов Иван <ivan@x.ru>", "[Все получатели]"]
    assert _parse_recipient_list(candidates[1], contacts) == ["a@x.ru", "b@x.ru"]
    assert _parse_recipient_list(", ".join(candidates) + ", manual@x.ru", contacts) == ["a@x.ru", "b@x.ru", "ivan@x.ru", "manual@x.ru"]
    assert _parse_recipient_list("[Нет такой], x@y.ru", contacts) == ["x@y.ru"]  # неизвестная группа — пропуск
    assert _parse_recipient_list("plain@x.ru") == ["plain@x.ru"]
    tooltip = _recipients_tooltip("[Все получатели], ivan@x.ru", contacts)
    assert tooltip.startswith("Адресатов: 3")


def test_thread_infos_groups_by_normalized_subject_with_newest_head() -> None:
    # Пожелание: "надо скрывать более ранние письма и показывать символ
    # группировки" — цепочка по теме без Re:/Fwd:, головное — самое новое.
    infos = _thread_infos([
        _summary(1, "2026-09-10 10:00", "Посылка"),
        _summary(2, "2026-09-12 10:00", "Re: Посылка"),
        _summary(3, "2026-09-11 10:00", "Fwd: посылка"),
        _summary(4, "2026-09-11 10:00", "Другое"),
        _summary(5, "2026-09-11 10:00", ""),
    ])
    assert infos[2].is_head and infos[2].count == 3
    assert not infos[1].is_head and infos[1].head_uid == 2 and infos[3].head_uid == 2
    assert infos[4].count == 1 and infos[4].is_head
    assert 5 not in infos  # без темы не группируем


def test_thread_subject_text_marks_head_and_indents_children() -> None:
    infos = _thread_infos([_summary(1, "2026-09-10 10:00", "Тема"), _summary(2, "2026-09-12 10:00", "Re: Тема")])
    assert _thread_subject_text(_summary(2, "", "Re: Тема"), infos[2], expanded=False) == "▸ Re: Тема (2)"
    assert _thread_subject_text(_summary(2, "", "Re: Тема"), infos[2], expanded=True) == "▾ Re: Тема (2)"
    assert _thread_subject_text(_summary(1, "", "Тема"), infos[1], expanded=True).endswith("Тема")
    assert _thread_subject_text(_summary(1, "", "Тема"), infos[1], expanded=True).startswith(" ")
    assert _thread_subject_text(_summary(9, "", "Одно"), None, expanded=False) == "Одно"


def test_html_to_preview_text_strips_markup_and_scripts() -> None:
    html_content = "<html><head><style>p{}</style></head><body><p>Привет,&nbsp;мир</p><div>вторая</div><script>x()</script></body></html>"
    assert _html_to_preview_text(html_content) == "Привет, мир\nвторая"
    assert _html_to_preview_text("<p>" + "а" * 50 + "</p>", limit=10) == "а" * 10 + "…"


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


def test_safe_attachment_filename_strips_relative_traversal() -> None:
    # Вредоносное вложение может прийти с именем-выходом за пределы
    # temp_dir/каталога сохранения — базовое имя не должно содержать
    # путевых разделителей.
    assert _safe_attachment_filename("../../.ssh/authorized_keys") == "authorized_keys"


def test_safe_attachment_filename_strips_absolute_path() -> None:
    # Path(temp_dir) / "/etc/passwd" в pathlib отбрасывает temp_dir
    # целиком и резолвится в абсолютный путь — критично отфильтровать.
    assert _safe_attachment_filename("/etc/passwd") == "passwd"
    assert _safe_attachment_filename("C:\\Windows\\System32\\evil.dll") == "evil.dll"


def test_safe_attachment_filename_rejects_dot_and_dotdot() -> None:
    assert _safe_attachment_filename("..") == "attachment"
    assert _safe_attachment_filename(".") == "attachment"
    assert _safe_attachment_filename("") == "attachment"


def test_safe_attachment_filename_keeps_normal_name() -> None:
    assert _safe_attachment_filename("Отчёт.pdf") == "Отчёт.pdf"


class _Stats:
    def __init__(self, downloaded: int, pending: int, server_busy: bool = False) -> None:
        self.bodies_downloaded = downloaded
        self.bodies_pending = pending
        self.server_busy = server_busy


def test_another_round_only_when_previous_one_downloaded_something() -> None:
    assert _needs_another_bodies_round(_Stats(downloaded=200, pending=500), None) is True


def test_no_round_when_nothing_was_downloaded() -> None:
    """Вне часов обслуживания тела не качаются: раунд за раундом крутился
    вхолостую и занимал процессор целиком."""
    assert _needs_another_bodies_round(_Stats(downloaded=0, pending=500), None) is False


def test_no_round_when_server_asked_to_wait() -> None:
    assert _needs_another_bodies_round(_Stats(downloaded=10, pending=500, server_busy=True), None) is False


def test_no_round_when_everything_is_downloaded() -> None:
    assert _needs_another_bodies_round(_Stats(downloaded=200, pending=0), None) is False


def test_periodic_mode_does_one_round_per_tick() -> None:
    assert _needs_another_bodies_round(_Stats(downloaded=200, pending=500), 200) is False


def test_shared_domain_labels_counts_matching_tail() -> None:
    assert _shared_domain_labels("calendar.vkm.corp.amurgpz.ru", "imap.vkm.corp.amurgpz.ru") == 4
    assert _shared_domain_labels("calendar.vkm.corp.amurgpz.ru", "svb-mail.corp.amurgpz.ru") == 3
    assert _shared_domain_labels("calendar.vkm.corp.amurgpz.ru", "imap.gmail.com") == 0


def test_calendar_uses_mail_account_of_its_own_server() -> None:
    """Календарь VK берёт пароль почты VK, даже если последней открывали
    папку Exchange с входом по Kerberos."""
    from types import SimpleNamespace

    vk = SimpleNamespace(host="imap.vkm.corp.amurgpz.ru", auth_type="password", username="vk")
    gmail = SimpleNamespace(host="imap.gmail.com", auth_type="password", username="gmail")
    url = "https://calendar.vkm.corp.amurgpz.ru/principals/amurgpz.ru/rsponomarev/calendars/1/"

    assert _pick_calendar_account(url, [gmail, vk]) is vk
    assert _pick_calendar_account("https://caldav.yandex.ru/", [gmail, vk]) is None
