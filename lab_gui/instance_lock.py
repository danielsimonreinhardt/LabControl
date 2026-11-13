"""Sperre gegen eine zweite LabControl-Instanz auf demselben Datenordner (0.58.0).

Zwei Instanzen wuerden dieselben seriellen Ports und CAN-Adapter doppelt oeffnen. Fenster (main.py) und
Kommandozeile (headless.py) holen dieselbe Sperrdatei im Datenordner; laeuft schon eine Instanz, startet
die zweite nicht. Ein abgestuerzter Prozess hinterlaesst keine Sperre: QLockFile erkennt die Datei eines
nicht mehr laufenden Prozesses auf demselben Rechner als verwaist.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLockFile

from paths import app_dir

LOCK_NAME = "labcontrol.lock"


class InstanceLock:
    def __init__(self, folder: Path | None = None) -> None:
        self.path = Path(folder or app_dir()) / LOCK_NAME
        self._lock = QLockFile(str(self.path))
        self._lock.setStaleLockTime(0)   # nur "Prozess tot" macht die Sperre verwaist, nicht ihr Alter

    def acquire(self, timeout_ms: int = 100) -> bool:
        return self._lock.tryLock(timeout_ms)

    def holder(self) -> str:
        """"PID 1234 (python)" der laufenden Instanz, oder ""."""
        info = self._lock.getLockInfo()   # PySide6: (pid, hostname, appname), pid 0 = unbekannt
        pid, app = (info[0], info[2]) if len(info) == 3 else (info[1], info[3])
        return f"PID {pid} ({app})" if pid else ""

    def release(self) -> None:
        if self._lock.isLocked():
            self._lock.unlock()
