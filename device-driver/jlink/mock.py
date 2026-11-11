"""Simulierter J-Link fuer den Simulationsmodus und die Pruefungen (gleiche Schnittstelle wie
driver.JLink). Merkt sich, was "geflasht" wurde, und ob der Kern laeuft."""
from __future__ import annotations

import os
import time

from .driver import FIRMWARE_SUFFIXES, ProbeInfo, Result


class MockJLink:
    def __init__(self, device: str = "S32K144", delay_s: float = 0.3) -> None:
        self.device = device
        self.interface = "SWD"
        self.speed_khz = 4000
        self.serial = "SIM"
        self.commander = "(Simulation)"
        self.delay_s = delay_s
        self.flashed: list[str] = []
        self.running = True
        self.resets = 0
        self.fail_next = ""     # Pruefungen: naechste Bedienung mit dieser Meldung scheitern lassen

    def _info(self, connected: bool) -> ProbeInfo:
        return ProbeInfo(serial="SIM", product="J-Link (Simulation)", firmware="Simulation",
                         vtref=3.3, core="Cortex-M4" if connected else "", connected=connected)

    def _result(self, connected: bool = True) -> Result:
        time.sleep(self.delay_s)
        if self.fail_next:
            message, self.fail_next = self.fail_next, ""
            return Result(False, message, "", self._info(False))
        return Result(True, "", "", self._info(connected))

    def probe_info(self) -> Result:
        return self._result(connected=False)

    def check_target(self) -> Result:
        return self._result()

    def flash(self, path: str) -> Result:
        if not os.path.isfile(path):
            return Result(False, f"Datei nicht gefunden: {path}")
        if not path.lower().endswith(FIRMWARE_SUFFIXES) or path.lower().endswith(".bin"):
            return Result(False, f"Unbekanntes Dateiformat ({os.path.basename(path)})")
        result = self._result()
        if result.ok:
            self.flashed.append(path)
            self.running = True
        return result

    def reset(self) -> Result:
        result = self._result()
        if result.ok:
            self.resets += 1
            self.running = True
        return result

    def halt(self) -> Result:
        result = self._result()
        if result.ok:
            self.running = False
        return result

    def go(self) -> Result:
        result = self._result()
        if result.ok:
            self.running = True
        return result
