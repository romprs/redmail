#!/usr/bin/env bash
# Надёжная доставка большого файла на .80 (память там сбоит): кусками по 16 МБ с проверкой сумм и повтором.
set -u
SRC="$1"; NAME=$(basename "$SRC"); DEST_DIR=/var/tmp/ship; SSH="ssh -i $HOME/.ssh/redos_80 -o StrictHostKeyChecking=no root@192.168.0.80"
W=$(mktemp -d); split -b 16m -d "$SRC" "$W/p."; (cd "$W" && sha256sum p.* | sed 's/\*//' > SUMS)
$SSH "rm -rf $DEST_DIR && mkdir -p $DEST_DIR" 2>/dev/null
scp -q -i ~/.ssh/redos_80 -o StrictHostKeyChecking=no "$W"/SUMS root@192.168.0.80:$DEST_DIR/ 2>/dev/null
for round in 1 2 3 4 5; do
  bad=$($SSH "cd $DEST_DIR && sha256sum -c SUMS 2>/dev/null | grep -vE ': (OK|ЦЕЛ)$' | cut -d: -f1; for f in \$(cut -d' ' -f3 SUMS); do [ -f \$f ] || echo \$f; done" 2>/dev/null | sort -u)
  [ -z "$bad" ] && break
  echo "round $round: resend $(echo $bad | wc -w) chunk(s)"
  for f in $bad; do scp -q -i ~/.ssh/redos_80 -o StrictHostKeyChecking=no "$W/$f" root@192.168.0.80:$DEST_DIR/ 2>/dev/null; done
done
[ -n "$bad" ] && { echo "FAILED chunks: $bad"; exit 1; }
EXP=$(sha256sum "$SRC" | cut -d' ' -f1)
GOT=$($SSH "cd $DEST_DIR && cat \$(cut -d' ' -f3 SUMS) > /var/tmp/$NAME && sha256sum /var/tmp/$NAME | cut -d' ' -f1; rm -rf $DEST_DIR" 2>/dev/null)
rm -rf "$W"
[ "$GOT" = "$EXP" ] && echo "delivered /var/tmp/$NAME OK" || { echo "FINAL MISMATCH"; exit 1; }
