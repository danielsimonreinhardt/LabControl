"""Register der Testlaeufe fuer REST, MCP und die Kommandozeile ueber die laufende Instanz (0.59.0).

Qt-frei und thread-sicher: der GUI-Thread traegt Laeufe ein und fuehrt ihren Stand nach (MainWindow),
der HTTP-Server-Thread liest (share_api /api/v1/runs). Jeder Lauf, auch einer aus dem Fenster, steht
hier; nach dem Ende liegen run-result.json, JUnit und HTML-Bericht als Dateien im Berichtsordner
(Netzlaufwerk ueber data_store) und sind ueber die API abrufbar.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_RUNS = 30            # im Speicher gehaltene Laeufe (die Dateien bleiben)
MAX_EVENTS = 200         # Meldungen je Lauf fuer die Abfrage


@dataclass
class RunEntry:
    run_id: str
    testcase: str
    source: str                      # "gui" oder Aufrufer (Adresse/Programm) bei REST
    started: float = field(default_factory=time.time)
    ended: float = 0.0
    status: str = "running"          # running | passed | failed_checks | error | stopped
    exit_code: int | None = None
    step: int = 0                    # 1-basiert, 0 = noch keiner
    steps_total: int = 0
    checks_total: int = 0
    checks_failed: int = 0
    error: str = ""
    events: list[dict] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)   # "result" | "junit" | "report" -> Pfad

    def public(self, with_events: int = -1) -> dict:
        data = {
            "id": self.run_id, "testcase": self.testcase, "source": self.source,
            "started": _iso(self.started), "ended": _iso(self.ended) if self.ended else None,
            "status": self.status, "running": self.status == "running", "exit_code": self.exit_code,
            "step": self.step, "steps_total": self.steps_total,
            "checks_total": self.checks_total, "checks_failed": self.checks_failed,
            "error": self.error or None, "files": sorted(self.files),
        }
        if with_events >= 0:
            data["events"] = self.events[with_events:]
            data["events_next"] = len(self.events)
        return data


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


class RunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, RunEntry] = {}
        self._order: list[str] = []
        self._counter = itertools.count(1)
        self._current: str | None = None

    # -- GUI-Thread -------------------------------------------------------------

    def begin(self, testcase: str, source: str, steps_total: int) -> str:
        run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{next(self._counter)}"
        with self._lock:
            self._runs[run_id] = RunEntry(run_id, testcase, source, steps_total=steps_total)
            self._order.append(run_id)
            self._current = run_id
            while len(self._order) > MAX_RUNS:
                self._runs.pop(self._order.pop(0), None)
        return run_id

    def current_id(self) -> str | None:
        with self._lock:
            return self._current

    def _event(self, entry: RunEntry, kind: str, **data) -> None:
        if len(entry.events) < MAX_EVENTS:
            entry.events.append({"t": round(time.time() - entry.started, 3), "kind": kind, **data})

    def step_started(self, run_id: str, index: int) -> None:
        with self._lock:
            entry = self._runs.get(run_id)
            if entry is not None:
                entry.step = index + 1

    def step_result(self, run_id: str, index: int, passed: bool, value: float) -> None:
        with self._lock:
            entry = self._runs.get(run_id)
            if entry is None:
                return
            entry.checks_total += 1
            entry.checks_failed += 0 if passed else 1
            self._event(entry, "result", step=index + 1, passed=passed, value=value)

    def step_failed(self, run_id: str, index: int, message: str) -> None:
        with self._lock:
            entry = self._runs.get(run_id)
            if entry is not None:
                entry.error = f"Schritt {index + 1}: {message}"
                self._event(entry, "failed", step=index + 1, message=message)

    def finish(self, run_id: str, status: str, exit_code: int, files: dict[str, str]) -> None:
        with self._lock:
            entry = self._runs.get(run_id)
            if entry is None:
                return
            entry.status = status
            entry.exit_code = exit_code
            entry.ended = time.time()
            entry.files = dict(files)
            self._event(entry, "end", status=status, exit_code=exit_code)
            if self._current == run_id:
                self._current = None

    def relocate(self, old: Path, new: Path) -> None:
        """data_store hat eine Datei aufs Netzlaufwerk geschoben (aus dessen Thread)."""
        old_s, new_s = str(old), str(new)
        with self._lock:
            for entry in self._runs.values():
                for key, path in entry.files.items():
                    if path == old_s:
                        entry.files[key] = new_s

    # -- Server-Thread ----------------------------------------------------------

    def get(self, run_id: str, with_events: int = -1) -> dict | None:
        with self._lock:
            entry = self._runs.get(run_id)
            return entry.public(with_events) if entry is not None else None

    def list(self) -> list[dict]:
        with self._lock:
            return [self._runs[r].public() for r in reversed(self._order)]

    def file(self, run_id: str, kind: str) -> Path | None:
        with self._lock:
            entry = self._runs.get(run_id)
            path = entry.files.get(kind) if entry is not None else None
        return Path(path) if path else None
