#!/usr/bin/env bash
set -euo pipefail
# Собирает RPM redmail из текущего состояния git-репозитория. Запускать на
# машине с rpmbuild (например, тестовой RED OS VM), из корня репозитория:
#   packaging/build_rpm.sh
#
# %install собранного пакета сам создаёт приватный venv и ставит туда все
# зависимости через pip — поэтому МАШИНЕ ДЛЯ СБОРКИ нужен доступ в
# интернет (PyPI). Уже собранному .rpm интернет не нужен: venv со всем
# нужным упакован внутрь, ставится и работает офлайн.

cd "$(dirname "$0")/.."

NAME=redmail
VERSION=$(grep -m1 '^version' pyproject.toml | sed -E 's/version = "(.*)"/\1/')

RPMBUILD_ROOT="${HOME}/rpmbuild"
mkdir -p "${RPMBUILD_ROOT}"/{SOURCES,SPECS,BUILD,RPMS,SRPMS,BUILDROOT}

TARBALL="${RPMBUILD_ROOT}/SOURCES/${NAME}-${VERSION}.tar.gz"
git archive --format=tar.gz --prefix="${NAME}-${VERSION}/" -o "${TARBALL}" HEAD

cp packaging/redmail.desktop "${RPMBUILD_ROOT}/SOURCES/"
cp packaging/redmail.spec "${RPMBUILD_ROOT}/SPECS/"

rpmbuild -ba "${RPMBUILD_ROOT}/SPECS/redmail.spec"

echo
echo "Готово. RPM:"
find "${RPMBUILD_ROOT}/RPMS" -name "${NAME}-${VERSION}*.rpm"
