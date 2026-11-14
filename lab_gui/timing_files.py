"""Zeitpruefungen auf Aufzeichnungen (0.63.1, FT-7, FP-19 b): dieselbe Auswertung wie der Block "Ueberwachen"
(timing_checks.Monitor), aber auf einer Datei statt live.

Quellen:
  - LabControl-Aufzeichnung CSV (Verlauf/Aufnahme: "Zeitstempel;Sekunden seit Start;Geraet-ID;…;Wert")
    Kanal = "<Geraet>/<Kanal>" oder "<Geraet-ID>/<Kanal>", z. B. "Netzteil 1/Spannung"
  - LabControl-Aufzeichnung MF4 (asammdf), Kanal = Kanalname in der Datei
  - Oszi-Erfassung CSV (scope_api.store: "# …"-Kopf, dann "time_s,<Kanal>_V,…"), Kanal = "A", "CH1" …
Zeiten in Sekunden ab Aufzeichnungsbeginn; --from/--to schneiden ein Fenster aus. Qt-frei.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import timing_checks


class TimingFileError(ValueError):
    pass


def _kind(path: Path, head: str) -> str:
    if path.suffix.lower() in (".mf4", ".mdf"):
        return "mf4"
    if head.lstrip("﻿").startswith("#") or head.startswith("time_s,"):
        return "scope"
    if "Sekunden seit Start" in head:
        return "recording"
    raise TimingFileError(f"{path.name}: weder LabControl-Aufzeichnung (CSV/MF4) noch Oszi-Erfassung")


def _read_recording(text: str) -> dict[str, tuple[list[float], list[float]]]:
    series: dict[str, tuple[list[float], list[float]]] = {}
    reader = csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";")
    header = next(reader, [])
    try:
        col = {name: header.index(name) for name in ("Sekunden seit Start", "Geraet-ID", "Geraet", "Kanal", "Wert")}
    except ValueError as exc:
        raise TimingFileError(f"Aufzeichnung: Spalte fehlt ({exc})") from None
    for row in reader:
        try:
            t = float(row[col["Sekunden seit Start"]])
            value = float(row[col["Wert"]])
        except (ValueError, IndexError):
            continue
        for name in (f'{row[col["Geraet"]]}/{row[col["Kanal"]]}', f'{row[col["Geraet-ID"]]}/{row[col["Kanal"]]}'):
            times, values = series.setdefault(name, ([], []))
            times.append(t)
            values.append(value)
    return series


def _read_scope(text: str) -> dict[str, tuple[list[float], list[float]]]:
    lines = [line for line in text.lstrip("﻿").splitlines() if line and not line.startswith("#")]
    if not lines:
        return {}
    header = lines[0].split(",")
    names = [h[:-2] if h.endswith("_V") else h for h in header[1:]]
    series = {name: ([], []) for name in names}
    for line in lines[1:]:
        parts = line.split(",")
        try:
            t = float(parts[0])
        except (ValueError, IndexError):
            continue
        for name, raw in zip(names, parts[1:]):
            try:
                value = float(raw)
            except ValueError:
                continue
            series[name][0].append(t)
            series[name][1].append(value)
    return series


def _read_mf4(path: Path) -> dict[str, tuple[list[float], list[float]]]:
    from asammdf import MDF
    series = {}
    mdf = MDF(str(path))
    try:
        for group_index, group in enumerate(mdf.groups):
            for channel_index, channel in enumerate(group.channels):
                if channel_index == 0 or channel.name in ("time", "t"):
                    continue
                signal = mdf.get(channel.name, group=group_index, index=channel_index)
                series[channel.name] = ([float(t) for t in signal.timestamps], [float(v) for v in signal.samples])
    finally:
        mdf.close()
    return series


def read_series(path) -> dict[str, tuple[list[float], list[float]]]:
    """Alle Kanaele einer Datei: {Kanal: (Zeiten s, Werte)}."""
    path = Path(path)
    if not path.is_file():
        raise TimingFileError(f"{path}: Datei nicht gefunden")
    if path.suffix.lower() in (".mf4", ".mdf"):
        return _read_mf4(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    kind = _kind(path, text[:400])
    return _read_recording(text) if kind == "recording" else _read_scope(text)


def evaluate(times: list[float], values: list[float], config: dict, t_from: float | None = None,
             t_to: float | None = None):
    """Monitor ueber den Verlauf laufen lassen (Fenster [t_from, t_to]); liefert (Verdict, Monitor)."""
    if not times:
        raise TimingFileError("Kanal ohne Werte")
    start = times[0] if t_from is None else t_from
    end = times[-1] if t_to is None else t_to
    monitor = timing_checks.Monitor(config, start)
    for t, value in zip(times, values):
        if start <= t <= end:
            monitor.feed(t, value)
    return monitor.finish(end), monitor


def check_file(path, channel: str | None, config: dict, t_from: float | None = None,
               t_to: float | None = None) -> dict:
    """Zeitpruefung auf einer Datei; Ergebnis wie run-result.json "timing_checks" (ohne Schritt)."""
    series = read_series(path)
    if not series:
        raise TimingFileError(f"{Path(path).name}: keine Kanäle")
    if channel is None:
        if len(series) != 1:
            raise TimingFileError("Mehrere Kanäle – bitte --channel angeben: " + ", ".join(sorted(series)))
        channel = next(iter(series))
    if channel not in series:
        raise TimingFileError(f"Kanal {channel!r} nicht in der Datei: " + ", ".join(sorted(series)))
    times, values = series[channel]
    verdict, monitor = evaluate(times, values, config, t_from, t_to)
    clean = timing_checks.clean_config(config)
    return {"file": str(path), "channel": channel, "mode": clean["mode"],
            "check": timing_checks.summary(clean, channel), "passed": verdict.passed,
            "value": round(verdict.value, 9), "message": verdict.message,
            "resolution_ms": round(verdict.resolution_s * 1000.0, 6) if verdict.resolution_s is not None else None,
            "samples": len(monitor.times), "from": monitor.t_start, "to": t_to if t_to is not None else times[-1]}
