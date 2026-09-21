set -u
N="$1"
RPM=redmail-0.0.1-$N.red80.x86_64.rpm
SRC=/root/rpmbuild/RPMS/x86_64/$RPM
VM="ssh -i $HOME/.ssh/redos_vm -o StrictHostKeyChecking=no -o ConnectTimeout=15 root@192.168.0.10"
H80="ssh -i $HOME/.ssh/redos_80 -o StrictHostKeyChecking=no -o ConnectTimeout=15 root@192.168.0.80"

$VM "rpm -K $SRC && dnf -y install $SRC >/dev/null 2>&1; rpm -qf /usr/bin/redmail" </dev/null 2>/dev/null
scp -q -i "$HOME/.ssh/redos_vm" -o StrictHostKeyChecking=no root@192.168.0.10:$SRC /e/rrr/dist/ 2>/dev/null
mkdir -p /d/111/mail
cp /e/rrr/dist/$RPM /d/111/mail/
VMSUM=$($VM "sha256sum $SRC | cut -c1-16" </dev/null 2>/dev/null | tr -d '\r')
LOCSUM=$(sha256sum /e/rrr/dist/$RPM | cut -c1-16)
D11SUM=$(sha256sum /d/111/mail/$RPM | cut -c1-16)
echo "SUMS vm=$VMSUM dist=$LOCSUM d111=$D11SUM"
[ "$VMSUM" = "$LOCSUM" ] && [ "$VMSUM" = "$D11SUM" ] || { echo "COPY_MISMATCH"; exit 1; }

bash /e/rrr/redos-mail-client/scripts/ship80.sh /d/111/mail/$RPM
$H80 "rpm -K /var/tmp/$RPM && dnf -y install /var/tmp/$RPM 2>&1 | tail -1; rpm -qf /usr/bin/redmail" </dev/null 2>/dev/null
# На .80 сбоит ОЗУ и портит часть файлов уже при установке (клиент падал с
# segfault в 136 и 140): сверяем установленное с пакетом и чиним битое.
scp -q -i "$HOME/.ssh/redos_80" -o StrictHostKeyChecking=no /e/rrr/redos-mail-client/scripts/repair80.sh root@192.168.0.80:/root/repair80.sh 2>/dev/null
$H80 "bash /root/repair80.sh /var/tmp/$RPM redmail" </dev/null 2>/dev/null
$H80 "pkill -u test -f '/usr/bin/redmail'; sleep 2; sudo -u test env HOME=/home/test DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority XDG_RUNTIME_DIR=/run/user/1000 setsid nohup /usr/bin/redmail >/tmp/redmail-launch.log 2>&1 < /dev/null & sleep 40; pgrep -u test -f /usr/bin/redmail >/dev/null && echo CLIENT_RUNNING || echo CLIENT_DOWN" </dev/null 2>/dev/null
# Резидент напоминаний — отдельный процесс; без перезапуска он продолжал бы
# работать со старым кодом из памяти.
$H80 "pkill -u test -f redmail-reminder; sleep 1; sudo -u test env HOME=/home/test DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus setsid nohup /usr/bin/redmail-reminder >/tmp/reminder.log 2>&1 < /dev/null & sleep 5; pgrep -u test -f redmail-reminder >/dev/null && echo REMINDER_RUNNING || echo REMINDER_DOWN" </dev/null 2>/dev/null
$H80 "grep -c ERROR /home/test/.config/redmail/logs/redmail.log 2>/dev/null | sed 's/^/ERRORS_IN_LOG=/'" </dev/null 2>/dev/null
echo "DELIVERY_DONE"
