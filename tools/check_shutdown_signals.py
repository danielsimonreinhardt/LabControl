"""Prueft das Beenden per Signal (SIGTERM, z.B. systemd stop auf dem Raspberry Pi, und Strg+C):
derselbe Weg wie das Schliessen des Fensters, also Netzteil-Freigabe (1 V / 0 A / Ausgang EIN).
Ein zweites Signal waehrend des Abbaus darf nicht erneut schliessen.
Offscreen, Simulationsmodus (MockHCS34xx), keine Hardware.

Aufruf:  python tools/check_shutdown_signals.py   (aus dem Repo-Stamm)
"""
from __future__ import annotations

import json
import os
import pathlib
import signal
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    import settings as settings_mod
    import device_registry as registry_mod
    tmp = pathlib.Path(tempfile.mkdtemp())
    settings_mod.SETTINGS_PATH = tmp / "settings.json"
    registry_mod.LABELS_PATH = tmp / "device_labels.json"
    settings_mod.SETTINGS_PATH.write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

    import device_worker as dw
    dw.KoradKEL102.discover_ports = staticmethod(lambda: [])
    dw.HCS34xx.discover_ports = staticmethod(lambda: [])
    dw.MicroHIL.discover = staticmethod(lambda: None)
    dw.JDS66xx.discover_ports = staticmethod(lambda: [])
    dw.picoscope_usb_present = lambda: False

    import shutdown_signals
    from main_window import MainWindow
    window = MainWindow(settings_mod.Settings())
    closes: list[float] = []

    def counted_close() -> None:
        closes.append(time.monotonic())
        window.close()

    timer = shutdown_signals.install(counted_close, app)
    window.show()

    def spin(seconds: float, until=None) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        return until() if until is not None else False

    check("Weck-Timer laeuft", timer.isActive() and timer.interval() == shutdown_signals.WAKE_INTERVAL_MS)
    check("SIGTERM und SIGINT abgefangen",
          signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, None)
          and signal.getsignal(signal.SIGINT) is not signal.default_int_handler)

    SIM = dw.SIM_PSU_ID
    spin(3.0, until=lambda: SIM in window._worker._psus)
    mock = window._worker._psus[SIM]
    window._worker.set_psu_voltage(SIM, 12.0)
    window._worker.set_psu_current(SIM, 3.0)
    window._worker.set_psu_output(SIM, False)
    spin(0.5)
    check("Ausgangslage 12 V / 3 A / AUS", mock.get_setpoint() == (12.0, 3.0) and mock.get_output() is False,
          f"{mock.get_setpoint()} {mock.get_output()}")

    print("SIGTERM")
    signal.raise_signal(signal.SIGTERM)
    spin(5.0, until=lambda: not window.isVisible())
    check("Fenster geschlossen", not window.isVisible())
    check("Netzteil freigegeben (1 V / 0 A / EIN)",
          mock.get_setpoint() == (dw.PSU_MIN_VOLTAGE, 0.0) and mock.get_output() is True,
          f"{mock.get_setpoint()} {mock.get_output()}")
    check("Worker-Thread beendet", not window._thread.isRunning())

    print("zweites Signal waehrend des Abbaus")
    signal.raise_signal(signal.SIGINT)
    spin(0.5)
    check("genau ein Schliessen", len(closes) == 1, str(len(closes)))

    print("Signal waehrend app.exec() (nur der Weck-Timer gibt Python dann die Kontrolle)")
    import threading
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QWidget
    plain = QWidget()
    plain.show()
    shutdown_signals.install(plain.close, app)
    threading.Timer(0.5, signal.raise_signal, args=(signal.SIGTERM,)).start()
    QTimer.singleShot(5000, app.quit)   # Notbremse
    started = time.monotonic()
    app.exec()
    elapsed = time.monotonic() - started
    check("Fenster per Signal im Event-Loop geschlossen", not plain.isVisible() and elapsed < 4.0, f"{elapsed:.2f} s")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
