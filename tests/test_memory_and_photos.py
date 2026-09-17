from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from redmail import contact_store, memory_report  # noqa: E402
from redmail.ui import main_window as mw  # noqa: E402


def _jpeg(side: int) -> bytes:
    QApplication.instance() or QApplication([])
    image = QImage(side, side, QImage.Format.Format_RGB32)
    for y in range(side):  # шум, чтобы JPEG не сжался в ноль
        for x in range(0, side, 3):
            image.setPixelColor(x, y, QColor((x * 7 + y * 13) % 256, (x * y) % 256, (x + y * 5) % 256))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG", 95)
    return bytes(buffer.data())


def test_large_contact_photos_are_shrunk_small_ones_kept(tmp_path: Path) -> None:
    path = tmp_path / "contacts.rmcontacts"
    contact_store.create_contacts_book(path)
    big = _jpeg(900)
    small = _jpeg(40)
    assert len(big) > contact_store.PHOTO_MAX_BYTES > len(small)
    contact_store.save_contact(path, contact_store.Contact(uid="1", display_name="Большое", photo=big, photo_type="image/jpeg"))
    contact_store.save_contact(path, contact_store.Contact(uid="2", display_name="Маленькое", photo=small, photo_type="image/jpeg"))
    contact_store.save_contact(path, contact_store.Contact(uid="3", display_name="Битое", photo=b"x" * 20000, photo_type="image/jpeg"))

    assert contact_store.shrink_large_photos(path, mw._shrink_photo_bytes) == 1
    photos = {c.display_name: c.photo for c in contact_store.list_contacts(path)}
    assert len(photos["Большое"]) < contact_store.PHOTO_MAX_BYTES
    assert not QImage.fromData(photos["Большое"]).isNull() and QImage.fromData(photos["Большое"]).width() == 128
    assert photos["Маленькое"] == small and photos["Битое"] == b"x" * 20000
    assert contact_store.shrink_large_photos(path, mw._shrink_photo_bytes) == 0  # повторно нечего


def test_memory_report_works_on_any_platform() -> None:
    lines = memory_report.process_lines()
    assert lines and all(isinstance(line, str) for line in lines)
    objects = memory_report.python_objects(top=5)
    assert len(objects) == 5 and all(count > 0 for _name, count, _mb in objects)
    memory_report.free_memory()  # на Windows без malloc_trim — просто сборка мусора
