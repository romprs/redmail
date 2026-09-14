"""Замена домена у получателей при отправке.

Зачем: на время перехода с Exchange на VK один и тот же человек существует
под двумя адресами — старым (name@amurgpz.ru) и фактическим на новом
сервере (name@vk.corp.amurgpz.ru). Веб-клиент нового сервера старые адреса
принимает, а почтовая программа отправляет ровно то, что записано в поле
«Кому», и письмо не уходит. Правило «старый домен → новый домен»
подменяет домен у получателей в момент отправки: адресная книга, ответы и
пересылки продолжают работать со старыми адресами, а на сервер уходит то,
что он понимает.

Правила хранятся текстом, по одному в строке: `старый = новый`. Регистр не
важен, домен можно писать с «@» или без.
"""
from __future__ import annotations

from email.utils import getaddresses, parseaddr

Rule = tuple[str, str]


def _clean_domain(value: str) -> str:
    return value.strip().lstrip("@").strip().lower()


def parse_rules(text: str) -> list[Rule]:
    """Текст из настроек → список правил. Строки без разделителя, пустые и
    начинающиеся с «#» пропускаются."""
    rules: list[Rule] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for separator in ("->", "=>", "=", ":"):
            if separator in line:
                left, right = line.split(separator, 1)
                break
        else:
            continue
        source, target = _clean_domain(left), _clean_domain(right)
        if source and target and source != target:
            rules.append((source, target))
    return rules


def format_rules(rules: list[Rule]) -> str:
    return "\n".join(f"{source} = {target}" for source, target in rules)


def rewrite_address(address: str, rules: list[Rule]) -> str:
    """Подменяет домен одного адреса. Адрес вида «Имя <a@b>» сохраняет имя."""
    if not rules or not address:
        return address
    name, addr = parseaddr(address)
    if "@" not in addr:
        return address
    local, _, domain = addr.rpartition("@")
    lowered = domain.lower()
    for source, target in rules:
        if lowered == source:
            new_addr = f"{local}@{target}"
            return f"{name} <{new_addr}>" if name else new_addr
    return address


def rewrite_recipients(addresses: list[str], rules: list[Rule]) -> list[str]:
    """Список адресов получателей с заменёнными доменами, без повторов
    (после замены два старых адреса могут совпасть)."""
    if not rules:
        return list(addresses)
    result: list[str] = []
    seen: set[str] = set()
    for address in addresses:
        rewritten = rewrite_address(address, rules)
        key = parseaddr(rewritten)[1].lower() or rewritten.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(rewritten)
    return result


def describe_changes(before: list[str], after: list[str]) -> list[str]:
    """Пары «было → стало» для журнала и статусной строки."""
    changes = []
    before_addrs = [addr for _n, addr in getaddresses(before)]
    after_addrs = [addr for _n, addr in getaddresses(after)]
    for old, new in zip(before_addrs, after_addrs):
        if old.lower() != new.lower():
            changes.append(f"{old} → {new}")
    return changes
