"""Erzeugt lab_gui/icons/splash.png fuer den PyInstaller-Bootloader-Splash
(siehe LabControl.spec: Splash(...)).

Nicht Teil der laufenden App. Wird von LabControl.spec vor jedem Build
automatisch aufgerufen (main() importiert und ausgefuehrt), damit das
Splash-Bild nie von der in version.py eingetragenen Versionsnummer
abweichen kann (siehe BUGS.md #13 -- vorher ein rein manueller Schritt, der
beim v0.9.0->v0.9.3-Bump vergessen wurde und eine veraltete Versionsnummer
im Splash zeigte). Laesst sich weiterhin auch direkt ausfuehren, z.B. um das
Bild-Design isoliert zu pruefen, ohne einen kompletten Build anzustossen:

    python tools/generate_splash.py

Nutzt PySide6/QPainter statt einer neuen Bild-Bibliothek (Pillow o.ae.), da
PySide6 ohnehin Kernabhaengigkeit der App ist.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lab_gui"))

from version import __version__  # noqa: E402

OUT_PATH = REPO_ROOT / "lab_gui" / "icons" / "splash.png"
WIDTH, HEIGHT = 480, 280

# Amber-Industrial-Farbwerte (siehe theme.py: AMBER_DARK) fest verdrahtet
# statt importiert -- der Splash erscheint bereits vor Settings/ThemeManager,
# lange bevor die App-eigene Theme-Wahl geladen ist.
BG = QColor("#14171c")
ACCENT = QColor("#ff9f1c")
TEXT = QColor("#e8e6e1")


def main() -> None:
    app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(WIDTH, HEIGHT)
    pixmap.fill(BG)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    painter.setPen(ACCENT)
    painter.drawRect(0, 0, WIDTH - 1, HEIGHT - 1)
    painter.fillRect(QRect(0, HEIGHT - 6, WIDTH, 6), ACCENT)

    painter.setPen(TEXT)
    title_font = QFont("Segoe UI", 28, QFont.Weight.Bold)
    painter.setFont(title_font)
    painter.drawText(QRect(0, 90, WIDTH, 50), Qt.AlignmentFlag.AlignCenter, "LabControl")

    version_font = QFont("Segoe UI", 12)
    painter.setFont(version_font)
    painter.setPen(ACCENT)
    painter.drawText(QRect(0, 140, WIDTH, 30), Qt.AlignmentFlag.AlignCenter, f"v{__version__}")

    # Unterer Bereich bleibt frei fuer den dynamischen Ladetext, den
    # pyi_splash.update_text() zur Laufzeit dort einblendet (siehe
    # main.py: text_pos in der Splash(...)-Deklaration im .spec).
    painter.end()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(OUT_PATH), "PNG")
    print(f"Splash-Bild geschrieben: {OUT_PATH}")


if __name__ == "__main__":
    main()
