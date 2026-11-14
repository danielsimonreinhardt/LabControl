"""Zeitbezogene Pruefungen (0.63.0, FT-7): Auswertung eines Messwertverlaufs ueber ein Zeitfenster.

Drei Arten (TestStep.params["monitor"]["mode"] eines Blocks "Ueberwachen … Ende"):
  band     -- der Wert bleibt fuer die ganze Blockdauer in [min, max]
  reach    -- der Wert erfuellt "op value" spaetestens within_s nach Blockbeginn; gemessen wird die Zeit bis
              dahin (Reaktionszeit, wenn der Ausloeser der erste Schritt im Block ist)
  can_gap  -- eine CAN-Botschaft kommt waehrend des Blocks hoechstens max_gap_ms auseinander

Die Monitore bekommen (t, Wert) bzw. Frame-Zeitstempel und sind Qt-frei, damit dieselbe Auswertung live
(testcase_runner) und spaeter auf Aufzeichnungen (CSV/MF4, Oszi-Erfassungen) laeuft. Die Aufloesung ist der
Median der Abstaende der gefuetterten Werte; ist sie fuer eine reach-Pruefung zu grob (mehr als die Haelfte
von within_s), ist die Pruefung nicht aussagekraeftig und gilt als verletzt (FP-18 c).
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field

from i18n import tr

MODES = ("band", "reach", "can_gap")
MODE_LABELS = {"band": "Im Band bleiben", "reach": "Erreichen innerhalb", "can_gap": "CAN-Lücken"}
MAX_SERIES = 4000          # fuer den Kurvenausschnitt im Bericht
_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq,
        "!=": operator.ne}


def clean_config(raw) -> dict:
    """params["monitor"] mit Standardwerten (Datei von Hand bearbeitet, aeltere Fassung)."""
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode") if raw.get("mode") in MODES else "band"

    def num(key: str, default: float) -> float:
        try:
            return float(raw.get(key, default))
        except (TypeError, ValueError):
            return default
    can_id = raw.get("can_id", 0)
    try:
        can_id = int(str(can_id), 0) if isinstance(can_id, str) else int(can_id)
    except (TypeError, ValueError):
        can_id = 0
    return {"mode": mode, "min": num("min", 0.0), "max": num("max", 0.0),
            "op": raw.get("op") if raw.get("op") in _OPS else ">=", "value": num("value", 0.0),
            "within_s": max(0.0, num("within_s", 1.0)), "can_id": can_id,
            "max_gap_ms": max(0.0, num("max_gap_ms", 100.0)), "abort": bool(raw.get("abort", False))}


def summary(config: dict, source: str = "", unit: str = "") -> str:
    """Kurztext fuer Editorzeile, Statuszeile und Bericht."""
    c = clean_config(config)
    u = f" {unit}" if unit else ""
    if c["mode"] == "band":
        text = tr("{source} bleibt in {lo:g}–{hi:g}{unit}", source=source, lo=c["min"], hi=c["max"], unit=u)
    elif c["mode"] == "reach":
        text = tr("{source} {op} {value:g}{unit} innerhalb {t:g} s", source=source, op=c["op"], value=c["value"],
                  unit=u, t=c["within_s"])
    else:
        text = tr("CAN 0x{can_id:X} Lücke ≤ {gap:g} ms", can_id=c["can_id"], gap=c["max_gap_ms"])
    return text.strip()


def resolution_s(times: list[float]) -> float | None:
    gaps = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    if not gaps:
        return None
    return gaps[len(gaps) // 2]


@dataclass
class Verdict:
    passed: bool
    value: float                  # band: Extremwert, reach: Zeit bis erreicht (s), can_gap: groesste Luecke (ms)
    message: str = ""
    resolution_s: float | None = None
    samples: int = 0


@dataclass
class Monitor:
    config: dict
    t_start: float
    times: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    violation: Verdict | None = None        # erste Verletzung (band sofort, reach bei Fristablauf)
    reached_at: float | None = None

    def __post_init__(self) -> None:
        self.config = clean_config(self.config)

    @property
    def mode(self) -> str:
        return self.config["mode"]

    def feed(self, t: float, value: float = 0.0) -> Verdict | None:
        """Neuer Wert (bei can_gap: Frame-Zeitstempel). Liefert eine Verletzung, sobald sie feststeht."""
        if t < self.t_start:
            return None
        self.times.append(t)
        self.values.append(value)
        if len(self.times) > MAX_SERIES * 4:
            # nur ausduennen, die Auswertung braucht keine Einzelwerte mehr (Extremwert/Luecke laufen mit)
            self.times, self.values = self.times[::2], self.values[::2]
        c = self.config
        if self.violation is not None:
            return None
        if self.mode == "band" and not (c["min"] <= value <= c["max"]):
            self.violation = Verdict(False, value, tr("{value:g} außerhalb {lo:g}–{hi:g} bei {t:.2f} s",
                                                      value=value, lo=c["min"], hi=c["max"], t=t - self.t_start))
            return self.violation
        if self.mode == "reach" and self.reached_at is None:
            if _OPS[c["op"]](value, c["value"]):
                self.reached_at = t
            elif t - self.t_start > c["within_s"]:
                return self.tick(t)
        if self.mode == "can_gap" and len(self.times) >= 2:
            gap_ms = (self.times[-1] - self.times[-2]) * 1000.0
            if gap_ms > c["max_gap_ms"]:
                self.violation = Verdict(False, gap_ms, tr("Lücke {gap:.0f} ms bei {t:.2f} s", gap=gap_ms,
                                                           t=self.times[-2] - self.t_start))
                return self.violation
        return None

    def tick(self, now: float) -> Verdict | None:
        """Zeit ohne neuen Wert: Frist (reach) bzw. Luecke seit dem letzten Frame (can_gap) pruefen."""
        if self.violation is not None:
            return None
        c = self.config
        if self.mode == "reach" and self.reached_at is None and now - self.t_start > c["within_s"]:
            self.violation = Verdict(False, now - self.t_start,
                                     tr("{op} {value:g} nicht innerhalb {t:g} s erreicht", op=c["op"],
                                        value=c["value"], t=c["within_s"]))
            return self.violation
        if self.mode == "can_gap":
            last = self.times[-1] if self.times else self.t_start
            if (now - last) * 1000.0 > c["max_gap_ms"]:
                gap_ms = (now - last) * 1000.0
                self.violation = Verdict(False, gap_ms, tr("Lücke {gap:.0f} ms bei {t:.2f} s", gap=gap_ms,
                                                           t=last - self.t_start)
                                         if self.times else tr("keine Botschaft seit Blockbeginn"))
                return self.violation
        return None

    def finish(self, now: float) -> Verdict:
        """Blockende: Gesamturteil."""
        self.tick(now)
        c = self.config
        res = resolution_s(self.times)
        count = len(self.times)
        if self.violation is not None:
            verdict = self.violation
        elif self.mode == "band":
            if not self.values:
                verdict = Verdict(False, 0.0, tr("keine Messwerte im Block"))
            else:
                worst = max(self.values, key=lambda v: max(c["min"] - v, v - c["max"],
                                                           -min(v - c["min"], c["max"] - v)))
                verdict = Verdict(True, worst)
        elif self.mode == "reach":
            if self.reached_at is None:
                verdict = Verdict(False, now - self.t_start, tr("{op} {value:g} bis Blockende nicht erreicht",
                                                                 op=c["op"], value=c["value"]))
            elif res is not None and res > c["within_s"] / 2:
                verdict = Verdict(False, self.reached_at - self.t_start,
                                  tr("Auflösung {res:.0f} ms zu grob für {t:g} s", res=res * 1000.0,
                                     t=c["within_s"]))
            else:
                verdict = Verdict(True, self.reached_at - self.t_start)
        else:
            gaps = [(b - a) * 1000.0 for a, b in zip(self.times, self.times[1:])]
            verdict = Verdict(True, max(gaps) if gaps else 0.0)
        verdict.resolution_s = res
        verdict.samples = count
        return verdict

    def series(self) -> list[tuple[float, float]]:
        """Verlauf fuer den Bericht (hoechstens MAX_SERIES Punkte)."""
        step = max(1, len(self.times) // MAX_SERIES)
        return list(zip(self.times[::step], self.values[::step]))
