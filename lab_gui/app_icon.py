"""LabControl-Anwendungssymbol: die Bildmarke aus brand.py (Messbildschirm mit Sprungantwort), dieselbe
wie im Boot-Logo am Pi und im Splash der .exe (seit 0.42.0; vorher ein amberfarbenes "L").

Von zwei Stellen genutzt:
  - main.py setzt damit zur Laufzeit das Fenstersymbol (QApplication.setWindowIcon) -- das ist es,
    was Windows fuer das Taskleisten-Symbol einer laufenden Anwendung heranzieht.
  - tools/generate_icon.py rendert dieselben Pixmaps in eine .ico-Datei, die LabControl.spec als
    EXE(icon=...) einbettet -- das zeigen Explorer/Taskleiste fuer die .exe selbst (nicht gestartet,
    angeheftet). Beide Pfade muessen zusammenpassen, sonst wechselt das Symbol beim Start sichtbar.
"""
from __future__ import annotations

from PySide6.QtGui import QIcon, QPixmap

import brand

# Fenstersymbol/Taskleiste braucht mehrere Aufloesungen (16px Titelleiste bis 256px High-DPI/Sprunglisten) --
# Qt bzw. der Windows-.ico-Container waehlt je nach Kontext die passendste aus.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_pixmap(size: int) -> QPixmap:
    return QPixmap.fromImage(brand.icon_image(size))


def icon() -> QIcon:
    result = QIcon()
    for size in SIZES:
        result.addPixmap(build_pixmap(size))
    return result
