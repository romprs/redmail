#!/usr/bin/env bash
# Точечная починка установленного пакета на .80 (сбойная ОЗУ портит часть
# файлов при записи): битые по rpm -V файлы извлекаются из самого пакета и
# перезаписываются, пока проверка не станет чистой.
#   bash repair80.sh /var/tmp/redmail-0.0.1-NN.red80.x86_64.rpm redmail
set -u
RPM="$1"
PKG="$2"
WORK=$(mktemp -d)
cd "$WORK" || exit 1
rpm2cpio "$RPM" | cpio -idm --quiet 2>/dev/null
for round in 1 2 3 4 5 6 7 8; do
  sync; echo 3 > /proc/sys/vm/drop_caches
  bad=$(rpm -V "$PKG" 2>/dev/null | awk '$1 ~ /^..5/ {print $NF}')
  if [ -z "$bad" ]; then
    echo "проход $round: чисто"
    rm -rf "$WORK"
    exit 0
  fi
  echo "проход $round: чиню $(echo "$bad" | wc -l) файл(ов)"
  for f in $bad; do
    src="$WORK$f"
    [ -f "$src" ] || continue
    # Сначала — эталон из пакета: он тоже прочитан через ту же память.
    want=$(rpm -q --qf '[%{FILENAMES} %{FILEDIGESTS}\n]' "$PKG" | awk -v f="$f" '$1 == f {print $2}')
    got_src=$(sha256sum "$src" | cut -d' ' -f1)
    if [ -n "$want" ] && [ "$got_src" != "$want" ]; then
      # Извлечённая копия сама битая — извлекаем этот файл заново.
      rpm2cpio "$RPM" | cpio -idm --quiet ".$f" 2>/dev/null
    fi
    cp -a "$src" "$f"
  done
done
echo "не удалось вылечить за 8 проходов"
rm -rf "$WORK"
exit 1
