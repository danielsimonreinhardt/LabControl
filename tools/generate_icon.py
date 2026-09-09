"""Erzeugt lab_gui/icons/app_icon.ico fuer das .exe-Symbol (siehe
LabControl.spec: EXE(..., icon=...)).

Nicht Teil der laufenden App. Wird von LabControl.spec vor jedem Build
automatisch aufgerufen (main() importiert und ausgefuehrt), analog zu
tools/generate_splash.py. Rendert dieselben Pixmaps wie lab_gui/app_icon.py
(das zur Laufzeit per QApplication.setWindowIcon() das Fenster-/
Taskleistensymbol der laufenden App setzt) -- beide Stellen muessen
optisch zusammenpassen, siehe Docstring dort.

Baut die .ico-Datei von Hand zusammen (ICONDIR/ICONDIRENTRY + eingebettete
PNGs, das seit Windows Vista unterstuetzte "PNG-in-ICO"-Format) statt ueber
eine Bildbibliothek wie Pillow, die sonst nirgends im Projekt gebraucht wird
-- Qt (ohnehin Kernabhaengigkeit) liefert die einzelnen Groessen bereits als
PNG-faehige QPixmaps.

    python tools/generate_icon.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lab_gui"))

from app_icon import SIZES, build_pixmap  # noqa: E402

OUT_PATH = REPO_ROOT / "lab_gui" / "icons" / "app_icon.ico"


def _png_bytes(size: int) -> bytes:
    pixmap = build_pixmap(size)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


def main() -> None:
    app = QApplication.instance() or QApplication([])
    images = [(size, _png_bytes(size)) for size in SIZES]

    # ICONDIR: reserved(2)=0, type(2)=1 (Icon), count(2)
    header = struct.pack("<HHH", 0, 1, len(images))

    entries = b""
    data = b""
    offset = len(header) + len(images) * 16  # 16 Byte je ICONDIRENTRY
    for size, png in images:
        dim = size if size < 256 else 0  # 256px wird laut Format als 0 kodiert
        entries += struct.pack(
            "<BBBBHHII",
            dim, dim,  # width, height
            0, 0,  # colorCount, reserved
            1, 32,  # planes, bitCount
            len(png), offset,  # bytesInRes, imageOffset
        )
        data += png
        offset += len(png)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_bytes(header + entries + data)
    print(f"App-Icon geschrieben: {OUT_PATH}")


if __name__ == "__main__":
    main()
