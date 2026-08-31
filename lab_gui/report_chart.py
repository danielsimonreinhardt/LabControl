"""Rendert ein Messwert-Diagramm eines Testlaufs als QImage, fuer den Einbau
als PNG in den Nachlauf-Report (siehe run_report.py).

Bewusst kein Widget und keine Abhaengigkeit von timeline_tab._ScopeChart --
der Report soll unabhaengig von einer sichtbaren GUI (auch aus einem
Hintergrundthread heraus, falls das spaeter noetig wird) und unabhaengig vom
aktuell aktiven App-Theme immer gleich aussehen (fester heller Print-Look).
Textzeichnen per QPainter setzt eine laufende QGuiApplication voraus -- beim
Aufruf aus der GUI (main_window.py) ist das immer gegeben.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from i18n import tr
from recording import Sample
from timeline_tab import SERIES_COLORS

CHART_W = 900
CHART_H = 260

BG = "#ffffff"
GRID = "#e5e7eb"
TEXT = "#1e2530"
MUTED = "#6b7280"
MARKER = "#9ca3af"

# Ab wie vielen Schrittmarkern die Marker weggelassen werden (Schleifenlaeufe
# wuerden das Diagramm sonst mit Strichen zupflastern).
MAX_STEP_MARKS = 40


def fmt_elapsed(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def render_field_chart(
    field: str,
    unit: str,
    samples: list[Sample],
    device_meta: dict[str, tuple[str, str]],
    t0: float,
    t1: float,
    step_marks: list[tuple[float, int]],
    width: int = CHART_W,
    height: int = CHART_H,
) -> QImage:
    """Zeichnet den Verlauf von `field` (bereits gefilterte samples) aller
    beteiligten Geraete zwischen t0 und t1 (Wanduhr-Sekunden, time.time())."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(BG))

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    left_margin, right_margin, top_margin, bottom_margin = 52, 16, 28, 26
    rect = QRectF(left_margin, top_margin, width - left_margin - right_margin,
                   height - top_margin - bottom_margin)

    t_hi = t1 if t1 > t0 else t0 + 1.0
    t_lo = t0

    by_device: dict[str, list[tuple[float, float]]] = {}
    for s in samples:
        if s.field != field:
            continue
        by_device.setdefault(s.device_id, []).append((s.t, s.value))
    for points in by_device.values():
        points.sort(key=lambda p: p[0])

    device_ids = sorted(by_device, key=lambda did: device_meta.get(did, ("", did))[1])

    # Gitter
    painter.setPen(QPen(QColor(GRID)))
    for i in range(5):
        y = rect.top() + i * rect.height() / 4
        painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
    painter.drawRect(rect)

    painter.setPen(QPen(QColor(TEXT)))
    painter.drawText(0, 0, left_margin - 4, top_margin, Qt.AlignmentFlag.AlignRight, f"[{unit}]")

    if not device_ids:
        painter.setPen(QPen(QColor(MUTED)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("Keine Messwerte"))
        painter.end()
        return image

    all_values = [v for points in by_device.values() for _t, v in points]
    lo, hi = min(all_values), max(all_values)
    if hi <= lo:
        pad = abs(lo) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    else:
        pad = (hi - lo) * 0.1
        lo, hi = lo - pad, hi + pad

    def x_of(t: float) -> float:
        return rect.left() + (t - t_lo) / (t_hi - t_lo) * rect.width()

    def y_of(value: float) -> float:
        frac = (value - lo) / (hi - lo)
        return rect.bottom() - frac * rect.height()

    # y-Achsenbeschriftung
    painter.setPen(QPen(QColor(MUTED)))
    for i in range(5):
        frac = 1.0 - i / 4
        y = rect.top() + i * rect.height() / 4
        value = lo + frac * (hi - lo)
        painter.drawText(0, int(y) - 7, left_margin - 6, 14, Qt.AlignmentFlag.AlignRight, f"{value:.2f}")

    # x-Achsenbeschriftung (verstrichene Zeit seit Laufstart)
    for i in range(5):
        frac = i / 4
        x = rect.left() + frac * rect.width()
        elapsed = frac * (t_hi - t_lo)
        painter.drawText(int(x) - 30, int(rect.bottom()) + 4, 60, 16, Qt.AlignmentFlag.AlignCenter, fmt_elapsed(elapsed))

    # Schrittmarker
    if 0 < len(step_marks) <= MAX_STEP_MARKS:
        marker_pen = QPen(QColor(MARKER))
        marker_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(marker_pen)
        for t, step_no in step_marks:
            if not (t_lo <= t <= t_hi):
                continue
            x = x_of(t)
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
            painter.setPen(QPen(QColor(MUTED)))
            painter.drawText(int(x) + 2, int(rect.top()) + 10, str(step_no))
            painter.setPen(marker_pen)

    # Serien: min/max-Dezimierung je Pixelspalte, falls mehr Punkte als
    # verfuegbare Spaltenbreite vorhanden sind (siehe Moduldoc).
    pixel_width = max(1, int(rect.width()))
    labels = [device_meta.get(did, ("", did))[1] for did in device_ids]
    max_label_width = max((painter.fontMetrics().horizontalAdvance(lbl) for lbl in labels), default=0)
    legend_x = rect.right() - max_label_width - 18
    legend_y = rect.top() + 4.0
    for i, device_id in enumerate(device_ids):
        color = QColor(SERIES_COLORS[i % len(SERIES_COLORS)])
        points = by_device[device_id]
        pen = QPen(color)
        pen.setWidth(2)
        painter.setPen(pen)

        if len(points) <= pixel_width:
            prev = None
            for t, v in points:
                if not (t_lo <= t <= t_hi):
                    continue
                p = (x_of(t), y_of(v))
                if prev is not None:
                    painter.drawLine(int(prev[0]), int(prev[1]), int(p[0]), int(p[1]))
                prev = p
        else:
            span = t_hi - t_lo
            bucket_min: list[float | None] = [None] * pixel_width
            bucket_max: list[float | None] = [None] * pixel_width
            for t, v in points:
                if t < t_lo or t > t_hi:
                    continue
                idx = min(max(int((t - t_lo) / span * pixel_width), 0), pixel_width - 1)
                if bucket_min[idx] is None or v < bucket_min[idx]:
                    bucket_min[idx] = v
                if bucket_max[idx] is None or v > bucket_max[idx]:
                    bucket_max[idx] = v
            for idx in range(pixel_width):
                if bucket_min[idx] is None:
                    continue
                x = rect.left() + idx
                painter.drawLine(int(x), int(y_of(bucket_min[idx])), int(x), int(y_of(bucket_max[idx])))

        # Legende
        label = labels[i]
        painter.fillRect(int(legend_x), int(legend_y) + i * 14, 10, 10, color)
        painter.setPen(QPen(QColor(TEXT)))
        painter.drawText(int(legend_x) + 14, int(legend_y) + i * 14 + 9, label)

    painter.end()
    return image
