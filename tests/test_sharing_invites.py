"""Разбор приглашений к общим папкам и календарям — по настоящим письмам
из ящика пользователя (VK) и по разметке Exchange."""
from __future__ import annotations

from redmail import sharing_invites as si

VK_FOLDERS_BODY = """
Здравствуйте, Роман Сергеевич

Никита Андреевич Захаров предоставил(а) вам доступ к папкам «Входящие», «0. Файрузов Д.Х.» и ещё 25

Вы можете получить доступ и добавить их в свой почтовый ящик
*Получить доступ:*
<account.vkm.corp.amurgpz.ru/share/mailbox?share-folders=confirm&grant=maxi&timestamp=1789613381&csrf=65f76c&folders=0%2C10%2C11&owner=nazaharov@amurgpz.ru&backUrl=https://e.vkm.corp.amurgpz.ru>
*Отказаться от доступа:*
<account.vkm.corp.amurgpz.ru/share/mailbox?share-folders=reject&grant=maxi&timestamp=1789613381&csrf=65f76c&folders=0%2C10%2C11&owner=nazaharov@amurgpz.ru&backUrl=https://e.vkm.corp.amurgpz.ru>
"""

EXCHANGE_METADATA = b"""<?xml version="1.0"?>
<SharingMessage xmlns="http://schemas.microsoft.com/sharing/2008">
  <DataType>calendar</DataType>
  <Initiator>
    <Name>Orlov Oleg</Name>
    <SmtpAddress>exorg@exlab.local</SmtpAddress>
  </Initiator>
  <Invitation>
    <Providers>
      <Provider Type="ms-exchange-internal" TargetRecipients="exguest@exlab.local">
        <FolderId>AAMkAG=</FolderId>
        <MailboxId>exorg@exlab.local</MailboxId>
        <FolderType>Calendar</FolderType>
      </Provider>
    </Providers>
  </Invitation>
</SharingMessage>
"""


def test_vk_folder_invite_gives_owner_and_confirm_link() -> None:
    invites = si.invites_from_message(
        subject="Вам предоставили доступ к папкам",
        sender_name="Никита Андреевич Захаров", sender_email="nazaharov@amurgpz.ru",
        body=VK_FOLDERS_BODY,
    )
    assert len(invites) == 1
    invite = invites[0]
    assert invite.owner_email == "nazaharov@amurgpz.ru"
    assert invite.kind == si.KIND_MAIL and invite.source == si.SOURCE_VK
    # ссылка «отказаться» приглашением не считается
    assert "share-folders=confirm" in invite.url and invite.url.startswith("https://")
    assert invite.title == "Папки: Никита Андреевич Захаров"


def test_vk_calendar_invite_uses_sender_as_owner() -> None:
    invites = si.invites_from_message(
        subject="Захаров Никита Андреевич предлагает вам совместный доступ к календарю",
        sender_name="Захаров Никита", sender_email="NAZaharov@amurgpz.ru",
        body="https://calendar.vkm.corp.amurgpz.ru/calendars/",
    )
    assert [(i.kind, i.owner_email) for i in invites] == [(si.KIND_CALENDAR, "nazaharov@amurgpz.ru")]


def test_exchange_sharing_invite() -> None:
    invites = si.invites_from_message(
        subject="Orlov Oleg has shared a calendar with you",
        sender_name="Orlov Oleg", sender_email="exorg@exlab.local",
        sharing_metadata=EXCHANGE_METADATA, item_class="IPM.Sharing",
    )
    assert len(invites) == 1
    invite = invites[0]
    assert invite.owner_email == "exorg@exlab.local" and invite.kind == si.KIND_CALENDAR
    assert invite.source == si.SOURCE_EXCHANGE and invite.folder_id == "AAMkAG="


def test_ordinary_letter_is_not_an_invite() -> None:
    assert not si.looks_like_invite("Отчёт за сентябрь")
    assert si.looks_like_invite("Вам предоставили доступ к папкам")
    assert si.looks_like_invite("что угодно", item_class="IPM.Sharing")
    assert si.invites_from_message(
        subject="Отчёт", sender_name="Кто-то", sender_email="x@x.ru",
        body="никаких ссылок",
    ) == []


def test_broken_metadata_does_not_crash() -> None:
    assert si.parse_exchange_metadata("<не xml".encode("utf-8")) == []


def test_exchange_invite_is_not_counted_twice() -> None:
    """У служебного письма Exchange тема тоже про «доступ к календарю» —
    на стенде приглашение Орлова распознавалось дважды."""
    invites = si.invites_from_message(
        subject="Орлов Олег предоставил доступ к календарю",
        sender_name="Orlov Oleg", sender_email="exorg@exlab.local",
        body="Я поделился с вами календарём.",
        sharing_metadata=EXCHANGE_METADATA, item_class="IPM.Sharing",
    )
    assert len(invites) == 1 and invites[0].source == si.SOURCE_EXCHANGE
