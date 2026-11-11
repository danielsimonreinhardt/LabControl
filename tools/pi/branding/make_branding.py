"""Erzeugt das LabControl-Logo und die Boot-/Hintergrundbilder fuer den Raspberry Pi.

Ausgabe (neben diesem Skript):
  logo-mark.svg      Bildmarke (Messbildschirm mit Sprungantwort)
  logo.svg           Bildmarke + Schriftzug, quer
  splash.png         1024 x 600, Boot-Logo (Plymouth-Theme) und Desktop-Hintergrund am Pi
  logo-mark-512.png  Bildmarke einzeln (z. B. fuer ein spaeteres App-Symbol)

Farben aus dem App-Theme "Slate Ops Dark" (lab_gui/theme.py), damit Boot, Hintergrund und App
ineinander uebergehen. Gerendert mit QtSvg (PySide6), keine weitere Bibliothek.

    python tools/pi/branding/make_branding.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

BG = "#0f172a"          # Slate Ops Dark: bg
SURFACE = "#1b2336"     # surface
ACCENT = "#38bdf8"      # accent (Himmelblau)
TEXT = "#f8fafc"        # text
MUTED = "#94a3b8"       # gedaempft

# Bildmarke im Raster 168 x 124: Messbildschirm (Standfuss und Polklemmen am 11.11.2026 auf Wunsch entfernt).
# Die Kurve ist eine Sprungantwort mit gedaempftem Ueberschwingen -- Messen und Regeln in einem Zeichen.
MARK = f"""
  <rect x="3" y="3" width="162" height="118" rx="20" fill="{SURFACE}" stroke="{ACCENT}" stroke-width="6"/>
  <path d="M28 41 H140 M28 62 H140 M28 83 H140" stroke="{ACCENT}" stroke-opacity="0.16" stroke-width="2"/>
  <path d="M62 24 V100 M106 24 V100" stroke="{ACCENT}" stroke-opacity="0.16" stroke-width="2"/>
  <path d="M22 92 H54 L60 36 C64 22 72 24 76 40 C80 56 86 60 92 48 C97 38 102 38 107 45
           C111 51 115 51 120 47 C125 43 129 44 146 45"
        fill="none" stroke="{ACCENT}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="146" cy="45" r="7" fill="{TEXT}"/>
"""
MARK_W, MARK_H = 168, 124   # Bildschirm samt Rahmenstrich

# Eine Familie je Element (QtSvg nimmt keine Ausweichliste); gerendert wird unter Windows (Segoe UI).
FONT = "Segoe UI"


def mark_svg() -> str:
    """Quadratisch (App-Symbol), Bildschirm senkrecht mittig."""
    dy = (MARK_W - MARK_H) / 2
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {MARK_W} {MARK_W}" width="{MARK_W}"'
            f' height="{MARK_W}"><g transform="translate(0 {dy})">{MARK}</g></svg>')


def logo_svg() -> str:
    """Bildmarke links, Schriftzug rechts (fuer Dokumente, Freigabe-Webseite)."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 168" width="640" height="168">'
            f'<g transform="translate(0 {(168 - MARK_H) / 2})">{MARK}</g>'
            f'<text x="196" y="104" font-family="{FONT}" font-weight="300" font-size="68" fill="{TEXT}">Lab'
            f'<tspan font-weight="600" fill="{ACCENT}">Control</tspan></text>'
            f'</svg>')


def splash_svg(width: int = 1024, height: int = 600) -> str:
    """Vollbild fuer Boot und Hintergrund: Bildmarke mittig, Schriftzug darunter, dezente Zeile."""
    scale = 176 / MARK_W
    mx = (width - MARK_W * scale) / 2
    my = height * 0.5 - MARK_H * scale - 4   # Bildschirm endet knapp ueber der Mitte, Schriftzug darunter
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
            f'<rect width="{width}" height="{height}" fill="{BG}"/>'
            f'<g transform="translate({mx} {my}) scale({scale})">{MARK}</g>'
            f'<text x="{width / 2}" y="{height * 0.5 + 82}" text-anchor="middle" font-family="{FONT}"'
            f' font-weight="300" font-size="60" fill="{TEXT}">Lab<tspan font-weight="600" fill="{ACCENT}">Control</tspan></text>'
            f'<text x="{width / 2}" y="{height * 0.5 + 122}" text-anchor="middle" font-family="{FONT}"'
            f' font-weight="600" font-size="17" letter-spacing="3" fill="{MUTED}">MESSEN · STEUERN · PRÜFEN</text>'
            f'</svg>')


def render(svg: str, path: Path, width: int, height: int) -> None:
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    painter.end()
    image.save(str(path))


def main() -> int:
    if sys.platform != "win32":   # Windows: normale Plattform, offscreen hat dort keine Schriften
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 (Schriften brauchen eine App)
    (HERE / "logo-mark.svg").write_text(mark_svg(), encoding="utf-8")
    (HERE / "logo.svg").write_text(logo_svg(), encoding="utf-8")
    render(splash_svg(), HERE / "splash.png", 1024, 600)
    render(mark_svg(), HERE / "logo-mark-512.png", 512, 512)
    print("geschrieben:", ", ".join(p.name for p in sorted(HERE.glob("*.png")) + sorted(HERE.glob("*.svg"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
