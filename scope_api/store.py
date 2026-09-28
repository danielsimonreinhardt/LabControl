"""Erfassungsspeicher: die letzten Erfassungen im Speicher, jede zusätzlich als CSV.

Eine Erfassung bekommt beim Ablegen eine capture_id (c-JJJJMMTT-HHMMSS-NN).
Über die ID lassen sich Ausschnitte holen (get_capture) und Kennwerte neu
berechnen (measure), ohne erneut zu messen. Die CSV bleibt auch liegen, wenn
die Erfassung aus dem Speicher fällt -- für eigene Auswertungen, z.B. mit
Python (Entscheidung E3: Datei UND HTTP-Abruf).

CSV-Format: Kopfzeilen mit '#' (Scope, Zeitpunkt, Abtastintervall,
Trigger-Index, Bereiche), dann time_s,<Kanal>_V,... -- mit
numpy.loadtxt(..., delimiter=",", comments="#", skiprows=...) oder
pandas.read_csv(..., comment="#") lesbar.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import numpy as np

from scope_api import analysis
from scope_api.base import Capture, ScopeError

DEFAULT_CAPACITY = 50


class CaptureStore:
    def __init__(self, directory: Path | None = None, capacity: int = DEFAULT_CAPACITY) -> None:
        self._directory = Path(directory) if directory is not None else None
        self._capacity = max(1, capacity)
        self._captures: OrderedDict[str, Capture] = OrderedDict()
        self._lock = threading.Lock()
        self._counter = 0

    def add(self, capture: Capture) -> str:
        with self._lock:
            self._counter = (self._counter + 1) % 100
            capture_id = f"c-{datetime.now():%Y%m%d-%H%M%S}-{self._counter:02d}"
            capture.capture_id = capture_id
            self._captures[capture_id] = capture
            while len(self._captures) > self._capacity:
                self._captures.popitem(last=False)
        if self._directory is not None:
            try:
                capture.csv_path = str(self._write_csv(capture))
            except OSError as exc:
                capture.warnings.append(f"CSV nicht geschrieben: {exc}")
        return capture_id

    def get(self, capture_id: str) -> Capture:
        with self._lock:
            capture = self._captures.get(capture_id)
        if capture is None:
            raise ScopeError("unknown_capture",
                             f"Erfassung {capture_id!r} ist nicht (mehr) im Speicher. Die CSV-Datei kann "
                             "noch vorhanden sein.", capacity=self._capacity)
        return capture

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._captures)

    def excerpt(self, capture_id: str, channel: str | None = None, t_start: float | None = None,
                t_stop: float | None = None, max_points: int = 500, mode: str = "minmax") -> dict:
        capture = self.get(capture_id)
        names = list(capture.channels)
        if channel is not None:
            channel = channel.upper()
            if channel not in capture.channels:
                raise ScopeError("invalid_request", f"Kanal {channel!r} nicht in der Erfassung.", allowed=names)
            names = [channel]
        max_points = int(max(2, min(max_points, 10_000)))
        t = capture.time_s
        mask = np.ones(t.size, dtype=bool)
        if t_start is not None:
            mask &= t >= t_start
        if t_stop is not None:
            mask &= t <= t_stop
        if mask.sum() < 1:
            raise ScopeError("invalid_request", "Im Zeitfenster liegen keine Samples.",
                             t_min=analysis.num(t[0]), t_max=analysis.num(t[-1]))
        result = {
            "capture_id": capture_id,
            "sample_interval_s": analysis.num(capture.sample_interval_s),
            "samples_in_window": int(mask.sum()),
            "csv_path": capture.csv_path,
            "channels": {},
        }
        for name in names:
            result["channels"][name] = analysis.decimate(t[mask], capture.channels[name].volts[mask],
                                                         max_points, mode)
        return result

    def measure(self, capture_id: str, items, channel: str | None = None, t_start: float | None = None,
                t_stop: float | None = None) -> dict:
        capture = self.get(capture_id)
        names = [channel.upper()] if channel else list(capture.channels)
        t = capture.time_s
        result: dict = {"capture_id": capture_id, "window": {"t_start_s": t_start, "t_stop_s": t_stop},
                        "channels": {}}
        for name in names:
            if name not in capture.channels:
                raise ScopeError("invalid_request", f"Kanal {name!r} nicht in der Erfassung.",
                                 allowed=list(capture.channels))
            ch = capture.channels[name]
            values, reasons = analysis.measure(t, ch.volts, items, t_start, t_stop,
                                               min_amplitude=analysis.noise_floor(ch.range_v))
            entry = dict(values)
            if reasons:
                entry["unavailable"] = reasons
            result["channels"][name] = entry
        return result

    def _write_csv(self, capture: Capture) -> Path:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{capture.capture_id}.csv"
        names = list(capture.channels)
        header = [
            f"# scope={capture.scope_id} model={capture.model} timestamp={capture.timestamp}",
            f"# sample_interval_s={capture.sample_interval_s!r} trigger_index={capture.trigger_index} "
            f"triggered={capture.triggered}",
            "# " + " ".join(f"{n}: range_v={c.range_v:g} coupling={c.coupling} probe={c.probe:g} "
                            f"overrange={c.overrange}" for n, c in capture.channels.items()),
        ]
        columns = [capture.time_s] + [capture.channels[n].volts for n in names]
        data = np.column_stack(columns)
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write("\n".join(header) + "\n")
            handle.write("time_s," + ",".join(f"{n}_V" for n in names) + "\n")
            np.savetxt(handle, data, delimiter=",", fmt="%.9g")
        return path
