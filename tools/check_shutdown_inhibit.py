"""Prueft die logind-Sperre fuers Herunterfahren (lab_gui/shutdown_inhibit.py, 10.11.2026).

Anlass: Am Pi endet beim Neustart aus dem Desktop der Wayland-Compositor vor LabControl; Qt
beendet den Prozess dann ohne closeEvent (keine Netzteil-Freigabe). Die Sperre sorgt dafuer,
dass LabControl schon bei PrepareForShutdown schliesst.

- PrepareForShutdown(true) ruft close genau einmal (auch bei Wiederholung)
- PrepareForShutdown(false) (abgebrochen) erneuert die Sperre, schliesst nicht
- Signal nicht abonnierbar / keine Sperre -> install liefert None, Warnung
- shutdown_signals.once: Signal und logind zusammen schliessen nur einmal
- nicht Linux -> install tut nichts
- nur Linux mit System-D-Bus und logind: echte Sperre, in "systemd-inhibit --list" sichtbar

Offscreen, keine Hardware.  Aufruf:  python tools/check_shutdown_inhibit.py
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


class FakeError:
    def message(self) -> str:
        return "kein Dienst"


class FakeBus:
    def __init__(self, connect_ok: bool = True) -> None:
        self.connect_ok = connect_ok
        self.connected: list[tuple] = []

    def connect(self, *args) -> bool:
        self.connected.append(args)
        return self.connect_ok

    def lastError(self) -> FakeError:
        return FakeError()


class FakeLock:
    def isValid(self) -> bool:
        return True


def main() -> int:
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    import shutdown_inhibit as si
    import shutdown_signals

    print("Ablauf mit nachgebildetem logind")
    calls: list[str] = []
    takes: list[int] = []

    def fake_take(self) -> bool:
        takes.append(1)
        self._lock = FakeLock()
        return True

    real_take = si.ShutdownInhibitor._take_lock
    si.ShutdownInhibitor._take_lock = fake_take
    try:
        bus = FakeBus()
        inh = si.ShutdownInhibitor(lambda: calls.append("close"), bus)
        check("start: Signal abonniert und Sperre genommen", inh.start() and inh.active and len(takes) == 1)
        check("abonniert PrepareForShutdown bei logind",
              bus.connected and bus.connected[0][:4] == (si.LOGIND_SERVICE, si.LOGIND_PATH,
                                                         si.LOGIND_INTERFACE, "PrepareForShutdown"))
        inh._lock = None
        inh.on_prepare_for_shutdown(False)
        app.processEvents()
        check("abgebrochen: kein Schliessen, Sperre erneuert", calls == [] and inh.active and len(takes) == 2)
        inh.on_prepare_for_shutdown(True)
        inh.on_prepare_for_shutdown(True)
        app.processEvents()
        check("Herunterfahren: close genau einmal", calls == ["close"], str(calls))
        inh.on_prepare_for_shutdown(False)
        check("nach dem Schliessen keine neue Sperre", len(takes) == 2)

        inh2 = si.ShutdownInhibitor(lambda: None, FakeBus(connect_ok=False))
        check("Signal nicht abonnierbar: start scheitert", not inh2.start() and not inh2.active)
    finally:
        si.ShutdownInhibitor._take_lock = real_take

    print("Signal und logind zusammen")
    closes: list[int] = []
    close_once = shutdown_signals.once(lambda: closes.append(1))
    inh3 = si.ShutdownInhibitor(close_once, FakeBus())
    close_once()                      # z. B. SIGTERM zuerst
    inh3.on_prepare_for_shutdown(True)
    app.processEvents()
    check("nur ein Schliessen", closes == [1], str(closes))

    print("Plattform")
    saved = sys.platform
    try:
        sys.platform = "win32"
        check("Windows: install tut nichts", si.install(lambda: None) is None)
    finally:
        sys.platform = saved

    if sys.platform.startswith("linux") and shutil.which("systemd-inhibit") and pathlib.Path("/run/systemd/system").exists():
        print("Echte logind-Sperre")
        inh4 = si.install(lambda: None, app)
        check("install: Sperre gehalten", inh4 is not None and inh4.active)
        listing = subprocess.run(["systemd-inhibit", "--list", "--no-pager"], capture_output=True, text=True).stdout
        mine = [line for line in listing.splitlines() if si.WHO in line and str(os.getpid()) in line]
        check("in systemd-inhibit --list als delay/shutdown", bool(mine) and "delay" in mine[0] and "shutdown" in mine[0],
              listing[-300:])
    else:
        print("  (keine echte logind-Sperre pruefbar: kein Linux mit systemd)")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
