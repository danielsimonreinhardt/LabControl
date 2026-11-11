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

Seit 0.42.0 dasselbe Logo wie das Boot-Logo am Raspberry Pi (lab_gui/brand.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lab_gui"))

import brand  # noqa: E402
from version import __version__  # noqa: E402

OUT_PATH = REPO_ROOT / "lab_gui" / "icons" / "splash.png"


def main() -> None:
    app = QApplication.instance() or QApplication([])  # noqa: F841 (Schriften brauchen eine App)
    image = brand.render_image(brand.windows_splash_svg(__version__), brand.WIN_SPLASH_W, brand.WIN_SPLASH_H)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(OUT_PATH), "PNG")
    print(f"Splash-Bild geschrieben: {OUT_PATH}")


if __name__ == "__main__":
    main()
