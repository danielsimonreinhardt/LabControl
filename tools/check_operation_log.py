"""Prueft Entscheidung PI-15: jede Bedienung an Netzteil, Last, Funktionsgenerator und microHIL steht im Log --
Geraet, Aktion, Wert, Kanal, Herkunft (Kachel, Preset, Testablauf) und Ergebnis (ok/FEHLER).
Leseaktionen erzeugen keine Zeile. Vorher (10.11.2026) liess sich "Sollwert 12 V, Ausgang EIN/AUS" am Pi nicht
im Log nachvollziehen. DeviceWorker im Simulationsmodus, ohne Hardware.

Aufruf:  python tools/check_operation_log.py   (aus dem Repo-Stamm)
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

app = QCoreApplication.instance() or QCoreApplication([])
import no_hardware  # noqa: E402
no_hardware.block()   # echte Geraete nicht anfassen
import device_worker as dw  # noqa: E402

FAILURES: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


class Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def spin(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


handler = Collect()
dw.logger.addHandler(handler)
dw.logger.setLevel(logging.INFO)
worker = dw.DeviceWorker(simulation_mode=True)
worker.start()
spin(1.0)


def new_lines(action) -> list[str]:
    start = len(handler.lines)
    action()
    spin(0.1)
    return handler.lines[start:]


def one(lines: list[str], *parts: str) -> bool:
    return len(lines) == 1 and all(p in lines[0] for p in parts)


print("Kachel")
lines = new_lines(lambda: worker.set_psu_voltage(dw.SIM_PSU_ID, 12.0))
check(one(lines, "Netzteil psu:SIM: PSU_VOLT (wert=12.0, kanal=0) per Kachel -> ok"), "Netzteil Sollwert", str(lines))
lines = new_lines(lambda: worker.set_psu_output(dw.SIM_PSU_ID, True))
check(one(lines, "PSU_OUT_ON", "per Kachel -> ok"), "Netzteil Ausgang EIN", str(lines))
lines = new_lines(lambda: worker.set_psu_output(dw.SIM_PSU_ID, False))
check(one(lines, "PSU_OUT_OFF", "per Kachel -> ok"), "Netzteil Ausgang AUS", str(lines))
lines = new_lines(lambda: worker.set_load_current(dw.SIM_LOAD_ID, 0.5))
check(one(lines, "Last load:SIM: CURR (wert=0.5", "per Kachel -> ok"), "Last Sollwert", str(lines))
lines = new_lines(lambda: worker.set_load_input(dw.SIM_LOAD_ID, False))
check(one(lines, "Last load:SIM: OUT_OFF", "per Kachel"), "Last Eingang AUS", str(lines))
lines = new_lines(lambda: worker.set_fg_frequency(dw.SIM_FG_ID, 2, 1500.0))
check(one(lines, "Funktionsgenerator fg:SIM: FG_FREQ (wert=1500.0, kanal=2) per Kachel -> ok"), "Funktionsgenerator", str(lines))
lines = new_lines(lambda: worker.set_hil_relay(dw.SIM_HIL_ID, 3, True))
check(one(lines, "microHIL hil:SIM: HIL_RELAY_ON (wert=0.0, kanal=3) per Kachel -> ok"), "microHIL Relais", str(lines))
lines = new_lines(lambda: worker.set_psu_voltage("psu:GIBTESNICHT", 5.0))
check(one(lines, "psu:GIBTESNICHT", "PSU_VOLT", "-> FEHLER ["), "Fehler steht mit Grund im Log", str(lines))

print("Preset")
lines = new_lines(lambda: (worker.set_operation_source("Preset"), worker.set_psu_current(dw.SIM_PSU_ID, 1.0),
                           worker.set_operation_source("Kachel")))
check(one(lines, "PSU_CURR (wert=1.0", "per Preset -> ok"), "Herkunft Preset", str(lines))
lines = new_lines(lambda: worker.set_psu_current(dw.SIM_PSU_ID, 1.5))
check(one(lines, "per Kachel"), "danach wieder Kachel", str(lines))

print("Testablauf")
lines = new_lines(lambda: worker.execute_action(dw.SIM_PSU_ID, "psu", "PSU_VOLT", 3.3, 0))
check(one(lines, "Netzteil psu:SIM: PSU_VOLT (wert=3.3", "per Testablauf -> ok"), "Testablauf schreibt eine Zeile", str(lines))
lines = new_lines(lambda: worker.execute_action(dw.SIM_HIL_ID, "hil", "HIL_AIN_READ", 0.0, 1))
check(lines == [], "Leseaktion ohne Zeile", str(lines))

print("Preset aus der Steuerung (Reihenfolge ueber die Warteschlange)")
from PySide6.QtCore import QObject, QThread, Signal  # noqa: E402


class Sender(QObject):
    source = Signal(str)
    volt = Signal(str, float)


thread = QThread()
worker2 = dw.DeviceWorker(simulation_mode=True)
worker2.moveToThread(thread)
thread.started.connect(worker2.start)
thread.start()
spin(1.0)
sender = Sender()
sender.source.connect(worker2.set_operation_source)
sender.volt.connect(worker2.set_psu_voltage)
start = len(handler.lines)
sender.source.emit("Preset")
sender.volt.emit(dw.SIM_PSU_ID, 7.0)
sender.source.emit("Kachel")
sender.volt.emit(dw.SIM_PSU_ID, 8.0)
spin(0.8)
lines = [line for line in handler.lines[start:] if "PSU_VOLT" in line]
check(len(lines) == 2 and "wert=7.0" in lines[0] and "per Preset" in lines[0]
      and "wert=8.0" in lines[1] and "per Kachel" in lines[1], "Preset und Kachel ueber den Worker-Thread getrennt", str(lines))
from PySide6.QtCore import QMetaObject, Qt  # noqa: E402
QMetaObject.invokeMethod(worker2, "stop_timers", Qt.ConnectionType.BlockingQueuedConnection)
thread.quit()
thread.wait(2000)
worker.stop_timers()

print()
if FAILURES:
    print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
    sys.exit(1)
print("Alles ok. (nur Simulation, keine Hardware geprueft)")
