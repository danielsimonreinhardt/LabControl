"""LabControl-Logo: eine Quelle fuer alle Stellen, an denen es erscheint (seit 0.42.0, 11.11.2026).

  - Boot-Logo und Hintergrund am Raspberry Pi  (tools/pi/branding/make_branding.py -> splash.png)
  - Splash der Windows-.exe                     (tools/generate_splash.py -> lab_gui/icons/splash.png)
  - Fenster-/Taskleisten-Symbol zur Laufzeit    (app_icon.py, QApplication.setWindowIcon)
  - Symbol der .exe-Datei                        (tools/generate_icon.py -> lab_gui/icons/app_icon.ico)

Bildmarke: ein Messbildschirm mit Sprungantwort (gedaempftes Ueberschwingen) -- Messen, Steuern, Pruefen in
einem Zeichen. Farben aus "Slate Ops Dark" (theme.py), fest verdrahtet, weil Splash und Symbol vor dem
ThemeManager entstehen. Als SVG beschrieben und mit QtSvg gerendert, keine Bilddateien als Quelle.
"""
from __future__ import annotations

BG = "#0f172a"        # Slate Ops Dark: bg
SURFACE = "#1b2336"   # surface
BORDER = "#475569"    # border
ACCENT = "#38bdf8"    # accent (Himmelblau)
TEXT = "#f8fafc"      # text
MUTED = "#94a3b8"     # gedaempft

TAGLINE = "MESSEN · STEUERN · PRÜFEN"
FONT = "Segoe UI"     # QtSvg nimmt keine Ausweichliste; gerendert wird unter Windows

# Raster 168 x 124: Bildschirm samt Rahmenstrich.
MARK_W, MARK_H = 168, 124

MARK = f"""
  <rect x="3" y="3" width="162" height="118" rx="20" fill="{SURFACE}" stroke="{ACCENT}" stroke-width="6"/>
  <path d="M28 41 H140 M28 62 H140 M28 83 H140" stroke="{ACCENT}" stroke-opacity="0.16" stroke-width="2"/>
  <path d="M62 24 V100 M106 24 V100" stroke="{ACCENT}" stroke-opacity="0.16" stroke-width="2"/>
  <path d="M22 92 H54 L60 36 C64 22 72 24 76 40 C80 56 86 60 92 48 C97 38 102 38 107 45
           C111 51 115 51 120 47 C125 43 129 44 146 45"
        fill="none" stroke="{ACCENT}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="146" cy="45" r="7" fill="{TEXT}"/>
"""

# Fuer 16/24 px (Titelleiste, kleine Taskleiste): ohne Gitter, dickere Striche, ruhigere Kurve --
# sonst verschwimmt das Zeichen zu einem blauen Fleck.
MARK_SMALL = f"""
  <rect x="7" y="7" width="154" height="110" rx="24" fill="{SURFACE}" stroke="{ACCENT}" stroke-width="14"/>
  <path d="M30 88 H56 L66 38 C72 24 82 30 88 50 C94 66 104 52 116 48 H140"
        fill="none" stroke="{ACCENT}" stroke-width="15" stroke-linecap="round" stroke-linejoin="round"/>
"""
SMALL_ICON_MAX = 24


def _svg(view_w: float, view_h: float, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view_w} {view_h}"'
            f' width="{view_w}" height="{view_h}">{body}</svg>')


def wordmark(x: float, y: float, size: float, anchor: str = "start") -> str:
    """'Lab' leicht in Textfarbe, 'Control' kraeftig im Akzent -- Grundlinie bei y."""
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-family="{FONT}" font-weight="300"'
            f' font-size="{size}" fill="{TEXT}">Lab<tspan font-weight="600" fill="{ACCENT}">Control</tspan></text>')


def tagline(x: float, y: float, size: float, spacing: float) -> str:
    return (f'<text x="{x}" y="{y}" text-anchor="middle" font-family="{FONT}" font-weight="600"'
            f' font-size="{size}" letter-spacing="{spacing}" fill="{MUTED}">{TAGLINE}</text>')


def mark_svg(small: bool = False) -> str:
    """Quadratisch (Symbol), Bildschirm senkrecht mittig."""
    dy = (MARK_W - MARK_H) / 2
    return _svg(MARK_W, MARK_W, f'<g transform="translate(0 {dy})">{MARK_SMALL if small else MARK}</g>')


def logo_svg() -> str:
    """Bildmarke links, Schriftzug rechts (Dokumente, Freigabe-Webseite)."""
    return _svg(640, 168, f'<g transform="translate(0 {(168 - MARK_H) / 2})">{MARK}</g>' + wordmark(196, 104, 68))


def boot_splash_svg(width: int = 1024, height: int = 600) -> str:
    """Pi: Vollbild fuer Boot und Hintergrund -- Bildmarke mittig, Schriftzug und Unterzeile darunter."""
    scale = 176 / MARK_W
    mx = (width - MARK_W * scale) / 2
    my = height * 0.5 - MARK_H * scale - 4   # Bildschirm endet knapp ueber der Mitte
    return _svg(width, height,
                f'<rect width="{width}" height="{height}" fill="{BG}"/>'
                f'<g transform="translate({mx} {my}) scale({scale})">{MARK}</g>'
                + wordmark(width / 2, height * 0.5 + 82, 60, "middle")
                + tagline(width / 2, height * 0.5 + 122, 17, 3))


# Windows-.exe: PyInstaller-Splash. Unten links blendet main.py den Ladetext ein (LabControl.spec:
# Splash(text_pos=WIN_SPLASH_TEXT_POS)), unten rechts steht die Version.
WIN_SPLASH_W, WIN_SPLASH_H = 480, 280
WIN_SPLASH_TEXT_POS = (20, 262)


def windows_splash_svg(version: str) -> str:
    w, h = WIN_SPLASH_W, WIN_SPLASH_H
    scale = 104 / MARK_W
    mx = (w - MARK_W * scale) / 2
    return _svg(w, h,
                f'<rect width="{w}" height="{h}" fill="{BG}"/>'
                f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" fill="none" stroke="{BORDER}"/>'
                f'<g transform="translate({mx} 30) scale({scale})">{MARK}</g>'
                + wordmark(w / 2, 162, 40, "middle")
                + tagline(w / 2, 190, 11, 2)
                + f'<text x="{w - 20}" y="{WIN_SPLASH_TEXT_POS[1]}" text-anchor="end" font-family="{FONT}"'
                  f' font-size="12" fill="{MUTED}">v{version}</text>')


def render_image(svg: str, width: int, height: int):
    """SVG -> QImage (ARGB, transparent). Braucht eine laufende Q(Gui)Application."""
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    painter.end()
    return image


def icon_image(size: int):
    """Symbol in einer Groesse; bis SMALL_ICON_MAX die vereinfachte Bildmarke."""
    return render_image(mark_svg(small=size <= SMALL_ICON_MAX), size, size)
