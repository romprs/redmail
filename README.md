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

## Локальный канал управления (IPC)

Запущенное окно слушает локальный сокет `redmail-ipc`
(`src/redmail/ipc_server.py`) — через него внешняя программа (прежде всего
будущий голосовой ассистент) просит открыть письмо/встречу с готовыми полями.
Только локальный сокет, никакого TCP; доступ — тому же пользователю ОС.

Протокол построчный JSON: одна строка `{"action": ..., "args": {...}}` —
одна строка ответа `{"ok": true, ...}` либо `{"ok": false, "error": "..."}`.
Команды: `ping`, `focus`, `compose_email`, `create_event`, `update_event`,
`find_events`, `cancel_event`, `apply_mail_rules`, `list_mail_rules`.

`update_event`/`cancel_event` требуют `uid` встречи — голосовая сторона его
не знает, у неё есть только тема (и, возможно, дата). `find_events` (`subject`
— подстрока темы, необязательно; `date` — `YYYY-MM-DD`, если не задана, берётся
сегодняшняя) возвращает список подходящих встреч с `uid`, по которому уже
можно вызвать `update_event`/`cancel_event`.

Всё, что отправляет что-либо наружу (письмо, приглашение, отмена встречи),
только ОТКРЫВАЕТ обычный диалог с заполненными полями — «Отправить»/
«Сохранить»/«Да» нажимает человек. Без подтверждения выполняются лишь
`focus`, `find_events`, `list_mail_rules` и `apply_mail_rules` (перекладывание
писем между папками того же ящика).

Проверить руками (или из голосового помощника audioreferent):

```bash
python3 scripts/ipc_client_test.py ping
python3 scripts/ipc_client_test.py compose_email --to ivan@example.com --subject Тест
python3 scripts/ipc_client_test.py create_event --subject Планёрка \
    --start 2026-09-10T15:00 --duration 30 --participants a@e.com,b@e.com
```

Фактический адрес сокета работающее приложение пишет в
`<каталог настроек>/ipc-endpoint.json`, скрипт читает его оттуда.

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
