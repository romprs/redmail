# RedMail — почтовый клиент для РЕД ОС

Настольный почтовый клиент для РЕД ОС 8 (Linux), функциональный аналог
Microsoft Outlook: почта по IMAP/SMTP и Microsoft Exchange (EWS), локальные
архивы писем с импортом из Outlook (.pst), mbox и Maildir, календарь с
приглашениями на встречи по iTIP и синхронизацией с CalDAV, адресная книга,
единый вход по Kerberos. Интерфейс — Qt 6 (PySide6), русский язык.

## Установка на РЕД ОС

Готовый пакет самодостаточен (все зависимости внутри), интернет не нужен:

```bash
sudo dnf install ./redmail-0.0.1-<сборка>.red80.x86_64.rpm
redmail
```

Подробнее — `docs/registry/out/02_Руководство_по_установке.docx`,
руководство пользователя — `03_Руководство_пользователя.docx`.

## Сборка пакета

На машине с РЕД ОС 8 и доступом к PyPI (нужен только для сборки):

```bash
sudo dnf install rpm-build python3-devel python3-pip gcc make automake libtool autoconf \
    zlib-devel bzip2-devel openssl-devel krb5-devel git
packaging/build_rpm.sh
# результат: ~/rpmbuild/RPMS/x86_64/redmail-0.0.1-<сборка>.red80.x86_64.rpm
```

Версия сборки и перечень изменений — в `packaging/redmail.spec` (Release и changelog).

## Запуск из исходников (разработка)

```bash
python -m venv .venv
.venv/Scripts/pip install -e .          # Linux: .venv/bin/pip
.venv/Scripts/python -m redmail
```

## Тесты

```bash
.venv/Scripts/pip install pytest
.venv/Scripts/python -m pytest -q
```

Тесты покрывают разбор IMAP, SMTP-кодирование, архивы, календарь и iTIP,
CalDAV (обнаружение календарей, синхронизация), Kerberos/GSSAPI и интерфейс
(в режиме без экрана, `QT_QPA_PLATFORM=offscreen`) — без реальных серверов.

## Состав

| Модуль | Назначение |
| --- | --- |
| `redmail/imap_client.py` | сеанс IMAP: папки, письма, флаги, маркеры, переподключение |
| `redmail/smtp_client.py` | отправка писем, вложения, встроенные изображения |
| `redmail/ews_client.py` | Microsoft Exchange (EWS) |
| `redmail/gssapi_sasl.py` | единый вход Kerberos для IMAP/SMTP |
| `redmail/mailbox.py`, `cache_store.py` | локальный кэш почты (SQLite) |
| `redmail/archive_store.py` | архивы `.rmarchive`, импорт .pst/mbox/Maildir |
| `redmail/calendar_store.py`, `itip.py` | календарь, приглашения iTIP, .ics |
| `redmail/caldav_sync.py` | синхронизация с CalDAV, поиск календарей |
| `redmail/contact_store.py` | адресная книга, импорт vCard/CSV |
| `redmail/ui/` | главное окно, недельный календарь, темы оформления |

Данные пользователя: `~/.config/redmail` (настройки, кэш, календарь, контакты);
пароли — в системном хранилище (Secret Service/keyring).

## Документация для регистрации

`docs/registry/build_docs.py` формирует пакет документов для Роспатента и
реестра российского ПО (описание характеристик, руководства, процессы
жизненного цикла, перечень сторонних компонентов, реферат, листинг,
лицензионное соглашение) в `docs/registry/out/`.

## Лицензия

Проприетарная. Правообладатель — Пономарев Роман Сергеевич. Сторонние
компоненты — по их открытым лицензиям (перечень в документе 05).
