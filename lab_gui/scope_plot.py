"""Zeichnet eine Oszilloskop-Erfassung (scope_api.base.Capture) als PNG.

Fuer das MCP-Werkzeug plot_capture (der Assistent SIEHT die Kurve, statt nur
Zahlen und Huellkurve zu lesen) und fuer den Knopf "Kurve" der
Oszilloskop-Kachel. Mit QPainter statt matplotlib: Qt bringt die App ohnehin
mit, matplotlib wuerde die .exe um viele MB vergroessern. Deshalb liegt das
hier in lab_gui/ und nicht im Qt-freien scope_api/.

QImage/QPainter duerfen ausserhalb des GUI-Threads zeichnen (die HTTP-Server-
Threads tun das), solange eine QGuiApplication existiert -- in der App immer.

Aufbau wie am Oszilloskop: 10 x 8 Raster, t = 0 am Trigger (senkrechte
Strichlinie), Triggerpegel als waagerechte Strichlinie in der Farbe der
Quelle. Die Spannungsachse ist EINE gemeinsame Achse in V, automatisch auf die
Daten skaliert (fuer die Auswertung wichtiger als die eingestellten Bereiche,
die in der Legende stehen). Je Pixelspalte werden Min und Max gezeichnet --
kurze Spitzen gehen dadurch nicht verloren.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

from scope_api import analysis
from scope_api.base import Capture, ScopeError

# Kanalfarben wie in PicoScope 7 (A blau, B rot, C gruen, D gelb).
CHANNEL_COLORS = {"A": "#1f5fbf", "B": "#c0392b", "C": "#2e8b57", "D": "#c9a100"}
BACKGROUND = "#ffffff"
GRID = "#d9dee3"
GRID_CENTER = "#b7c0c9"
TEXT = "#18212a"
MUTED = "#5a6672"

DEFAULT_WIDTH, DEFAULT_HEIGHT = 1000, 560
MIN_WIDTH, MAX_WIDTH = 400, 2000
MIN_HEIGHT, MAX_HEIGHT = 250, 1200
DIVISIONS_X, DIVISIONS_Y = 10, 8

_PREFIXES = ((1e-9, "n"), (1e-6, "µ"), (1e-3, "m"), (1.0, ""), (1e3, "k"), (1e6, "M"))


def format_si(value: float | None, unit: str, digits: int = 4) -> str:
    """1000.04, "Hz" -> "1.000 kHz"; 2.56e-6, "s" -> "2.560 µs"."""
    if value is None or not math.isfinite(value):
        return "–"
    if value == 0:
        return f"0 {unit}"
    index = 0
    for i, (s, _p) in enumerate(_PREFIXES):
        if abs(value) >= s:
            index = i
    bumped = False
    while True:
        scale, prefix = _PREFIXES[index]
        scaled = value / scale
        magnitude = 1.0 if bumped else abs(scaled)
        decimals = max(0, digits - 1 - int(math.floor(math.log10(magnitude))))
        text = f"{scaled:.{decimals}f}"
        # 999.96 Hz wuerde auf "1000" gerundet -> naechste Vorsilbe ("1.000 kHz")
        if abs(float(text)) >= 1000 and index + 1 < len(_PREFIXES):
            index += 1
            bumped = True
            continue
        return f"{text} {prefix}{unit}"


def _nice_step(span: float, divisions: int) -> float:
    """Rasterabstand aus der 1-2-5-Reihe, damit Beschriftungen glatt sind."""
    raw = span / divisions
    if raw <= 0:
        return 1.0
    exponent = math.floor(math.log10(raw))
    for factor in (1, 2, 5, 10):
        step = factor * 10 ** exponent
        if step >= raw * 0.999:
            return step
    return 10 ** (exponent + 1)


def plot_png(capture: Capture, channels: list[str] | None = None, t_start: float | None = None,
             t_stop: float | None = None, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> bytes:
    names = [c.upper() for c in channels] if channels else list(capture.channels)
    unknown = [n for n in names if n not in capture.channels]
    if unknown:
        raise ScopeError("invalid_request", f"Kanal {', '.join(unknown)} nicht in der Erfassung.",
                         allowed=list(capture.channels))
    width = int(max(MIN_WIDTH, min(MAX_WIDTH, width)))
    height = int(max(MIN_HEIGHT, min(MAX_HEIGHT, height)))

    t = capture.time_s
    mask = np.ones(t.size, dtype=bool)
    if t_start is not None:
        mask &= t >= t_start
    if t_stop is not None:
        mask &= t <= t_stop
    if mask.sum() < 2:
        raise ScopeError("invalid_request", "Im Zeitfenster liegen weniger als 2 Samples.",
                         t_min=analysis.num(t[0]), t_max=analysis.num(t[-1]))
    t = t[mask]
    series = {n: capture.channels[n].volts[mask] for n in names}

    # -- Achsen ---------------------------------------------------------------
    t0, t1 = float(t[0]), float(t[-1])
    vmin = min(float(v.min()) for v in series.values())
    vmax = max(float(v.max()) for v in series.values())
    trig = capture.request.trigger
    show_level = trig.mode != "none" and trig.source in series
    if show_level:
        vmin, vmax = min(vmin, trig.level_v), max(vmax, trig.level_v)
    span = vmax - vmin
    if span < 1e-9:
        pad = max(abs(vmax) * 0.1, 0.01)
    else:
        pad = span * 0.08
    v_step = _nice_step(span + 2 * pad, DIVISIONS_Y)
    y_lo = math.floor((vmin - pad) / v_step) * v_step
    y_hi = y_lo + v_step * DIVISIONS_Y
    while y_hi < vmax + pad * 0.5:
        v_step = _nice_step(v_step * DIVISIONS_Y * 1.2, DIVISIONS_Y)
        y_lo = math.floor((vmin - pad) / v_step) * v_step
        y_hi = y_lo + v_step * DIVISIONS_Y

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(BACKGROUND))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    font = QFont("Segoe UI", 9)
    painter.setFont(font)
    fm = painter.fontMetrics()

    left, right = 70, 16
    top = 34 + fm.height() * len(names)
    bottom = 40
    plot = QRectF(left, top, width - left - right, height - top - bottom)

    def x_of(tv):
        return plot.left() + (tv - t0) / (t1 - t0) * plot.width()

    def y_of(vv):
        return plot.bottom() - (vv - y_lo) / (y_hi - y_lo) * plot.height()

    # -- Kopfzeilen -------------------------------------------------------------
    painter.setPen(QColor(TEXT))
    bold = QFont(font)
    bold.setBold(True)
    painter.setFont(bold)
    trig_text = ("freilaufend" if trig.mode == "none" else
                 f"Trigger {trig.mode} {trig.source} {'↑' if trig.edge == 'rising' else '↓'} "
                 f"{format_si(trig.level_v, 'V')}"
                 + ("" if capture.triggered is not False else " (nicht ausgelöst)"))
    # Wichtiges zuerst: bei schmalen Bildern wird hinten gekuerzt.
    header = (f"{trig_text}  ·  {format_si(capture.sample_interval_s, 's')}/Sample  ·  "
              f"{capture.model}  ·  {capture.capture_id or 'ohne ID'}  ·  {capture.timestamp[11:19]}")
    header = painter.fontMetrics().elidedText(header, Qt.TextElideMode.ElideRight, width - left - right)
    painter.drawText(QPointF(left, 18), header)
    painter.setFont(font)
    for i, name in enumerate(names):
        ch = capture.channels[name]
        values, _ = analysis.measure(t, series[name], ["vmin", "vmax", "vpp", "vrms", "frequency"],
                                     min_amplitude=analysis.noise_floor(ch.range_v))
        line = (f"{name}: ±{format_si(ch.range_v, 'V', 3)} {ch.coupling}"
                + (f" {ch.probe:g}:1" if ch.probe != 1 else "")
                + f"   min {format_si(values['vmin'], 'V')}   max {format_si(values['vmax'], 'V')}"
                + f"   Vss {format_si(values['vpp'], 'V')}   eff {format_si(values['vrms'], 'V')}"
                + f"   f {format_si(values['frequency'], 'Hz')}"
                + ("   ÜBERSTEUERT" if ch.overrange else ""))
        painter.setPen(QColor(CHANNEL_COLORS.get(name, TEXT)))
        painter.drawText(QPointF(left, 20 + fm.height() * (i + 1)), line)

    # -- Raster und Beschriftung --------------------------------------------------
    # Senkrechte Rasterlinien auf runden Zeiten (1-2-5-Reihe), an t = 0
    # ausgerichtet -- lesbarer als 10 starre Teile mit krummen Werten.
    t_step_label = _nice_step(t1 - t0, DIVISIONS_X)
    first = math.ceil(t0 / t_step_label - 1e-9)
    last = math.floor(t1 / t_step_label + 1e-9)
    for k in range(first, last + 1):
        tv = k * t_step_label
        x = x_of(tv)
        painter.setPen(QPen(QColor(GRID_CENTER if k == 0 else GRID), 1))
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        painter.setPen(QColor(MUTED))
        label = "0" if k == 0 else format_si(tv, "s", 3)
        painter.drawText(QRectF(x - 45, plot.bottom() + 4, 90, fm.height()),
                         Qt.AlignmentFlag.AlignHCenter, label)
    for j in range(DIVISIONS_Y + 1):
        v = y_lo + j * v_step
        y = y_of(v)
        painter.setPen(QPen(QColor(GRID_CENTER if abs(v) < v_step * 1e-6 else GRID), 1))
        painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        painter.setPen(QColor(MUTED))
        painter.drawText(QRectF(0, y - fm.height() / 2, left - 6, fm.height()),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, format_si(v, "V", 3))
    painter.setPen(QColor(MUTED))
    painter.drawText(QRectF(plot.left(), plot.bottom() + 4 + fm.height(), plot.width(), fm.height()),
                     Qt.AlignmentFlag.AlignHCenter,
                     f"Zeit (t = 0 am Trigger)  ·  {format_si(t_step_label, 's', 3)}/div  ·  "
                     f"{format_si(v_step, 'V', 3)}/div")

    # -- Trigger ----------------------------------------------------------------------
    dash = QPen(QColor(MUTED), 1, Qt.PenStyle.DashLine)
    if trig.mode != "none" and t0 <= 0.0 <= t1:
        painter.setPen(dash)
        painter.drawLine(QPointF(x_of(0.0), plot.top()), QPointF(x_of(0.0), plot.bottom()))
    if show_level:
        painter.setPen(QPen(QColor(CHANNEL_COLORS.get(trig.source, MUTED)), 1, Qt.PenStyle.DashLine))
        y = y_of(trig.level_v)
        painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

    # -- Kurven -----------------------------------------------------------------------
    painter.setClipRect(plot)
    columns = int(plot.width())
    for name in names:
        v = series[name]
        painter.setPen(QPen(QColor(CHANNEL_COLORS.get(name, TEXT)), 1.4))
        if v.size <= columns:
            points = [QPointF(x_of(tv), y_of(vv)) for tv, vv in zip(t, v)]
            painter.drawPolyline(points)
            continue
        bounds = np.linspace(0, v.size, columns + 1).astype(int)
        starts = bounds[:-1]
        lows = np.minimum.reduceat(v, starts)
        highs = np.maximum.reduceat(v, starts)
        firsts = v[starts]
        lasts = v[np.maximum(bounds[1:] - 1, starts)]
        prev = None
        for c in range(columns):
            x = plot.left() + c + 0.5
            painter.drawLine(QPointF(x, y_of(lows[c])), QPointF(x, y_of(highs[c])))
            if prev is not None:
                painter.drawLine(QPointF(x - 1, y_of(prev)), QPointF(x, y_of(firsts[c])))
            prev = lasts[c]
    painter.setClipping(False)
    painter.setPen(QPen(QColor(GRID_CENTER), 1))
    painter.drawRect(plot)
    painter.end()

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def summary(capture: Capture) -> str:
    """Eine Zeile fuer die Kachel: Uhrzeit und die wichtigsten Kennwerte je Kanal."""
    parts = [capture.timestamp[11:19]]
    t = capture.time_s
    for name, ch in capture.channels.items():
        values, _ = analysis.measure(t, ch.volts, ["vpp", "frequency"],
                                     min_amplitude=analysis.noise_floor(ch.range_v))
        text = f"{name} {format_si(values['vpp'], 'Vss', 3)}"
        if values["frequency"] is not None:
            text += f" {format_si(values['frequency'], 'Hz', 4)}"
        parts.append(text)
    return "  ·  ".join(parts)
