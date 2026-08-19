Name:           redmail
Version:        0.0.1
Release:        1%{?dist}
Summary:        Почтовый клиент (аналог Outlook) для RED OS

License:        Proprietary
URL:            https://github.com/romprs/redmail
Source0:        %{name}-%{version}.tar.gz
Source1:        redmail.desktop

BuildArch:      x86_64
BuildRequires:  python3 >= 3.9
BuildRequires:  python3-pip

Requires:       python3 >= 3.9

%description
RedMail — почтовый клиент для Linux (RED OS 8), функциональный аналог
Microsoft Outlook: почта по IMAP/SMTP, локальные архивы писем (свой
формат — аналог .pst по назначению, не побайтово; импорт из .pst/mbox/
Maildir) и календарь (локальное хранение событий; приглашения, ответы и
переносы встреч — через iTIP-вложения .ics в обычной почте, RFC 5546, без
прямой синхронизации с CalDAV/EWS). Такой подход работает одинаково поверх
MS Exchange, VK Mail и любого другого IMAP/SMTP-сервера — то, что и нужно
в закрытой корпоративной сети без выхода к серверу календаря напрямую.

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

mkdir -p %{buildroot}%{_bindir}
ln -s /opt/redmail/venv/bin/redmail %{buildroot}%{_bindir}/redmail

install -D -m 644 %{SOURCE1} %{buildroot}%{_datadir}/applications/redmail.desktop

%files
/opt/redmail
%{_bindir}/redmail
%{_datadir}/applications/redmail.desktop

%changelog
* Wed Aug 19 2026 RedMail dev <noreply@example.com> - 0.0.1-1
- Первая сборка: почта (IMAP/SMTP, кэш, архивы), календарь (события, iTIP-приглашения/ответы/переносы, повторы)
