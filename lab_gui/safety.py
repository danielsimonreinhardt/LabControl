"""Software-Watchdog: geraete-individuelle Sicherheits-Grenzwerte, unabhaengig
vom Testcase-Schritt.

Ergaenzt die Pass/Fail-Pruefungen aus testcase_runner.py (die nur einzelne
Aktionsschritte betreffen) um eine durchgehende Ueberwachung: sobald eine
Last- oder Netzteil-Messung einen fuer GENAU DIESES Geraet aktivierten
Grenzwert (Spannung/Strom/Leistung) ueberschreitet, werden ALLE Ausgaenge
sofort abgeschaltet -- unabhaengig davon, welcher Testschritt (falls
ueberhaupt einer) gerade laeuft. Die Grenzwerte selbst sind pro Geraete-ID
konfigurierbar (siehe device_safety_limits()/set_safety_limit() in
settings.py) statt geraeteartweit gemeinsam, damit z.B. zwei baugleiche
Netzteile unterschiedliche Schwellen bekommen koennen. Waehrend eines
Testlaufs wird zusaetzlich der Verbindungsstatus der beteiligten Geraete
ueberwacht (Stale-Data/Disconnect), siehe begin_run_supervision().

Der Monitor selbst loest nur aus (tripped/all_off_requested) -- das
tatsaechliche Abschalten uebernimmt DeviceWorker.all_outputs_off() im
Worker-Thread (siehe main_window._wire_safety).

Ein Trip ist "latchend": er bleibt bestehen, bis acknowledge() aufgerufen
wird (Quittieren-Button im Trip-Banner), damit ein unbeaufsichtigter Lauf
nicht unbemerkt automatisch weiterlaeuft. Ein Temperaturkanal liesse sich
spaeter einfach als zusaetzliches Feld in SAFETY_LIMIT_FIELDS ergaenzen --
_check() ist bereits generisch ueber ein field->value-Dict.
"""
from __future__ import annotations

import copy
import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from i18n import tr
from testcase_model import COND_FIELD_LABELS

logger = logging.getLogger(__name__)

# field -> (Einheit, Spin-Minimum, Spin-Maximum, Default-Wert). Einheiten
# sprachunabhaengig wie COND_FIELD_UNITS in testcase_model.py. Die
# Default-Werte entsprechen den Geraetemaxima aus
# testcase_model.ACTION_VALUE_RANGE (VOLT/CURR/POW bzw. PSU_VOLT/PSU_CURR).
SAFETY_LIMIT_FIELDS: dict[str, list[tuple[str, str, float, float, float]]] = {
    "load": [
        ("max_voltage", "V", 0, 150, 150),
        ("max_current", "A", 0, 40, 40),
        ("max_power", "W", 0, 300, 300),
    ],
    "psu": [
        ("max_voltage", "V", 0, 60, 60),
        ("max_current", "A", 0, 10, 10),
    ],
}

# Wie testcase_runner.MEASUREMENT_STALE_S: bewusst als absoluter Wert
# (deutlich groesser als device_worker.POLL_INTERVAL_MS, auch nach dessen
# Absenkung auf 100ms) statt an das Poll-Intervall gekoppelt -- toleriert
# einzelne verpasste/verzoegerte Zyklen (z.B. wenn ein Poll wegen mehrerer
# Geraete laenger braucht als das Timer-Intervall), ohne eine tatsaechlich
# getrennte/eingefrorene Quelle zu uebersehen.
STALE_TIMEOUT_S = 2.0
STALE_CHECK_MS = 500

# Aktions-Feldname (siehe SAFETY_LIMIT_FIELDS) -> Messgroesse im
# Messwert-Dict, das on_load_measurement/on_psu_measurement an _check()
# uebergeben.
_FIELD_TO_MEASURE = {"max_voltage": "voltage", "max_current": "current", "max_power": "power"}


def device_kind(device_id: str) -> str:
    """Leitet die Geraeteart aus einer Device-ID ab (Praefix vor dem ':',
    siehe device_worker._resolve_device_ids)."""
    kind, _, _ = device_id.partition(":")
    return kind


def default_device_limits(kind: str) -> dict:
    """Frisches Grenzwert-Dict fuer EIN Geraet dieser Art, alle Felder
    deaktiviert (Default-Werte)."""
    return {
        field: {"enabled": False, "value": default}
        for field, _unit, _lo, _hi, default in SAFETY_LIMIT_FIELDS.get(kind, [])
    }


