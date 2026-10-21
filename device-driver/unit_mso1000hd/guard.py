"""Wer spricht gerade mit welchem MSO1000HD?

Über LAN (VXI-11) ist das Gerät nicht exklusiv wie das PicoScope. Trotzdem
sollen sich zwei Stellen in LabControl nicht ins Wort fallen: die Erkennung im
DeviceWorker (kurz öffnen, *IDN?, schließen) und der ScopeService
(Erfassungen über Netzwerk/MCP, hält die Verbindung bis 60 s Leerlauf). Hält
der ScopeService das Gerät, lässt die Erkennung es in Ruhe -- es ist ja
offensichtlich erreichbar. Je Gerät ein Wächter, nicht blockierend.
"""
from __future__ import annotations

import threading

OWNER_WORKER = "worker"
OWNER_SERVICE = "mcp"


class _Guard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.owner = ""

    def try_acquire(self, owner: str) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.owner = owner
        return True

    def release(self) -> None:
        self.owner = ""
        self._lock.release()


_GUARDS: dict[str, _Guard] = {}
_GUARDS_LOCK = threading.Lock()


def guard_for(device_id: str) -> _Guard:
    with _GUARDS_LOCK:
        guard = _GUARDS.get(device_id)
        if guard is None:
            guard = _GUARDS[device_id] = _Guard()
        return guard
