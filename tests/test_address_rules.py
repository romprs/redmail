from __future__ import annotations

from redmail.address_rules import (
    describe_changes,
    format_rules,
    parse_rules,
    rewrite_address,
    rewrite_recipients,
)


def test_parse_rules_accepts_common_separators_and_at_sign() -> None:
    text = """
    # переход на VK
    amurgpz.ru = vk.corp.amurgpz.ru
    @old.example.com -> @new.example.com
    corp.example : mail.corp.example
    мусорная строка без разделителя
    same.ru = same.ru
    """
    assert parse_rules(text) == [
        ("amurgpz.ru", "vk.corp.amurgpz.ru"),
        ("old.example.com", "new.example.com"),
        ("corp.example", "mail.corp.example"),
    ]
    assert parse_rules("") == []
    assert format_rules([("a.ru", "b.ru")]) == "a.ru = b.ru"


def test_rewrite_address_changes_domain_and_keeps_display_name() -> None:
    rules = [("amurgpz.ru", "vk.corp.amurgpz.ru")]
    assert rewrite_address("rsponomarev@amurgpz.ru", rules) == "rsponomarev@vk.corp.amurgpz.ru"
    assert rewrite_address("Пономарев Роман <rsponomarev@AmurGPZ.RU>", rules) == "Пономарев Роман <rsponomarev@vk.corp.amurgpz.ru>"
    assert rewrite_address("someone@other.ru", rules) == "someone@other.ru"
    assert rewrite_address("не адрес", rules) == "не адрес"
    assert rewrite_address("x@amurgpz.ru", []) == "x@amurgpz.ru"


def test_rewrite_recipients_deduplicates_after_rewrite() -> None:
    rules = [("amurgpz.ru", "vk.corp.amurgpz.ru")]
    result = rewrite_recipients(
        ["ivan@amurgpz.ru", "ivan@vk.corp.amurgpz.ru", "petr@amurgpz.ru"], rules
    )
    assert result == ["ivan@vk.corp.amurgpz.ru", "petr@vk.corp.amurgpz.ru"]
    assert rewrite_recipients(["a@b.ru"], []) == ["a@b.ru"]


def test_describe_changes_lists_only_replaced_addresses() -> None:
    before = ["ivan@amurgpz.ru", "petr@other.ru"]
    after = ["ivan@vk.corp.amurgpz.ru", "petr@other.ru"]
    assert describe_changes(before, after) == ["ivan@amurgpz.ru → ivan@vk.corp.amurgpz.ru"]
