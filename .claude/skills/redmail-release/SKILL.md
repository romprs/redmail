---
name: redmail-release
description: Выпуск сборки redmail — прогон тестов, бамп Release в spec с changelog, коммит и push, сборка RPM на VM RED OS, установка на VM и на .80, копия в D:\111, проверка запуска. Использовать по команде /redmail-release или когда изменения готовы к сборке.
---

# Выпуск сборки redmail

Репозиторий: `E:\rrr\redos-mail-client` (Windows, venv `.venv`). Сборочная VM RED OS 8:
`ssh -i ~/.ssh/redos_vm root@192.168.0.10`, чекаут `/home/test/redmail`. Боевой хост
пользователя: `ssh -i ~/.ssh/redos_80 root@192.168.0.80`, клиент запускается под
пользователем `test` в сессии `:1`. Копии пакетов: `D:\111`.

Коммиты — БЕЗ трейлера Co-Authored-By и без упоминаний ИИ (продукт идёт на регистрацию).

## Шаги

1. **Тесты и компиляция** (Windows):
   ```bash
   cd /e/rrr/redos-mail-client && ./.venv/Scripts/python.exe -m compileall -q src/redmail && ./.venv/Scripts/python.exe -m pytest -q 2>&1 | tail -1
   ```
   Дальше только при «N passed».
2. **Бамп релиза**: в `packaging/redmail.spec` увеличить `Release:` на 1 и добавить запись
   в `%changelog` первой (формат `* <Day Mon DD YYYY> redmail <redmail@example.com> - 0.0.1-<N>`,
   строки `- …` по-русски, по сути изменений). Файл в CRLF — править через python с
   `newline=""`/`replace("\r\n","\n")` и записью обратно с CRLF.
3. **Коммит и push**: осмысленный коммит изменений (если ещё не сделан), затем
   `git commit -qam "Bump RPM release to <N>" && git push -q origin main`.
4. **Сборка на VM** (10–15 минут, запускать в фоне):
   ```bash
   ssh -i ~/.ssh/redos_vm root@192.168.0.10 "cd /home/test/redmail && git pull -q origin main && bash packaging/build_rpm.sh > /tmp/build<N>.log 2>&1; while pgrep -x rpmbuild >/dev/null; do sleep 5; done; rpm -K ~/rpmbuild/RPMS/x86_64/redmail-0.0.1-<N>.red80.x86_64.rpm && dnf -y install ~/rpmbuild/RPMS/x86_64/redmail-0.0.1-<N>.red80.x86_64.rpm | tail -1; rpm -q redmail"
   ```
   Основной RPM ~210 МБ дописывается после debuginfo — ждать, пока `rpm -q redmail`
   не покажет `<N>`; `rpm -K` должен сказать «digests ОК».
5. **Доставка**: `scp` RPM с VM в `/d/111/`, затем с Windows на `.80:/tmp/`, там
   `rpm -K` + `dnf -y install`, удалить предыдущий RPM из `/tmp`.
6. **Перезапуск клиента на .80** (окно на живом рабочем столе пользователя):
   ```bash
   pkill -u test -f '/usr/bin/redmail'; sleep 1
   sudo -u test env HOME=/home/test DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority XDG_RUNTIME_DIR=/run/user/1000 setsid nohup /usr/bin/redmail >/tmp/redmail-launch.log 2>&1 < /dev/null & disown
   ```
   Через 30–40 с проверить: процесс есть, `py-spy dump --pid` (стоит в `/opt/redmail/venv/bin`)
   показывает главный поток `idle`, IPC отвечает:
   `sudo -u test env HOME=/home/test python3 -c "from audioreferent import redmail_client as c; print(c.send_request('ping'))"`,
   в `/home/test/.config/redmail/logs/redmail.log` нет ERROR.
7. **Отчёт пользователю** по-русски: номер сборки, где лежит, что вошло, что проверить.

## Подводные камни

- В heredoc Bash-инструмента обратные слэши искажаются — патчи с `\\` писать через Write в файл.
- Не запускать две сборки одновременно (общий BUILDROOT).
- Пароли пользователя в keyring VM недоступны из ssh — офлайн-проверки делать через
  `QT_QPA_PLATFORM=offscreen` и подмену `app_dir`; настоящий IMAP — Dovecot на VM
  (`syncuser`/`SyncPass123`, 127.0.0.1:143 без SSL).
