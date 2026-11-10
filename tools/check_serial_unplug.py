"""Prueft das Abziehen serieller Geraete unter Linux (0.31.1, am Pi am 10.11.2026 gefunden).

Unter Linux meldet pyserial bei einem abgezogenen USB-Adapter tcflush (reset_input_buffer) als
termios.error -- kein OSError. Die Treiber fingen nur SerialException/OSError: Das Netzteil galt
weiter als verbunden, jeder Abfrage-Zyklus brach ab (nur auf stderr) und CAN/Batterie-Simulator
wurden danach nicht mehr abgefragt.

- jeder serielle Treiber macht aus SerialException, OSError und (Linux) termios.error seinen
  eigenen Verbindungsfehler
- DeviceWorker: ein unerwarteter Fehler an Netzteil oder Last trennt nur dieses Geraet, steht im
  Log, und der Zyklus fragt die uebrigen Geraete (CAN) trotzdem ab

Mock, keine Hardware.  Aufruf:  python tools/check_serial_unplug.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import serial  # noqa: E402
from PySide6.QtCore import QCoreApplication  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


class DeadPort:
    """Port eines abgezogenen Adapters: reset_input_buffer wirft wie pyserial unter Linux."""
    port = "/dev/ttyUSB0"

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def reset_input_buffer(self):
        raise self.exc

    def write(self, _data):
        raise self.exc

    def close(self):
        pass


def termios_error() -> BaseException | None:
    try:
        import termios
    except ImportError:
        return None
    return termios.error(5, "Eingabe-/Ausgabefehler")


def check_drivers() -> None:
    print("Treiber")
    from hcs34xx import driver as hcs
    from korad_kel102 import driver as kel
    from jds66xx import driver as jds
    from microhil import driver as hil

    cases = [
        ("Netzteil HCS-34xx", hcs, hcs.HCS34xx, hcs.PowerSupplyError, lambda d: d.get_display()),
        ("Last KEL102", kel, kel.KoradKEL102, kel.LoadError, lambda d: d.measure()),
        ("Funktionsgenerator JDS66xx", jds, jds.JDS66xx, jds.FunctionGeneratorError, lambda d: d.get_output(1)),
        ("microHIL", hil, hil.MicroHIL, hil.HilError, lambda d: d.get_inputs()),
    ]
    errors = [serial.SerialException("device disconnected"), OSError(5, "I/O")]
    t_err = termios_error()
    if t_err is not None:
        errors.append(t_err)
    for name, module, cls, err_cls, call in cases:
        check(f"{name}: SERIAL_ERRORS enthaelt termios.error (wo es das gibt)",
              t_err is None or type(t_err) in module.SERIAL_ERRORS)
        for exc in errors:
            dev = object.__new__(cls)
            dev._ser = DeadPort(exc)
            try:
                call(dev)
                ok, got = False, "keine Ausnahme"
            except err_cls:
                ok, got = True, ""
            except Exception as other:  # noqa: BLE001
                ok, got = False, f"{type(other).__name__}: {other}"
            check(f"{name}: {type(exc).__name__} -> {err_cls.__name__}", ok, got)


def check_worker() -> None:
    print("DeviceWorker-Zyklus")
    app = QCoreApplication.instance() or QCoreApplication([])
    import device_worker as dw

    messages: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    grab = Grab()
    dw.logger.addHandler(grab)
    try:
        worker = dw.DeviceWorker()
        removed: list[tuple] = []
        worker.device_removed.connect(lambda kind, did: removed.append((kind, did)))

        def boom():
            raise RuntimeError("unerwartet")

        worker._psus["psu:TOT"] = SimpleNamespace(get_display=boom, close=boom)
        worker._loads["load:TOT"] = SimpleNamespace(measure=boom, close=boom)
        frames = [SimpleNamespace(arbitration_id=0x120, data=bytes(8), extended=False, timestamp=0.0)]
        worker._can_buses["can:fake"] = SimpleNamespace(recv=lambda timeout=0.0: frames.pop() if frames else None)
        worker._can_stats["can:fake"] = [0, 0]
        worker._poll()
        app.processEvents()
        check("Netzteil mit unerwartetem Fehler gilt als getrennt",
              "psu:TOT" not in worker._psus and ("psu", "psu:TOT") in removed, str(removed))
        check("Last mit unerwartetem Fehler gilt als getrennt",
              "load:TOT" not in worker._loads and ("load", "load:TOT") in removed, str(removed))
        check("CAN wird im selben Zyklus trotzdem abgefragt", worker._can_stats["can:fake"][1] == 1,
              str(worker._can_stats["can:fake"]))
        check("steht im Log", any("psu:TOT" in m and "unerwartet" in m for m in messages)
              and any("load:TOT" in m for m in messages), str(messages))
    finally:
        dw.logger.removeHandler(grab)


def main() -> int:
    check_drivers()
    check_worker()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
