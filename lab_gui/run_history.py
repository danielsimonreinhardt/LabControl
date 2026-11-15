"""Ergebnis-Uebersicht und Trends (FT-11, PI-57 a, 0.68.0). Qt-frei.

Grundlage sind die run-result.json aller Laeufe (run_results.py) in den Berichtsordnern von PC und Pi:
<gemeinsamer Ordner>/<rechner>/reports/run_*.json und der lokale Berichtsordner. Daraus:

- Historie je Ablauf (Datum, Rechner, Variante, Ergebnis, Pruefungen),
- wackelnde Pruefungen: dieselbe Pruefung mal bestanden, mal nicht (Wechsel gezaehlt),
- Messwert-Trend je Pruefung ueber viele Laeufe (letzter Messwert je Lauf, Grenzen),
- Vergleich zweier Laeufe Pruefung fuer Pruefung,
- HTML-Uebersicht fuer `labcontrol history --html`.

Eine Pruefung wird ueber Laeufe hinweg an (Schrittnummer, Aktion, Messgroesse) erkannt; aendert sich der Ablauf,
erscheint sie als neue Pruefung.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path

from i18n import tr

RESULT_FORMAT = "labcontrol-run-result"
OUTCOME_DE = {"passed": "bestanden", "failed_checks": "nicht bestanden", "error": "abgebrochen",
              "aborted": "abgebrochen", "stopped": "gestoppt", "interrupted": "unterbrochen"}


@dataclass
class CheckResult:
    key: str
    label: str
    verdict: str                 # passed | failed | not_run
    value: float | None
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None


@dataclass
class RunSummary:
    file: str
    testcase: str
    host: str
    started: str
    duration_s: float | None
    outcome: str
    exit_code: int | None
    variant: str = ""
    error: str = ""
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"

    def check(self, key: str) -> CheckResult | None:
        return next((c for c in self.checks if c.key == key), None)


def check_key(row: dict) -> str:
    return f"{row.get('index')}:{row.get('action')}:{row.get('field')}"


def _summary(data: dict, path: Path) -> RunSummary | None:
    if not isinstance(data, dict) or data.get("format") != RESULT_FORMAT:
        return None
    checks = []
    for row in data.get("checks") or []:
        values = [v for v in (row.get("values") or []) if isinstance(v, (int, float))]
        label = " · ".join(p for p in (tr("Schritt {n}", n=row.get("index")), row.get("device") or "",
                                       row.get("action_label") or row.get("action") or "", row.get("check") or "")
                           if p)
        checks.append(CheckResult(check_key(row), label, row.get("verdict") or "not_run",
                                  values[-1] if values else None, row.get("unit") or "",
                                  row.get("min"), row.get("max")))
    for row in data.get("timing_checks") or []:
        key = f"timing:{row.get('step')}:{row.get('mode')}"
        label = tr("Schritt {n} · Zeitprüfung {check}", n=row.get("step"), check=row.get("check") or row.get("mode"))
        value = row.get("value")
        checks.append(CheckResult(key, label, "passed" if row.get("passed") else
                                  ("failed" if row.get("passed") is False else "not_run"),
                                  float(value) if isinstance(value, (int, float)) else None, row.get("unit") or ""))
    error = data.get("error") or {}
    return RunSummary(str(path), str(data.get("testcase") or path.stem), str(data.get("host") or ""),
                      str(data.get("started") or ""), data.get("duration_s"), str(data.get("outcome") or ""),
                      data.get("exit_code"), str((data.get("meta") or {}).get("variant") or ""),
                      str(error.get("message") or "") if isinstance(error, dict) else "", checks)


def default_folders(network_dir: str | None, local_reports: Path | None) -> list[Path]:
    """Berichtsordner aller Rechner im gemeinsamen Ordner, dazu der lokale (wie `labcontrol coverage`)."""
    folders: list[Path] = []
    if network_dir and Path(network_dir).is_dir():
        folders += sorted(p for p in Path(network_dir).glob("*/reports") if p.is_dir())
    if local_reports is not None:
        folders.append(local_reports)
    return folders


def load_runs(folders, cache: dict | None = None) -> list[RunSummary]:
    """Alle Laeufe, aelteste zuerst. cache: {Pfad: (mtime, RunSummary)} haelt schon gelesene Dateien."""
    runs: list[RunSummary] = []
    seen: set[str] = set()
    for folder in folders:
        try:
            paths = sorted(Path(folder).glob("run_*.json"))
        except OSError:
            continue
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if cache is not None and key in cache and cache[key][0] == mtime:
                summary = cache[key][1]
            else:
                try:
                    summary = _summary(json.loads(path.read_text(encoding="utf-8")), path)
                except (OSError, ValueError):
                    summary = None
                if cache is not None:
                    cache[key] = (mtime, summary)
            if summary is not None:
                runs.append(summary)
    return sorted(runs, key=lambda r: r.started)


def testcases(runs: list[RunSummary]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for run in runs:
        counts[run.testcase] = counts.get(run.testcase, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[0].lower())


def for_testcase(runs: list[RunSummary], testcase: str) -> list[RunSummary]:
    return [r for r in runs if r.testcase == testcase]


def pass_rate(runs: list[RunSummary]) -> float | None:
    return sum(r.passed for r in runs) / len(runs) if runs else None


def flaky_checks(runs: list[RunSummary]) -> list[dict]:
    """Pruefungen, die in diesen Laeufen mal bestanden und mal nicht: Wechsel, bestanden, nicht bestanden."""
    series: dict[str, dict] = {}
    for run in runs:
        for check in run.checks:
            if check.verdict not in ("passed", "failed"):
                continue
            entry = series.setdefault(check.key, {"key": check.key, "label": check.label, "verdicts": []})
            entry["verdicts"].append(check.verdict)
            entry["label"] = check.label
    result = []
    for entry in series.values():
        verdicts = entry["verdicts"]
        flips = sum(1 for a, b in zip(verdicts, verdicts[1:]) if a != b)
        if "passed" in verdicts and "failed" in verdicts:
            result.append({"key": entry["key"], "label": entry["label"], "flips": flips,
                           "passed": verdicts.count("passed"), "failed": verdicts.count("failed"),
                           "last": verdicts[-1]})
    return sorted(result, key=lambda e: (-e["flips"], e["label"]))


def check_keys(runs: list[RunSummary]) -> list[tuple[str, str]]:
    """(key, label) aller Pruefungen mit Messwert, in Schrittreihenfolge."""
    labels: dict[str, str] = {}
    for run in runs:
        for check in run.checks:
            if check.value is not None:
                labels[check.key] = check.label
    def order(key: str):
        head = key.split(":", 1)[0]
        return (int(head) if head.isdigit() else 10 ** 6, key)
    return [(key, labels[key]) for key in sorted(labels, key=order)]


def trend(runs: list[RunSummary], key: str) -> list[dict]:
    """Je Lauf mit dieser Pruefung: Zeit, Rechner, Wert, Grenzen, Urteil."""
    points = []
    for run in runs:
        check = run.check(key)
        if check is not None and check.value is not None:
            points.append({"started": run.started, "host": run.host, "value": check.value, "min": check.minimum,
                           "max": check.maximum, "verdict": check.verdict, "unit": check.unit, "file": run.file})
    return points


def compare(a: RunSummary, b: RunSummary) -> list[dict]:
    """Pruefung fuer Pruefung: Urteil und Wert in a und b, changed = Urteil anders."""
    keys = list(dict.fromkeys([c.key for c in a.checks] + [c.key for c in b.checks]))
    rows = []
    for key in keys:
        ca, cb = a.check(key), b.check(key)
        rows.append({"key": key, "label": (ca or cb).label,
                     "a_verdict": ca.verdict if ca else "–", "a_value": ca.value if ca else None,
                     "b_verdict": cb.verdict if cb else "–", "b_value": cb.value if cb else None,
                     "unit": (ca or cb).unit,
                     "changed": (ca.verdict if ca else None) != (cb.verdict if cb else None)})
    return rows


# -- HTML (labcontrol history --html) -------------------------------------------------------------

def _svg_trend(points: list[dict], width: int = 360, height: int = 80) -> str:
    values = [p["value"] for p in points]
    limits = [v for p in points for v in (p["min"], p["max"]) if isinstance(v, (int, float))]
    low, high = min(values + limits), max(values + limits)
    if high == low:
        high, low = high + 1, low - 1
    def y(v):
        return height - 6 - (v - low) / (high - low) * (height - 12)
    def x(i):
        return 6 + (i / max(1, len(points) - 1)) * (width - 12)
    parts = [f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
             f'style="background:#f8fafc;border:1px solid #e5e7eb">']
    for limit_key in ("min", "max"):
        limit = points[-1][limit_key]
        if isinstance(limit, (int, float)):
            parts.append(f'<line x1="0" x2="{width}" y1="{y(limit):.1f}" y2="{y(limit):.1f}" '
                         f'stroke="#9ca3af" stroke-dasharray="4 3"/>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    parts.append(f'<path d="{path}" fill="none" stroke="#2563eb" stroke-width="1.5"/>')
    for i, p in enumerate(points):
        color = "#16a34a" if p["verdict"] == "passed" else "#dc2626"
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(p["value"]):.1f}" r="2.5" fill="{color}">'
                     f'<title>{html.escape(p["started"])} {p["value"]:g} {html.escape(p["unit"])}</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def html_overview(runs: list[RunSummary], testcase: str | None = None, last: int = 50) -> str:
    esc = html.escape
    names = [testcase] if testcase else [name for name, _n in testcases(runs)]
    out = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>LabControl – Ergebnisse</title>",
           "<style>body{font:14px system-ui,sans-serif;margin:24px;color:#111}table{border-collapse:collapse;"
           "margin:8px 0 16px}td,th{border-bottom:1px solid #e5e7eb;padding:4px 8px;text-align:left}"
           ".ok{color:#16a34a}.bad{color:#dc2626}h2{margin-top:28px}</style></head><body>",
           f"<h1>{esc(tr('Ergebnis-Übersicht'))}</h1>"]
    for name in names:
        selected = for_testcase(runs, name)[-last:]
        if not selected:
            continue
        rate = pass_rate(selected)
        out.append(f"<h2>{esc(name)}</h2><p>{len(selected)} {esc(tr('Läufe'))}, "
                   f"{esc(tr('bestanden'))} {rate * 100:.0f} %</p>")
        out.append(f"<table><tr><th>{esc(tr('Start'))}</th><th>{esc(tr('Rechner'))}</th>"
                   f"<th>{esc(tr('Variante'))}</th><th>{esc(tr('Ergebnis'))}</th><th>{esc(tr('Prüfungen'))}</th></tr>")
        for run in reversed(selected):
            failed = sum(c.verdict == "failed" for c in run.checks)
            css = "ok" if run.passed else "bad"
            out.append(f"<tr><td>{esc(run.started)}</td><td>{esc(run.host)}</td><td>{esc(run.variant)}</td>"
                       f"<td class='{css}'>{esc(tr(OUTCOME_DE.get(run.outcome, run.outcome)))}</td>"
                       f"<td>{len(run.checks) - failed}/{len(run.checks)}</td></tr>")
        out.append("</table>")
        flaky = flaky_checks(selected)
        if flaky:
            out.append(f"<h3>{esc(tr('Wackelnde Prüfungen'))}</h3><table><tr><th>{esc(tr('Prüfung'))}</th>"
                       f"<th>{esc(tr('Wechsel'))}</th><th>{esc(tr('bestanden'))}</th>"
                       f"<th>{esc(tr('nicht bestanden'))}</th></tr>")
            for entry in flaky:
                out.append(f"<tr><td>{esc(entry['label'])}</td><td>{entry['flips']}</td><td>{entry['passed']}</td>"
                           f"<td>{entry['failed']}</td></tr>")
            out.append("</table>")
        keys = check_keys(selected)
        if keys:
            out.append(f"<h3>{esc(tr('Messwert-Trends'))}</h3>")
            for key, label in keys:
                points = trend(selected, key)
                if len(points) >= 2:
                    out.append(f"<p>{esc(label)}<br>{_svg_trend(points)}</p>")
    out.append("</body></html>")
    return "".join(out)
