"""Herstellerneutrale Auswertung von Oszilloskop-Erfassungen (numpy).

Alle Kennwerte werden hier aus den Rohdaten berechnet, nicht von der
Mess-Engine des jeweiligen Geräts -- so liefern das PicoScope 2204A und ein
späteres zweites Scope vergleichbare Zahlen, und eine gespeicherte Erfassung
lässt sich neu auswerten, ohne erneut zu messen.

Einheiten: V, s, Hz, %. Ein Kennwert, der sich im Fenster nicht bestimmen
lässt (z.B. Frequenz bei nur einer Flanke), ist None; der Grund steht dann in
`unavailable`.

Pegel und Flanken: base/top sind die Mediane der Samples unter bzw. über der
Mitte zwischen Min und Max (robust gegen Überschwinger und Rauschen). Flanken
werden an der 50-%-Schwelle mit Hysterese erkannt (10 % des Hubs, bei
Rauschen bis 3 Sigma, höchstens 40 %) und linear interpoliert; Anstiegs-/Abfallzeit sind 10-90 %.

Rauschschwelle: ist der Hub (top - base) kleiner als min_amplitude, gibt es
keine Flanken -- sonst liefert reines Rauschen eines offenen Eingangs Fantasie-
Frequenzen (am 2204A gesehen: 15 kHz und 620 kHz ohne Signal). report() und
der Speicher setzen dafür NOISE_FRACTION des Messbereichs an.
"""
from __future__ import annotations

import math

import numpy as np

from scope_api.base import Capture, ScopeError

MEASUREMENTS = {
    "vmax": "V", "vmin": "V", "vpp": "V", "vmean": "V", "vrms": "V",
    "vbase": "V", "vtop": "V", "amplitude": "V",
    "frequency": "Hz", "period": "s", "duty": "%",
    "rise_time": "s", "fall_time": "s", "overshoot": "%", "undershoot": "%",
    "edge_count": "",
}
DEFAULT_MEASUREMENTS = ("vmin", "vmax", "vpp", "vmean", "vrms", "frequency", "duty")

HYSTERESIS = 0.10
MAX_HYSTERESIS = 0.40
# Anstiegs-/Abfallzeiten unter so vielen Abtastintervallen sind nicht messbar.
RESOLUTION_SAMPLES = 2
# 2 % des Vollausschlags = gut 2,5 ADC-Stufen bei 8 Bit (Stufe = 2 * fs / 256).
NOISE_FRACTION = 0.02


def noise_floor(range_v: float) -> float:
    """Kleinster Hub, ab dem Flanken ausgewertet werden (range_v = Vollausschlag +-V)."""
    return NOISE_FRACTION * range_v


def num(value) -> float | int | None:
    """JSON-taugliche Zahl mit 6 signifikanten Stellen (spart Kontext)."""
    if value is None:
        return None
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        return int(value)
    value = float(value)
    if not math.isfinite(value):
        return None
    return float(f"{value:.6g}")


def _window(t: np.ndarray, v: np.ndarray, t_start: float | None, t_stop: float | None):
    mask = np.ones(t.size, dtype=bool)
    if t_start is not None:
        mask &= t >= t_start
    if t_stop is not None:
        mask &= t <= t_stop
    if mask.sum() < 2:
        raise ScopeError("invalid_request", "Im Zeitfenster liegen weniger als 2 Samples.",
                         t_min=num(t[0]), t_max=num(t[-1]))
    return t[mask], v[mask]


def levels(v: np.ndarray) -> tuple[float, float]:
    vmax, vmin = float(v.max()), float(v.min())
    mid = (vmax + vmin) / 2.0
    low, high = v[v <= mid], v[v > mid]
    base = float(np.median(low)) if low.size else vmin
    top = float(np.median(high)) if high.size else vmax
    return base, top


def _cross_time(t: np.ndarray, v: np.ndarray, i: int, level: float) -> float:
    """Zeitpunkt, an dem v zwischen Sample i und i+1 `level` kreuzt (linear)."""
    dv = v[i + 1] - v[i]
    if dv == 0:
        return float(t[i])
    return float(t[i] + (level - v[i]) / dv * (t[i + 1] - t[i]))


def edges(t: np.ndarray, v: np.ndarray, base: float, top: float) -> list[tuple[str, int, float]]:
    """Flanken als (richtung, index_vor_kreuzung, zeit) in zeitlicher Reihenfolge."""
    amp = top - base
    if not amp > 0:
        return []
    mid = base + amp / 2.0
    # Hysterese an das Rauschen anpassen: Rauschen aus der Differenz
    # benachbarter Samples (robust per MAD, Flanken fallen kaum ins Gewicht).
    diffs = np.diff(v)
    sigma = 1.4826 * float(np.median(np.abs(diffs - np.median(diffs)))) / math.sqrt(2.0) if diffs.size else 0.0
    hyst = min(max(HYSTERESIS * amp, 3.0 * sigma), MAX_HYSTERESIS * amp)
    state = np.full(v.size, -1, dtype=np.int8)
    state[v > mid + hyst] = 1
    state[v < mid - hyst] = 0
    known = np.where(state >= 0, np.arange(v.size), 0)
    np.maximum.accumulate(known, out=known)
    filled = state[known]
    changes = np.nonzero((filled[1:] != filled[:-1]) & (filled[:-1] >= 0))[0] + 1
    result: list[tuple[str, int, float]] = []
    for i in changes:
        rising = filled[i] == 1
        j = int(i)
        # zurück bis zum letzten Sample auf der anderen Seite der Mitte
        while j > 0 and ((v[j - 1] > mid) if rising else (v[j - 1] < mid)):
            j -= 1
        if j == 0:
            continue
        result.append(("rising" if rising else "falling", j - 1, _cross_time(t, v, j - 1, mid)))
    return result


