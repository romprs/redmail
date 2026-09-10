# -*- coding: utf-8 -*-
"""Снимок сторонних Python-компонентов установленного пакета для документа
05 (перечень компонентов и лицензий). Запускать интерпретатором из
установленного окружения:

    /opt/redmail/venv/bin/python docs/registry/dump_components.py > docs/registry/components.txt

Формат строки: имя|версия|лицензия|домашняя страница. Служебные пакеты
(pip, setuptools, wheel) и сам продукт исключаются.
"""
from __future__ import annotations

from importlib.metadata import distributions

SKIP = {"pip", "setuptools", "wheel", "redmail"}


def main() -> None:
    rows: dict[str, tuple[str, str, str]] = {}
    for dist in distributions():
        meta = dist.metadata
        name = meta["Name"]
        if not name or name.lower() in SKIP:
            continue
        license_ = meta.get("License-Expression") or meta.get("License") or ""
        if not license_ or len(license_) > 60 or "\n" in license_:
            classifiers = [
                c.split("::")[-1].strip()
                for c in (meta.get_all("Classifier") or [])
                if c.startswith("License")
            ]
            license_ = ", ".join(classifiers) or (license_.splitlines()[0][:60] if license_ else "?")
        home = meta.get("Home-page") or ""
        if not home:
            for url in meta.get_all("Project-URL") or []:
                label, _, value = url.partition(",")
                if label.strip().lower() in ("homepage", "source", "repository", "source code"):
                    home = value.strip()
                    break
        rows[name.lower()] = (name, meta["Version"], license_.replace("|", "/"), home.replace("|", "/"))
    print("# имя|версия|лицензия|домашняя страница — снимок установленного окружения")
    for _, (name, version, license_, home) in sorted(rows.items()):
        print(f"{name}|{version}|{license_}|{home}")


if __name__ == "__main__":
    main()
