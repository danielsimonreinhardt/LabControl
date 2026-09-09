"""Gemeinsame Platzierungslogik fuer feste Kachel-Raster (Control-Tab und
Dashboard-Tab, siehe FEATURES.md Punkt 4/5). Reine Funktionen ohne
Qt-Abhaengigkeit (bis auf make_drag_pixmap), damit sie unabhaengig von der
jeweiligen Wachstumsrichtung wiederverwendbar bleiben: der Control-Tab ist
spaltenbegrenzt (feste Breite) und waechst nach unten, der Dashboard-Tab ist
zeilenbegrenzt (max. doppelte Hoehe) und waechst nach rechts -- mathematisch
dasselbe Problem, nur transponiert (siehe pack_tiles_by_row).

Die Drag&Drop-MAUS-MECHANIK selbst (Event-Filter, Press/Move/Release-
Zustandsmaschine) bleibt bewusst je Tab in control_tab.py/dashboard.py
dupliziert statt hier als Mixin zusammengefasst -- nur das schwebende
Drag-Abbild (rein optisch, zustandslos) wird hier geteilt.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

# Drag & Drop: eigener MIME-Typ statt text/plain, damit dragEnterEvent
# zuverlaessig nur eigene Kachel-Drags akzeptiert und nichts, das zufaellig
# aus einer anderen Anwendung hereingezogen wird. Von beiden Tabs geteilt,
# damit ein aus dem Dashboard gezogenes Panel nicht versehentlich als
# Control-Tab-Kachel-Drop interpretiert wird (unterschiedliche Nutzlast-
# Semantik je Tab trotz gleicher Kodierung waere sonst ein stiller Bug).
PANEL_DRAG_MIME_TYPE = "application/x-lab-gui-panel-device-id"
CONTROL_TILE_DRAG_MIME_TYPE = "application/x-lab-gui-control-tile-device-id"
# Nur der obere Streifen (Rahmentitel) startet einen Drag -- siehe
# PANEL_DRAG_HANDLE_HEIGHT-Docstring in der bisherigen dashboard.py-Fassung:
# Hoehe orientiert sich an OFFLINE_ICON_MARGIN/-SIZE (5+22=27), die dieselbe
# Zone bereits als "Kopfbereich" behandeln.
PANEL_DRAG_HANDLE_HEIGHT = 28
# Optik des schwebenden Abbilds waehrend des Ziehens (Nutzerfeedback: ein
# direkt durchgereichtes panel.grab() erschien beim Ziehen deutlich
# vergroessert). Fester Blauton statt palette.accent: die Akzentfarbe ist im
# Amber-Industrial-Theme selbst gelb/amber und auf einer ebenfalls
# amberfarbenen Kachel kaum zu erkennen, waehrend Blau in beiden Themes
# zuverlaessig als "hier wird gerade gezogen" heraussticht.
PANEL_DRAG_BORDER_COLOR = "#2f7dfd"
PANEL_DRAG_OPACITY = 0.55


def make_drag_pixmap(widget: QWidget, border_color: str = PANEL_DRAG_BORDER_COLOR, opacity: float = PANEL_DRAG_OPACITY) -> QPixmap:
    """Baut das schwebende Abbild waehrend des Ziehens: leicht durchsichtig
    mit farbigem Rahmen, in EXAKT der Groesse von `widget` -- ein direkt an
    QDrag.setPixmap() durchgereichtes widget.grab() erschien beim Ziehen
    deutlich vergroessert, vermutlich weil Qts Drag-Compositing das
    devicePixelRatio von grab() (bei Windows-Anzeigeskalierung > 100%) nicht
    korrekt beruecksichtigt. Ein selbst zusammengesetztes Pixmap mit explizit
    auf 1.0 gesetztem devicePixelRatio umgeht das zuverlaessig, unabhaengig
    vom Skalierungsfaktor des Bildschirms."""
    size = widget.size()
    pixmap = QPixmap(size)
    pixmap.setDevicePixelRatio(1.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(opacity)
    widget.render(painter, QPoint(0, 0))
    painter.setOpacity(1.0)
    pen = QPen(QColor(border_color))
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(1, 1, size.width() - 2, size.height() - 2), 6, 6)
    painter.end()
    return pixmap


def pack_tiles(order: list[str], spans: dict[str, tuple[int, int]], max_cols: int) -> dict[str, tuple[int, int]]:
    """Packt `order` (Geraete-IDs in Wunschreihenfolge) dicht in ein Raster
    mit `max_cols` Spalten, Zeilen wachsen beliebig nach unten.
    `spans[device_id]` ist (col_span, row_span) in Rastereinheiten.
    Rueckgabe: device_id -> (row, col) der oberen linken Zelle.

    Greedy "dense packing" wie bei CSS-Grid auto-flow: fuer jede Kachel wird
    in Lesereihenfolge (Zeile, dann Spalte) die erste freie Stelle gesucht,
    in die ihre Spanne passt -- Luecken kleinerer/frueherer Kacheln werden
    dabei mit aufgefuellt (Voraussetzung fuer "zwei einfache Kacheln
    uebereinander", siehe FEATURES.md Punkt 5 und pack_tiles_by_row unten).
    """
    occupied: set[tuple[int, int]] = set()
    positions: dict[str, tuple[int, int]] = {}
    max_cols = max(1, max_cols)
    for device_id in order:
        col_span, row_span = spans.get(device_id, (1, 1))
        col_span = min(col_span, max_cols)
        row = 0
        while device_id not in positions:
            for col in range(0, max_cols - col_span + 1):
                cells = [(row + dr, col + dc) for dr in range(row_span) for dc in range(col_span)]
                if not any(cell in occupied for cell in cells):
                    occupied.update(cells)
                    positions[device_id] = (row, col)
                    break
            row += 1
    return positions


def pack_tiles_by_row(order: list[str], spans: dict[str, tuple[int, int]], max_rows: int) -> dict[str, tuple[int, int]]:
    """Wie pack_tiles, aber mit fester ZEILENzahl (waechst nach rechts) --
    fuer den Dashboard-Tab (doppelte Hoehe als maximale vertikale
    Ausdehnung, siehe FEATURES.md Punkt 5). Transponiert Spannweiten/
    Ergebnis intern auf pack_tiles zurueck, da beide Faelle mathematisch
    identisch sind (Zeilen <-> Spalten vertauscht)."""
    swapped_spans = {device_id: (row_span, col_span) for device_id, (col_span, row_span) in spans.items()}
    swapped_positions = pack_tiles(order, swapped_spans, max_rows)
    return {device_id: (col, row) for device_id, (row, col) in swapped_positions.items()}


def cell_size_ratchet(current: int, sizes: list[tuple[int, int]]) -> int:
    """Ratchet-Update (waechst nur, wie DashboardWidget._panel_width bereits
    heute) einer Rasterzellen-Basisgroesse (Breite ODER Hoehe) aus einer
    Liste von (natuerliche sizeHint()-Ausdehnung, Spannweite in Zellen)-
    Paaren -- stellt sicher, dass die Standard-Zellgroesse jede Kachel im
    Normalfall bequem fasst, auch bei einer Spannweite > 1 (aufgerundetes
    Verhaeltnis natuerliche Groesse / Spannweite)."""
    needed = max((-(-size // span) for size, span in sizes if span > 0), default=0)
    return max(current, needed)
