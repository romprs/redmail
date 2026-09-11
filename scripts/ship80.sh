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
DEST_DIR=/var/tmp/ship
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
for round in 1 2 3 4 5 6; do
  # Проверка на .80: имена битых/отсутствующих кусков.
  bad=$($SSH "cd $DEST_DIR && sha256sum -c --quiet SUMS 2>&1 | sed -n 's/: .*//p'" </dev/null 2>/dev/null | tr -d '\r')
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
GOT=$($SSH "cd $DEST_DIR && cat $(echo $PARTS) > /var/tmp/$NAME && sha256sum /var/tmp/$NAME | cut -d' ' -f1 && rm -rf $DEST_DIR" </dev/null 2>/dev/null | tr -d '\r')
rm -rf "$W"
if [ "$GOT" = "$EXP" ]; then
  echo "delivered /var/tmp/$NAME OK"
else
  echo "FINAL MISMATCH (got $GOT, expected $EXP)"
  exit 1
fi
