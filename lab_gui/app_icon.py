"""LabControl-Anwendungssymbol: ein amberfarbenes "L" auf dunklem Grund,
im selben "Amber Industrial"-Look wie der Bootloader-Splash (siehe
tools/generate_splash.py -- gleiche Farbwerte BG/ACCENT/TEXT).

Per QPainter code-generiert statt als statische PNG/ICO-Datei gepflegt,
demselben Prinzip folgend wie die Icons in icons.py/theme.py ("vermeidet
Extra-Assets im Repo/.spec"). Von zwei Stellen genutzt:
  - main.py setzt damit zur Laufzeit das Fenstersymbol (QApplication.
    setWindowIcon) -- das ist es, was Windows fuer das Taskleisten-Symbol
    einer laufenden Anwendung heranzieht.
  - tools/generate_icon.py rendert dieselben Pixmaps in eine .ico-Datei,
    die LabControl.spec als EXE(icon=...) einbettet -- das ist es, was
    Explorer/Taskleiste fuer die .exe selbst (nicht gestartet, angeheftet)
    anzeigt. Beide Pfade muessen zusammenpassen, sonst wechselt das Symbol
    beim Start sichtbar.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap

# Identisch zu tools/generate_splash.py (dort erklaert: fest verdrahtet statt
# aus theme.py importiert, da dieses Symbol schon vor ThemeManager existiert).
BG = QColor("#14171c")
ACCENT = QColor("#ff9f1c")
TEXT = QColor("#e8e6e1")

# Fenstersymbol/Taskleiste braucht mehrere Aufloesungen (16px Titelleiste bis
# 256px High-DPI/Sprunglisten) -- Qt bzw. der Windows-.ico-Container waehlt
# je nach Kontext die passendste aus einer QIcon mit mehreren Pixmaps aus.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_pixmap(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    radius = size * 0.22
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setClipPath(path)
    painter.fillPath(path, BG)

    # Amberfarbener Streifen am unteren Rand, wie die Akzentleiste im Splash.
    bar_height = max(1, round(size * 0.10))
    painter.fillRect(0, size - bar_height, size, bar_height, ACCENT)

    font = QFont("Segoe UI", -1, QFont.Weight.Bold)
    font.setPixelSize(round(size * 0.62))
    painter.setFont(font)
    painter.setPen(TEXT)
    painter.drawText(QRectF(0, 0, size, size - bar_height), Qt.AlignmentFlag.AlignCenter, "L")

    painter.end()
    return pixmap


def icon() -> QIcon:
    result = QIcon()
    for size in SIZES:
        result.addPixmap(build_pixmap(size))
    return result
