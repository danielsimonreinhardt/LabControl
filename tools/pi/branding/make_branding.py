"""Erzeugt das LabControl-Logo und die Boot-/Hintergrundbilder fuer den Raspberry Pi.

Ausgabe (neben diesem Skript):
  logo-mark.svg      Bildmarke (Messbildschirm mit Sprungantwort)
  logo.svg           Bildmarke + Schriftzug, quer
  splash.png         1024 x 600, Boot-Logo (Plymouth-Theme) und Desktop-Hintergrund am Pi
  logo-mark-512.png  Bildmarke einzeln

Das Logo selbst steht seit 0.42.0 in lab_gui/brand.py -- dieselbe Quelle wie Splash und Symbol der
Windows-.exe. Gerendert mit QtSvg (PySide6) unter Windows (Schrift Segoe UI), keine weitere Bibliothek.

    python tools/pi/branding/make_branding.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent / "lab_gui"))

import brand  # noqa: E402


def main() -> int:
    if sys.platform != "win32":   # Windows: normale Plattform, offscreen hat dort keine Schriften
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 (Schriften brauchen eine App)
    (HERE / "logo-mark.svg").write_text(brand.mark_svg(), encoding="utf-8")
    (HERE / "logo.svg").write_text(brand.logo_svg(), encoding="utf-8")
    brand.render_image(brand.boot_splash_svg(), 1024, 600).save(str(HERE / "splash.png"))
    brand.render_image(brand.mark_svg(), 512, 512).save(str(HERE / "logo-mark-512.png"))
    print("geschrieben:", ", ".join(p.name for p in sorted(HERE.glob("*.png")) + sorted(HERE.glob("*.svg"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
