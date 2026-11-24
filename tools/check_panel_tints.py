"""Panel-Farben (1.0.4): genug Toene, gut unterscheidbar, Text lesbar.

- jede Farbe aus theme.PANEL_COLOR_ORDER hat Anzeigenamen, Hex-Wert in hellem und dunklem Theme und
  Uebersetzungen (de/en)
- kleinster Farbabstand (CIEDE2000) zweier Toene: dunkel >= 14, hell >= 9; Tuerkis/Gruen dunkel >= 14
  (vor 1.0.4: 11, Nutzer: "unterscheiden sich zu wenig")
- Text und gedaempfter Text auf jeder Toenung mindestens 4,5:1 (WCAG AA)

Rein rechnerisch, keine Qt-Anwendung.  Aufruf:  python tools/check_panel_tints.py
"""
from __future__ import annotations

import itertools
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "lab_gui")]

FAILURES: list[str] = []
MIN_DE = {"Slate Ops Dark": 14.0}
MIN_DE_DEFAULT = 9.0
MIN_COLORS = 10


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(name)


def _channel(c: int) -> float:
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(hex_color: str) -> list[float]:
    h = hex_color.lstrip("#")
    return [_channel(int(h[i:i + 2], 16)) for i in (0, 2, 4)]


def luminance(hex_color: str) -> float:
    r, g, b = _rgb(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def lab(hex_color: str) -> tuple[float, float, float]:
    r, g, b = _rgb(hex_color)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(c1: tuple, c2: tuple) -> float:
    """CIEDE2000."""
    l1, a1, b1 = c1
    l2, a2, b2 = c2
    cb = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(cb ** 7 / (cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dl, dc = l2 - l1, c2p - c1p
    dh = h2p - h1p
    if c1p * c2p == 0:
        dh = 0
    elif dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360
    dhp = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))
    lbp, cbp = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dth = 30 * math.exp(-((hbp - 275) / 25) ** 2)
    rc = 2 * math.sqrt(cbp ** 7 / (cbp ** 7 + 25 ** 7))
    sl = 1 + 0.015 * (lbp - 50) ** 2 / math.sqrt(20 + (lbp - 50) ** 2)
    sc, sh = 1 + 0.045 * cbp, 1 + 0.015 * cbp * t
    rt = -math.sin(math.radians(2 * dth)) * rc
    return math.sqrt((dl / sl) ** 2 + (dc / sc) ** 2 + (dhp / sh) ** 2 + rt * (dc / sc) * (dhp / sh))


def main() -> int:
    from theme import DARK, LIGHT, PANEL_COLOR_LABELS, PANEL_COLOR_ORDER
    print("Farbliste")
    check(f"mindestens {MIN_COLORS} Farben", len(PANEL_COLOR_ORDER) >= MIN_COLORS, str(len(PANEL_COLOR_ORDER)))
    check("keine doppelt", len(set(PANEL_COLOR_ORDER)) == len(PANEL_COLOR_ORDER))
    check("jede mit Anzeigenamen", set(PANEL_COLOR_LABELS) == set(PANEL_COLOR_ORDER))
    for lang in ("de", "en"):
        table = json.loads((ROOT / "lab_gui" / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
        missing = [name for name in PANEL_COLOR_LABELS.values() if name not in table]
        check(f"Namen übersetzt ({lang})", not missing, ", ".join(missing))
    for pal in (DARK, LIGHT):
        print(pal.name)
        tints = pal.panel_tints
        check("alle Farben definiert", set(tints) == set(PANEL_COLOR_ORDER), str(set(PANEL_COLOR_ORDER) ^ set(tints)))
        labs = {k: lab(v) for k, v in tints.items()}
        pairs = sorted((delta_e(labs[a], labs[b]), a, b) for a, b in itertools.combinations(tints, 2))
        dist, a, b = pairs[0]
        need = MIN_DE.get(pal.name, MIN_DE_DEFAULT)
        check(f"kleinster Abstand >= {need:g}", dist >= need, f"{a}/{b} {dist:.1f}")
        if pal is DARK:
            tg = delta_e(labs["teal"], labs["green"])
            check("Türkis/Grün >= 14", tg >= 14, f"{tg:.1f}")
        for key, color in tints.items():
            text, muted = contrast(color, pal.text), contrast(color, pal.text_muted)
            check(f"{key}: Text {text:.1f}:1, gedämpft {muted:.1f}:1", text >= 4.5 and muted >= 4.5)
    print()
    if FAILURES:
        print("FEHLGESCHLAGEN:", FAILURES)
        return 1
    print("Alles ok. (rein rechnerisch)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
