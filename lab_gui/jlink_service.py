"""J-Link als Geraet in LabControl (Verbund V3, PI-31 a).

Eigener Dienst statt eines weiteren Geraets im DeviceWorker: Flashen dauert bis zu einige Minuten,
und waehrenddessen muss der Abfrage-Zyklus von Netzteilen und Last weiterlaufen (Sicherheits-
Watchdog). Jede Bedienung laeuft deshalb in einem eigenen Thread; es laeuft hoechstens eine
gleichzeitig (der J-Link ist ein einzelnes Geraet).

Kein Hintergrund-Abfragen der Sonde (siehe device-driver/jlink/driver.py): die Kachel zeigt den
Stand der letzten Bedienung; "Verbindung prüfen" fragt auf Wunsch nach.

Jede Bedienung steht im Log wie bei den anderen Geraeten (PI-15), beim Flashen mit Dateiname und
SHA-256 (PI-31 a: "Prüfsumme ins Log").
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal

from jlink.driver import JLink, Result, find_commander
from jlink.mock import MockJLink

logger = logging.getLogger(__name__)

ACTIONS = ("JLINK_CHECK", "JLINK_FLASH", "JLINK_RESET", "JLINK_HALT", "JLINK_GO")
ACTION_TEXT = {
    "JLINK_CHECK": "Verbindung prüfen",
    "JLINK_FLASH": "Firmware flashen",
    "JLINK_RESET": "Reset",
    "JLINK_HALT": "Anhalten",
    "JLINK_GO": "Starten",
}
SIM_ID = "jlink:SIM"


def device_id_for(config: dict) -> str:
    return f"jlink:{config.get('serial') or 'usb'}"


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


class JLinkService(QObject):
    # Geraete kommen/gehen (Einstellungen, Simulation): kind, device_id
    device_added = Signal(str, str)
    device_removed = Signal(str, str)
    # device_id, Zustand {"busy", "action", "ok", "message", "vtref", "core", "serial", "firmware",
    #                     "device", "interface", "speed", "last_file", "time"}
    state_changed = Signal(str, dict)
    # Anfrage-Nr., device_id, Aktion, ok, Meldung
    finished = Signal(int, str, str, bool, str)

    _done = Signal(int, str, str, object, str)   # intern aus dem Arbeits-Thread

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._probes: dict[str, object] = {}       # device_id -> JLink | MockJLink
        self._states: dict[str, dict] = {}
        self._busy: dict[str, bool] = {}
        self._running: dict[str, int] = {}         # device_id -> Anfrage-Nr. der laufenden Bedienung
        self._requests = 0
        # Immer queued: auch ein sofort erkannter Fehler kommt erst nach run() an (Aufrufer kennt die Nummer)
        self._done.connect(self._on_done, Qt.ConnectionType.QueuedConnection)

    # -- Konfiguration -----------------------------------------------------------

    def apply(self, config: dict, simulation: bool) -> None:
        """config: Settings.jlink_config ({"enabled", "device", "interface", "speed", "serial",
        "commander"}). In der Simulation gibt es zusaetzlich jlink:SIM."""
        wanted: dict[str, object] = {}
        if config.get("enabled"):
            commander = find_commander(config.get("commander", ""))
            wanted[device_id_for(config)] = JLink(commander, config.get("device", ""),
                                                  config.get("interface", "SWD"),
                                                  int(config.get("speed", 4000)), config.get("serial", ""))
        if simulation:
            wanted[SIM_ID] = self._probes.get(SIM_ID) or MockJLink()
        for device_id in [d for d in self._probes if d not in wanted]:
            del self._probes[device_id]
            self.device_removed.emit("jlink", device_id)
        for device_id, probe in wanted.items():
            new = device_id not in self._probes
            self._probes[device_id] = probe
            state = self._states.setdefault(device_id, {})
            state.update({"device": getattr(probe, "device", ""), "interface": getattr(probe, "interface", ""),
                          "speed": getattr(probe, "speed_khz", 0), "commander": getattr(probe, "commander", "")})
            if new:
                self.device_added.emit("jlink", device_id)
            self.state_changed.emit(device_id, dict(state))

    def device_ids(self) -> list[str]:
        return list(self._probes)

    def probe(self, device_id: str):
        return self._probes.get(device_id)

    def state(self, device_id: str) -> dict:
        return dict(self._states.get(device_id, {}))

    def is_busy(self, device_id: str) -> bool:
        return self._busy.get(device_id, False)

    # -- Bedienen ----------------------------------------------------------------

    def run(self, device_id: str, action: str, file_path: str = "", source: str = "Kachel") -> int:
        """Startet eine Bedienung. Liefert die Anfrage-Nr. (in finished). Ein Fehler vor dem Start
        (unbekanntes Geraet, beschaeftigt) kommt ebenfalls ueber finished -- im naechsten Durchlauf
        der Ereignisschleife, damit der Aufrufer die Nummer vorher kennt."""
        self._requests += 1
        request = self._requests
        probe = self._probes.get(device_id)
        problem = ""
        if probe is None:
            problem = f"J-Link '{device_id}' nicht eingerichtet"
        elif action not in ACTIONS:
            problem = f"Unbekannte Aktion '{action}' für J-Link"
        elif self._busy.get(device_id):
            problem = "J-Link ist noch mit der vorigen Bedienung beschäftigt"
        elif action == "JLINK_FLASH" and not file_path:
            problem = "Keine Firmware-Datei angegeben"
        if problem:
            self._done.emit(request, device_id, action, Result(False, problem), source)
            return request
        self._busy[device_id] = True
        self._running[device_id] = request
        state = self._states.setdefault(device_id, {})
        state.update({"busy": True, "action": action, "message": "", "last_file": file_path or state.get("last_file", "")})
        self.state_changed.emit(device_id, dict(state))

        def work() -> None:
            extra = ""
            try:
                if action == "JLINK_FLASH":
                    try:
                        extra = f"datei={os.path.basename(file_path)}, sha256={file_sha256(file_path)[:16]}…"
                    except OSError:
                        extra = f"datei={file_path}"
                result = {
                    "JLINK_CHECK": probe.check_target,
                    "JLINK_FLASH": lambda: probe.flash(file_path),
                    "JLINK_RESET": probe.reset,
                    "JLINK_HALT": probe.halt,
                    "JLINK_GO": probe.go,
                }[action]()
            except Exception as exc:  # noqa: BLE001 -- der Thread darf nie still sterben
                logger.exception("J-Link %s: %s fehlgeschlagen", device_id, action)
                result = Result(False, str(exc))
            result.output = extra   # fuers Log; die volle Ausgabe braucht niemand weiter
            self._done.emit(request, device_id, action, result, source)

        threading.Thread(target=work, name=f"JLink-{action}", daemon=True).start()
        return request

    def _on_done(self, request: int, device_id: str, action: str, result: Result, source: str) -> None:
        if self._running.get(device_id) != request:
            # Vor dem Start abgewiesen (z. B. beschaeftigt): laufende Bedienung und Kachel unberuehrt lassen
            logger.info("J-Link %s: %s per %s -> FEHLER [%s]", device_id, action, source, result.message)
            self.finished.emit(request, device_id, action, False, result.message)
            return
        self._running.pop(device_id, None)
        self._busy[device_id] = False
        state = self._states.setdefault(device_id, {})
        info = result.info
        state.update({"busy": False, "action": action, "ok": result.ok, "message": result.message,
                      "time": time.strftime("%H:%M:%S")})
        for key in ("vtref", "core", "serial", "firmware"):
            value = getattr(info, key, None)
            if value not in (None, ""):
                state[key] = value
        details = f" ({result.output})" if result.output else ""
        outcome = "ok" if result.ok else f"FEHLER [{result.message}]"
        logger.info("J-Link %s: %s%s per %s -> %s", device_id, action, details, source, outcome)
        self.state_changed.emit(device_id, dict(state))
        self.finished.emit(request, device_id, action, result.ok, result.message)
