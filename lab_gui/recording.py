"""Recorder: sammelt Zeitstempel/Geraet/Wert-Tripel aller Messwerte waehrend
einer laufenden Aufzeichnung, als Grundlage fuer den Export (siehe
recording_export.py).

Haengt -- analog zu TimelineTab -- direkt an DeviceWorker.load_measurement/
psu_measurement sowie DeviceRegistry.device_known/label_changed, sammelt aber
nur, waehrend start()/stop() aktiv ist (kein Ringpuffer, keine Deckelung: eine
bewusst gestartete Aufzeichnung soll nicht durch ein Zeitfenster beschnitten
werden). Laeuft in einer eigenen Long-Format-Liste (Zeitstempel, Geraet,
Kanal, Wert) statt geraetespezifischer Felder, damit Last und Netzteil sowie
eine beliebige Anzahl Geraete einheitlich behandelt werden koennen.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot

STATS_INTERVAL_MS = 500

# (kind, field) -> (deutscher Basis-Anzeigename, Einheit) -- analog zu
# timeline_tab.KIND_FIELDS, hier zusaetzlich als flaches Mapping fuer den
# Export (CSV-Spalte/MF4-Kanalname) gebraucht.
FIELD_INFO: dict[tuple[str, str], tuple[str, str]] = {
    ("load", "voltage"): ("Spannung", "V"),
    ("load", "current"): ("Strom", "A"),
    ("load", "power"): ("Leistung", "W"),
    ("psu", "voltage"): ("Spannung", "V"),
    ("psu", "current"): ("Strom", "A"),
}


@dataclass(frozen=True)
class Sample:
    t: float  # time.time()-Sekunden
    device_id: str
    field: str
    value: float


class Recorder(QObject):
    recording_changed = Signal(bool)
    stats_changed = Signal(int, float)  # sample_count, elapsed_s

    def __init__(self) -> None:
        super().__init__()
        self._active = False
        self._samples: list[Sample] = []
        self._device_meta: dict[str, tuple[str, str]] = {}  # device_id -> (kind, label)
        self._start_time: float | None = None
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._emit_stats)

    @property
    def is_recording(self) -> bool:
        return self._active

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def samples(self) -> list[Sample]:
        return list(self._samples)

    def device_meta(self) -> dict[str, tuple[str, str]]:
        return dict(self._device_meta)

    # -- Geraeteregistrierung (von MainWindow/DeviceRegistry gespeist) --------

    @Slot(str, str, str)
    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        self._device_meta[device_id] = (kind, label)

    @Slot(str, str, str)
    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        self._device_meta[device_id] = (kind, label)

    # -- Steuerung -------------------------------------------------------------

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._start_time = time.time()
        self._stats_timer.start(STATS_INTERVAL_MS)
        self.recording_changed.emit(True)
        self._emit_stats()

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        self._stats_timer.stop()
        self.recording_changed.emit(False)
        self._emit_stats()

    def clear(self) -> None:
        self._samples.clear()
        self._start_time = None
        self._emit_stats()

    # -- Messwerte -----------------------------------------------------------

    @Slot(str, float, float, float)
    def on_load_measurement(self, device_id: str, voltage: float, current: float, power: float) -> None:
        self._append(device_id, "voltage", voltage)
        self._append(device_id, "current", current)
        self._append(device_id, "power", power)

    @Slot(str, float, float, bool)
    def on_psu_measurement(self, device_id: str, voltage: float, current: float, constant_current: bool) -> None:
        self._append(device_id, "voltage", voltage)
        self._append(device_id, "current", current)

    def _append(self, device_id: str, field: str, value: float) -> None:
        if not self._active:
            return
        self._samples.append(Sample(time.time(), device_id, field, value))

    def _emit_stats(self) -> None:
        elapsed = (time.time() - self._start_time) if self._start_time is not None else 0.0
        self.stats_changed.emit(len(self._samples), elapsed)

    # -- Export ----------------------------------------------------------------
    # Delegiert an recording_export.py, aber als Methode hier, damit Aufrufer
    # (RecordingTab/MainWindow) nicht selbst auf die interne Sample-Liste
    # zugreifen muessen -- ein Export liest immer den zum Aufrufzeitpunkt
    # aktuellen Stand, auch waehrend eine Aufnahme noch laeuft.

    def export_csv(self, path: Path) -> None:
        import recording_export  # lokal: vermeidet Zirkelimport (recording_export importiert Sample/FIELD_INFO von hier)

        recording_export.export_csv(path, self._samples, self._device_meta)

    def export_mf4(self, path: Path) -> None:
        import recording_export

        recording_export.export_mf4(path, self._samples, self._device_meta)