class SafetyMonitor(QObject):
    # "off" (kein Limit aktiv) | "armed" (mind. 1 Limit aktiv) | "tripped"
    state_changed = Signal(str)
    tripped = Signal(str, str)  # device_id, Grund (bereits uebersetzt)
    all_off_requested = Signal(str)  # Grund -> DeviceWorker.all_outputs_off

    def __init__(self, limits: dict | None = None) -> None:
        super().__init__()
        # device_id -> {field: {"enabled": bool, "value": float}} -- pro
        # Geraete-ID statt geraeteartweit (siehe Modul-Docstring).
        self._limits: dict[str, dict] = limits if limits is not None else {}
        self._tripped = False
        self._last_seen: dict[str, float] = {}
        self._supervised: set[str] = set()

        self._stale_timer = QTimer(self)
        self._stale_timer.setInterval(STALE_CHECK_MS)
        self._stale_timer.timeout.connect(self._check_stale)

    # -- Grenzwerte -------------------------------------------------------

    @Slot(dict)
    def set_limits(self, limits: dict) -> None:
        self._limits = copy.deepcopy(limits)
        self.state_changed.emit(self._state())

    def is_tripped(self) -> bool:
        return self._tripped

    def current_state(self) -> str:
        return self._state()

    @Slot()
    def acknowledge(self) -> None:
        if not self._tripped:
            return
        self._tripped = False
        self.state_changed.emit(self._state())

    def _state(self) -> str:
        if self._tripped:
            return "tripped"
        for fields in self._limits.values():
            if any(entry.get("enabled") for entry in fields.values()):
                return "armed"
        return "off"

    # -- Messwerte ----------------------------------------------------------

    @Slot(str, float, float, float)
    def on_load_measurement(self, device_id: str, voltage: float, current: float, power: float) -> None:
        self._last_seen[device_id] = time.monotonic()
        self._check("load", device_id, {"voltage": voltage, "current": current, "power": power})

    @Slot(str, float, float, bool)
    def on_psu_measurement(
        self, device_id: str, voltage: float, current: float, _constant_current: bool
    ) -> None:
        self._last_seen[device_id] = time.monotonic()
        self._check("psu", device_id, {"voltage": voltage, "current": current})

    def _check(self, kind: str, device_id: str, values: dict[str, float]) -> None:
        if self._tripped:
            return
        limits = self._limits.get(device_id, {})
        for field, measure_key in _FIELD_TO_MEASURE.items():
            entry = limits.get(field)
            if entry is None or not entry.get("enabled"):
                continue
            measured = values.get(measure_key)
            if measured is None:
                continue
            threshold = entry["value"]
            if measured > threshold:
                unit = next((u for f, u, *_ in SAFETY_LIMIT_FIELDS[kind] if f == field), "")
                reason = tr(
                    "Grenzwert überschritten: {device_id} {measure} = {measured:.3g} {unit} > {limit:.3g} {unit}",
                    device_id=device_id,
                    measure=tr(COND_FIELD_LABELS.get(measure_key, measure_key)),
                    measured=measured,
                    limit=threshold,
                    unit=unit,
                )
                self._trip(device_id, reason)
                return

    # -- Verbindungsueberwachung (nur waehrend eines Testlaufs) ------------

    def begin_run_supervision(self, device_ids: set[str]) -> None:
        now = time.monotonic()
        self._supervised = set(device_ids)
        for device_id in self._supervised:
            # Seed verhindert einen Sofort-Trip, bevor die erste Messung
            # dieses Laufs eingetroffen ist.
            self._last_seen.setdefault(device_id, now)
        if self._supervised:
            self._stale_timer.start()

    def end_run_supervision(self) -> None:
        self._supervised.clear()
        self._stale_timer.stop()

    def _check_stale(self) -> None:
        if self._tripped or not self._supervised:
            return
        now = time.monotonic()
        for device_id in list(self._supervised):
            last = self._last_seen.get(device_id)
            if last is None or (now - last) > STALE_TIMEOUT_S:
                self._trip(device_id, tr("Messwerte veraltet für Gerät '{device_id}'", device_id=device_id))
                return

    @Slot(str, str)
    def on_device_removed(self, _kind: str, device_id: str) -> None:
        self._last_seen.pop(device_id, None)
        if self._tripped or device_id not in self._supervised:
            return
        self._trip(device_id, tr("Gerät '{device_id}' während des Testlaufs getrennt", device_id=device_id))

    # -- Ausloesen -----------------------------------------------------------

    def _trip(self, device_id: str, reason: str) -> None:
        if self._tripped:
            return
        self._tripped = True
        logger.error("Sicherheitsabbruch: %s", reason)
        self.all_off_requested.emit(reason)
        self.tripped.emit(device_id, reason)
        self.state_changed.emit(self._state())
