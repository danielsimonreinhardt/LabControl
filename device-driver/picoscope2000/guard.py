"""Wer hält gerade das exklusive PicoScope-Handle?

ps2000_open_unit() belegt das Gerät exklusiv. Innerhalb von LabControl öffnen
es zwei Stellen in verschiedenen Threads: der DeviceWorker (Erkennung,
Testablauf-Session, einzelne PICO_*-Aktion) und der ScopeService
(lab_gui/scope_service.py, Erfassungen über Netzwerk/MCP). Beide fragen vor
jedem Öffnen hier an und geben nach dem Schließen wieder frei. Wer das Handle
nicht bekommt, behandelt das wie „belegt“ -- ohne zu warten, denn ein Warten
im DeviceWorker würde dessen Poll-Zyklus einfrieren.

Außerdem hält der Wächter den Zeitpunkt des letzten Schließens für beide
fest: ein erneutes Öffnen direkt danach scheitert am echten Gerät (siehe
device_worker.PICOSCOPE_SETTLE_S). Nur für das echte Gerät; der Mock kennt
keine Exklusivität.
"""
from __future__ import annotations

import threading
import time

# Mindestabstand zwischen Schließen und erneutem Öffnen (am 2204A beobachtet:
# ~0 s scheitert, ab 0,5-1 s klappt es; doppelte Marge).
SETTLE_S = 2.0

OWNER_WORKER = "worker"
OWNER_SERVICE = "mcp"


class HandleGuard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner = ""
        self._last_close = 0.0

    @property
    def owner(self) -> str:
        """"" wenn frei, sonst OWNER_WORKER oder OWNER_SERVICE."""
        return self._owner

    def try_acquire(self, owner: str) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self._owner = owner
        return True

    def release(self, closed: bool = True) -> None:
        """closed=False, wenn das Öffnen gescheitert ist (kein Schließen nötig)."""
        if closed:
            self._last_close = time.monotonic()
        self._owner = ""
        self._lock.release()

    def settle(self) -> None:
        """Wartet, bis seit dem letzten Schließen SETTLE_S vergangen sind."""
        wait = SETTLE_S - (time.monotonic() - self._last_close)
        if wait > 0:
            time.sleep(wait)


GUARD = HandleGuard()