def _transition_time(t, v, idx: int, lo: float, hi: float, rising: bool, lower: int, upper: int) -> float | None:
    """10-90-%-Zeit einer Flanke um Index idx, gesucht zwischen lower und upper."""
    start_level, end_level = (lo, hi) if rising else (hi, lo)
    k = idx
    while k >= lower and ((v[k] > start_level) if rising else (v[k] < start_level)):
        k -= 1
    m = idx + 1
    while m <= upper and ((v[m] < end_level) if rising else (v[m] > end_level)):
        m += 1
    if k < lower or m > upper or k + 1 >= v.size or m < 1:
        return None
    t_start = _cross_time(t, v, k, start_level)
    t_end = _cross_time(t, v, m - 1, end_level)
    return t_end - t_start if t_end >= t_start else None


def measure(t: np.ndarray, v: np.ndarray, items=DEFAULT_MEASUREMENTS,
            t_start: float | None = None, t_stop: float | None = None,
            min_amplitude: float = 0.0) -> tuple[dict, dict]:
    """Kennwerte für ein Signal. Liefert (werte, unavailable-gruende)."""
    unknown = [name for name in items if name not in MEASUREMENTS]
    if unknown:
        raise ScopeError("unknown_measurement", f"Unbekannte Kennwerte: {', '.join(unknown)}",
                         allowed=sorted(MEASUREMENTS))
    t, v = _window(np.asarray(t, dtype=float), np.asarray(v, dtype=float), t_start, t_stop)
    base, top = levels(v)
    amp = top - base
    too_small = amp < min_amplitude
    found = [] if too_small else edges(t, v, base, top)
    rising = [e for e in found if e[0] == "rising"]
    falling = [e for e in found if e[0] == "falling"]

    values: dict = {}
    reasons: dict = {}

    def period() -> float | None:
        if len(rising) >= 2:
            return (rising[-1][2] - rising[0][2]) / (len(rising) - 1)
        if len(falling) >= 2:
            return (falling[-1][2] - falling[0][2]) / (len(falling) - 1)
        return None

    for name in items:
        value = None
        if name == "vmax":
            value = v.max()
        elif name == "vmin":
            value = v.min()
        elif name == "vpp":
            value = v.max() - v.min()
        elif name == "vmean":
            value = v.mean()
        elif name == "vrms":
            value = math.sqrt(float(np.mean(v * v)))
        elif name == "vbase":
            value = base
        elif name == "vtop":
            value = top
        elif name == "amplitude":
            value = amp
        elif name == "edge_count":
            value = len(found)
        elif name in ("period", "frequency"):
            p = period()
            if p is None or p <= 0:
                reasons[name] = "weniger als zwei gleichartige Flanken im Fenster"
            else:
                value = p if name == "period" else 1.0 / p
        elif name == "duty":
            ratios = []
            for k in range(len(found) - 2):
                a, b, c = found[k], found[k + 1], found[k + 2]
                if a[0] == "rising" and b[0] == "falling" and c[0] == "rising":
                    ratios.append((b[2] - a[2]) / (c[2] - a[2]))
            if ratios:
                value = 100.0 * float(np.mean(ratios))
            else:
                reasons[name] = "keine vollständige Periode (steigend-fallend-steigend) im Fenster"
        elif name in ("rise_time", "fall_time"):
            want = "rising" if name == "rise_time" else "falling"
            lo, hi = base + 0.1 * amp, base + 0.9 * amp
            times = []
            for n, (direction, idx, _) in enumerate(found):
                if direction != want:
                    continue
                lower = found[n - 1][1] + 1 if n > 0 else 0
                upper = found[n + 1][1] if n + 1 < len(found) else v.size - 1
                tt = _transition_time(t, v, idx, lo, hi, want == "rising", lower, upper)
                if tt is not None:
                    times.append(tt)
            dt = float(np.median(np.diff(t)))
            if times and float(np.mean(times)) < RESOLUTION_SAMPLES * dt:
                # Flanke fällt zwischen zwei Samples: gemessen würde nur das Abtastintervall.
                reasons[name] = (f"Flanke schneller als die Auflösung ({RESOLUTION_SAMPLES} Abtastintervalle = "
                                 f"{RESOLUTION_SAMPLES * dt:.3g} s): feineres Abtastintervall wählen, sofern das Gerät "
                                 "eines hat (Fähigkeiten: max_sample_rate_hz)")
            elif times:
                value = float(np.mean(times))
            else:
                reasons[name] = f"keine vollständige {'steigende' if want == 'rising' else 'fallende'} Flanke im Fenster"
        elif name in ("overshoot", "undershoot"):
            if amp > 0:
                value = 100.0 * ((v.max() - top) if name == "overshoot" else (base - v.min())) / amp
            else:
                reasons[name] = "Signal ohne zwei Pegel"
        if value is None and too_small and name in reasons:
            reasons[name] = (f"Signalhub {amp:.3g} V unter der Rauschschwelle {min_amplitude:.3g} V "
                             "(kleineren Bereich wählen)")
        values[name] = num(value)
    return values, reasons


