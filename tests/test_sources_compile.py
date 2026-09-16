from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

SOURCES = sorted((Path(__file__).resolve().parent.parent / "src" / "redmail").rglob("*.py"))


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.name)
def test_source_compiles(path: Path, tmp_path: Path) -> None:
    # Модули, которые тесты не импортируют (например, __main__), иначе
    # доходят с синтаксической ошибкой до пакета.
    py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"), doraise=True)
