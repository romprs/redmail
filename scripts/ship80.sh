#!/usr/bin/env bash
# Надёжная доставка большого файла на хост .80 (там сбоит ОЗУ: файлы портятся
# при копировании). Файл режется на куски по 16 МБ, каждый проверяется по
# sha256 на стороне .80 и при несовпадении пересылается; собирается в /var/tmp.
#   bash scripts/ship80.sh /d/111/redmail-0.0.1-NN.red80.x86_64.rpm
set -u
SRC="$1"
NAME=$(basename "$SRC")
HOST=root@192.168.0.80
KEY="$HOME/.ssh/redos_80"
DEST_DIR="${SHIP_DIR:-/var/tmp/ship}"  # свой каталог через SHIP_DIR, если доставки идут параллельно
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 $HOST"

W=$(mktemp -d)
split -b 16m -d "$SRC" "$W/p."
(cd "$W" && sha256sum p.* | sed 's/\*//' > SUMS)
PARTS=$(cd "$W" && ls p.*)

$SSH "rm -rf $DEST_DIR && mkdir -p $DEST_DIR" </dev/null 2>/dev/null
scp -q -i "$KEY" -o StrictHostKeyChecking=no "$W/SUMS" "$HOST:$DEST_DIR/" 2>/dev/null

# Первая отправка — все куски разом (один scp).
scp -q -i "$KEY" -o StrictHostKeyChecking=no "$W"/p.* "$HOST:$DEST_DIR/" 2>/dev/null

bad=""
for round in 1 2 3 4 5 6 7 8 9 10; do
  # Проверка на .80: имена битых/отсутствующих кусков. Перед проверкой —
  # сброс страничного кэша: битая ОЗУ .80 портит кусок в кэше, после сброса
  # он перечитывается с диска (иначе один и тот же кусок «бился» 6 раундов).
  bad=$($SSH "sync; echo 3 > /proc/sys/vm/drop_caches; cd $DEST_DIR && sha256sum -c --quiet SUMS 2>&1 | sed -n 's/: .*//p'" </dev/null 2>/dev/null | tr -d '\r')
  if [ -z "$bad" ]; then
    break
  fi
  echo "round $round: resend $(echo $bad | wc -w) chunk(s): $bad"
  for f in $bad; do
    scp -q -i "$KEY" -o StrictHostKeyChecking=no "$W/$f" "$HOST:$DEST_DIR/" 2>/dev/null
  done
done
if [ -n "$bad" ]; then
  echo "FAILED chunks: $bad"
  rm -rf "$W"
  exit 1
fi

EXP=$(sha256sum "$SRC" | cut -d' ' -f1)
# Сборка — с несколькими попытками и сбросом кэша перед подсчётом суммы:
# на .80 сбоит ОЗУ, и собранный файл портится уже при записи/чтении через
# страничный кэш, хотя все куски по отдельности целы (каждая попытка даёт
# свою сумму — признак именно памяти, а не потери при передаче).
GOT=""
for attempt in 1 2 3 4 5 6; do
  GOT=$($SSH "cd $DEST_DIR && cat $(echo $PARTS) > /var/tmp/$NAME && sync && echo 3 > /proc/sys/vm/drop_caches; sha256sum /var/tmp/$NAME | cut -d' ' -f1" </dev/null 2>/dev/null | tr -d '\r')
  [ "$GOT" = "$EXP" ] && break
  echo "attempt $attempt: собранный файл не совпал, пересобираю"
done
$SSH "rm -rf $DEST_DIR" </dev/null 2>/dev/null
rm -rf "$W"
if [ "$GOT" = "$EXP" ]; then
  echo "delivered /var/tmp/$NAME OK"
else
  echo "FINAL MISMATCH (got $GOT, expected $EXP)"
  exit 1
fi
