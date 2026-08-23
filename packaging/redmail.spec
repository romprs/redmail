Name:           redmail
Version:        0.0.1
Release:        12%{?dist}
Summary:        Почтовый клиент (аналог Outlook) для RED OS

License:        Proprietary
URL:            https://github.com/romprs/redmail
Source0:        %{name}-%{version}.tar.gz
Source1:        redmail.desktop

BuildArch:      x86_64
BuildRequires:  python3 >= 3.9
BuildRequires:  python3-pip
BuildRequires:  python3-devel
# libpff-python (импорт .pst) собирается из исходников при pip install —
# готовых wheel под RED OS нет. Без этих пакетов сборка .pst-импорта тихо
# отсутствует в собранном RPM (было так до Release 9 — обнаружено, когда
# импорт .pst не заработал через установленный пакет, хотя работал из
# исходников на этой же машине, где эти пакеты уже стояли вручную).
BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  automake
BuildRequires:  libtool
BuildRequires:  autoconf
BuildRequires:  zlib-devel
BuildRequires:  bzip2-devel
BuildRequires:  openssl-devel

Requires:       python3 >= 3.9

# Всё под /opt/redmail/venv — сторонние pip-пакеты (в основном PySide6),
# не наш код. rpm's brp-mangle-shebangs требует, чтобы каждый исполняемый
# файл с #!/usr/bin/env python имел явную версию (python3), и падает с
# ошибкой на файлах вроде PySide6/scripts/pyside_tool.py, которые мы не
# редактируем и которые не нужно запускать напрямую (они библиотечные,
# не наши точки входа — единственная реальная точка входа, venv/bin/redmail,
# уже правильно переписана вручную в %%install). Исключаем весь venv из
# этой проверки вместо того, чтобы точечно чинить вендоренные файлы.
%global __brp_mangle_shebangs_exclude_from ^/opt/redmail/venv/.*$

# Автосканер зависимостей rpm просматривает КАЖДЫЙ .so под venv, включая
# Qt-плагины, которые redmail не грузит (Qt SQL под Oracle/Firebird/Mimer
# и т.п.) — и на них ловятся жёсткие Requires: на клиентские библиотеки
# этих СУБД, которых на системе никогда не будет и не нужно. Всё под
# /opt/redmail/venv самодостаточно (все свои зависимости внутри того же
# пакета), поэтому автогенерацию зависимостей для него отключаем целиком.
%global __requires_exclude_from ^/opt/redmail/venv/.*$
%global __provides_exclude_from ^/opt/redmail/venv/.*$

%description
RedMail — почтовый клиент для Linux (RED OS 8), функциональный аналог
Microsoft Outlook: почта по IMAP/SMTP, локальные архивы писем (свой
формат — аналог .pst по назначению, не побайтово; импорт из .pst/mbox/
Maildir) и календарь (локальное хранение событий; приглашения, ответы и
переносы встреч — через iTIP-вложения .ics в обычной почте, RFC 5546).
Такой подход работает одинаково поверх MS Exchange, VK Mail и любого
другого IMAP/SMTP-сервера — то, что и нужно в закрытой корпоративной
сети без прямого выхода к серверу календаря. Опционально — двусторонняя
синхронизация с CalDAV-сервером (адрес указывается вручную в
Параметрах), если такой сервер в сети доступен.

Приложение ставится вместе с приватным виртуальным окружением Python
(в /opt/redmail/venv) со всеми зависимостями внутри — собирается один раз
на этапе сборки пакета. После установки RPM работает офлайн, ничего не
скачивает и не требует доступа в интернет ни при установке, ни при
запуске (интернет нужен только МАШИНЕ, на которой собирается сам RPM).

%prep
%setup -q

%build
# Настоящая сборка (venv + зависимости) — в %%install, а не здесь: venv
# нужно создавать сразу по финальному пути установки (/opt/redmail/venv),
# иначе шебанги в venv/bin/* и pyvenv.cfg останутся указывать на путь
# сборки, а не на путь после rpm -i, и всё сломается на целевой машине.

%install
mkdir -p %{buildroot}/opt/redmail
python3 -m venv %{buildroot}/opt/redmail/venv
%{buildroot}/opt/redmail/venv/bin/pip install --no-cache-dir --upgrade pip wheel
%{buildroot}/opt/redmail/venv/bin/pip install --no-cache-dir %{_builddir}/%{name}-%{version}

# pip пишет шебанги (bin/pip, bin/redmail, bin/pyside6-*, ...) и
# pyvenv.cfg (строка command=) с ТЕКУЩИМ путём venv — то есть с путём
# сборки (%{buildroot}/...), а не с финальным путём установки
# (/opt/redmail/venv). check-buildroot иначе обрывает сборку, найдя путь
# сборки внутри установленных файлов; замена ниже убирает этот префикс.
find %{buildroot}/opt/redmail/venv -type f \( -name "pyvenv.cfg" -o -path "*/bin/*" \) -exec \
    sed -i "s|%{buildroot}||g" {} \;

mkdir -p %{buildroot}%{_bindir}
ln -s /opt/redmail/venv/bin/redmail %{buildroot}%{_bindir}/redmail

install -D -m 644 %{SOURCE1} %{buildroot}%{_datadir}/applications/redmail.desktop

%files
/opt/redmail
%{_bindir}/redmail
%{_datadir}/applications/redmail.desktop

%changelog
* Sun Aug 23 2026 RedMail dev <noreply@example.com> - 0.0.1-12
- Импорт .pst/.mbox/Maildir и отправка приглашений/ответов по календарю
  (создание, перенос мышью, отмена встречи, ответ на приглашение) больше
  не блокируют интерфейс — выполняются в фоновом потоке; у импорта
  индикатор процесса, у отправки — сообщение в строке состояния
- Если ещё не открыто ни одного архива, диалог выбора архива больше не
  показывает бесполезный единственный пункт — сразу открывается диалог
  создания нового архива
- Открытые/импортированные архивы теперь можно отключить: правая кнопка
  мыши на архиве в дереве папок → «Закрыть архив» (сам файл не удаляется)
- Исправлено декодирование имени отправителя в архиве для писем, где в
  From нет реального адреса в <угловых скобках> — раньше в таких письмах
  имя отправителя оставалось нерасшифрованным (=?utf-8?b?...?=)

* Sun Aug 23 2026 RedMail dev <noreply@example.com> - 0.0.1-11
- Панель инструментов почты разбита на два ряда — раньше при большом
  числе кнопок Qt прятал "лишние" (в т.ч. Параметры) за скрытую стрелку
- Правила сортировки почты и добавление учётной записи перенесены в
  Параметры (были отдельными кнопками на переполненной панели)
- Событие в виде месяца (таблетка) больше не пытается перетаскиваться
  мышью — перенос там раньше не работал (не был реализован), теперь
  явно недоступен вместо обманчивого полу-перетаскивания

* Thu Aug 20 2026 RedMail dev <noreply@example.com> - 0.0.1-2
- Календарь: недельная сетка (даты в кружках, линия "сейчас", drag-перенос
  с рассылкой участникам), вложения к событиям, кликабельные ссылки
- Адресная книга: локальное хранилище, импорт vCard/CSV, автодополнение
  в письмах и встречах, "добавить отправителя в контакты"
- Фильтр писем по цвету маркера; открытые архивы переживают перезапуск

* Wed Aug 19 2026 RedMail dev <noreply@example.com> - 0.0.1-1
- Первая сборка: почта (IMAP/SMTP, кэш, архивы), календарь (события, iTIP-приглашения/ответы/переносы, повторы)