def envelope(t: np.ndarray, v: np.ndarray, points: int = 200) -> dict:
    """Verdichtung auf höchstens `points` Abschnitte mit Min/Max je Abschnitt.

    Anders als einfaches Ausdünnen gehen dabei kurze Spitzen nicht verloren.
    """
    points = max(1, int(points))
    if v.size <= points:
        return {"t": [num(x) for x in t], "min": [num(x) for x in v], "max": [num(x) for x in v]}
    bounds = np.linspace(0, v.size, points + 1).astype(int)
    starts = bounds[:-1]
    return {
        "t": [num(x) for x in t[starts]],
        "min": [num(x) for x in np.minimum.reduceat(v, starts)],
        "max": [num(x) for x in np.maximum.reduceat(v, starts)],
    }


def decimate(t: np.ndarray, v: np.ndarray, max_points: int, mode: str = "minmax") -> dict:
    """Ausschnitt für get_capture: roh, gemittelt oder als Min/Max-Hüllkurve."""
    if v.size <= max_points:
        return {"mode": "raw", "t": [num(x) for x in t], "v": [num(x) for x in v]}
    if mode == "minmax":
        return {"mode": "minmax", **envelope(t, v, max(1, max_points // 2))}
    if mode == "mean":
        bounds = np.linspace(0, v.size, max_points + 1).astype(int)
        starts = bounds[:-1]
        counts = np.diff(bounds)
        means = np.add.reduceat(v, starts) / counts
        return {"mode": "mean", "t": [num(x) for x in t[starts]], "v": [num(x) for x in means]}
    raise ScopeError("invalid_request", f"Verdichtung {mode!r} unbekannt.", allowed=["minmax", "mean"])


def trigger_seen(v: np.ndarray, index: int, level: float, edge: str, tolerance: int = 3) -> bool:
    """Kreuzt das Signal am Trigger-Index den Pegel in der richtigen Richtung?

    Für den auto-Modus: das Gerät sagt nicht, ob es getriggert oder nach dem
    Timeout frei erfasst hat -- am Signal selbst lässt es sich ablesen.
    """
    lo = max(0, index - tolerance)
    hi = min(v.size - 1, index + tolerance)
    if hi <= lo:
        return False
    segment = v[lo:hi + 1]
    if edge == "rising":
        return bool(np.any((segment[:-1] <= level) & (segment[1:] > level)))
    return bool(np.any((segment[:-1] >= level) & (segment[1:] < level)))


def report(capture: Capture, items=DEFAULT_MEASUREMENTS, envelope_points: int = 200) -> dict:
    """Kompakte Antwort auf eine Erfassung (acquire): Einstellungen, Kennwerte
    je Kanal, Warnungen, Hüllkurve. Rohdaten nur über get_capture/CSV."""
    t = capture.time_s
    channels: dict = {}
    env: dict = {"points": 0, "t": None}
    warnings = list(capture.warnings)
    for name, ch in capture.channels.items():
        values, reasons = measure(t, ch.volts, items, min_amplitude=noise_floor(ch.range_v))
        entry = {"overrange": ch.overrange, **values}
        if reasons:
            entry["unavailable"] = reasons
        channels[name] = entry
        if ch.overrange:
            warnings.append(f"{name} übersteuert: Bereich erhöhen (aktuell ±{num(ch.range_v)} V).")
        e = envelope(t, ch.volts, envelope_points)
        env["t"] = e["t"]
        env["points"] = len(e["t"])
        env[name] = {"min": e["min"], "max": e["max"]}
    req = capture.request
    return {
        "capture_id": capture.capture_id,
        "scope_id": capture.scope_id,
        "model": capture.model,
        "timestamp": capture.timestamp,
        "triggered": capture.triggered,
        "trigger": {"mode": req.trigger.mode, "source": req.trigger.source,
                    "level_v": num(req.trigger.level_v), "edge": req.trigger.edge},
        "actual": {
            "sample_interval_s": num(capture.sample_interval_s),
            "samples": capture.samples,
            "duration_s": num(capture.samples * capture.sample_interval_s),
            "t_start_s": num(t[0]),
            "t_stop_s": num(t[-1]),
            "channels": {name: {"range_v": num(ch.range_v), "coupling": ch.coupling, "probe": num(ch.probe)}
                         for name, ch in capture.channels.items()},
        },
        "channels": channels,
        "warnings": warnings,
        "envelope": env,
        "csv_path": capture.csv_path,
    }
