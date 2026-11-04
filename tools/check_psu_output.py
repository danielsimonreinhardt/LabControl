"""Prueft den echten Ausgang-Schalter des Netzteils (SOUT/GOUT, seit 0.25.0) in Worker und Kachel.
Simuliertes Netzteil (MockHCS34xx), keine Hardware.

Aufruf:  python tools/check_psu_output.py   (aus dem Repo-Stamm)
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
import device_worker as dw  # noqa: E402
from control_tab import PsuControlGroup  # noqa: E402
from hcs34xx.driver import PowerSupplyValueError  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def spin(seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else False


# Keine echte Hardware anfassen
dw.KoradKEL102.discover_ports = staticmethod(lambda: [])
dw.HCS34xx.discover_ports = staticmethod(lambda: [])
dw.MicroHIL.discover = staticmethod(lambda: None)
dw.JDS66xx.discover_ports = staticmethod(lambda: [])
dw.picoscope_usb_present = lambda: False

SIM = dw.SIM_PSU_ID
worker = dw.DeviceWorker(simulation_mode=True)
states: list[tuple[str, bool]] = []
measurements: list[float] = []
worker.psu_output_state.connect(lambda d, on: states.append((d, on)))
worker.psu_measurement.connect(lambda d, v, i, cc: measurements.append(time.monotonic()))
worker.start()
spin(1.0)
mock = worker._psus[SIM]

print("Worker: Ausgang schalten")
check("Mock startet mit Ausgang aus", mock.get_output() is False)
states.clear()
worker.set_psu_output(SIM, True)
check("set_psu_output(True) schaltet ein", mock.get_output() is True)
check("... und meldet EIN an die Kachel", states == [(SIM, True)], str(states))
worker.set_psu_current(SIM, 0.0)
check("Strom 0 A schaltet den Ausgang NICHT aus (nur Strombegrenzung)", mock.get_output() is True)
worker.set_psu_current(SIM, 2.0)
check("Strom > 0 A aendert den Ausgang nicht", mock.get_output() is True)
states.clear()
worker.set_psu_output(SIM, False)
check("set_psu_output(False) schaltet aus", mock.get_output() is False and states == [(SIM, False)], str(states))
check("Sollwerte bleiben beim Schalten stehen", mock.get_setpoint()[1] == 2.0, str(mock.get_setpoint()))

print("Worker: Aktionen aus Fernsteuerung/Testablauf")
states.clear()
ok, msg, _ = worker._dispatch_action(SIM, "psu", "PSU_OUT_ON", 0.0, 0)
check("PSU_OUT_ON schaltet ein und meldet EIN", ok and mock.get_output() is True and states == [(SIM, True)], f"{ok} {msg} {states}")
ok, msg, _ = worker._dispatch_action(SIM, "psu", "PSU_CURR", 0.0, 0)
check("PSU_CURR 0 schaltet nicht aus", ok and mock.get_output() is True)
states.clear()
ok, msg, _ = worker._dispatch_action(SIM, "psu", "PSU_OUT_OFF", 0.0, 0)
check("PSU_OUT_OFF schaltet aus und meldet AUS", ok and mock.get_output() is False and states == [(SIM, False)], f"{ok} {msg} {states}")

print("Worker: Alle aus")
worker.set_psu_output(SIM, True)
states.clear()
worker.all_outputs_off("Test")
check("Alle aus schaltet den Ausgang aus", mock.get_output() is False)
check("... und meldet AUS", (SIM, False) in states, str(states))

print("Worker: Schalten am Geraet von Hand wird erkannt (GOUT-Abfrage)")
states.clear()
mock.set_output(True)   # Taste am Geraet
limit = dw.PSU_OUTPUT_POLL_EVERY * dw.PSU_POLL_INTERVAL_S + 1.5
check("Kachel folgt dem von Hand eingeschalteten Ausgang", spin(limit, lambda: (SIM, True) in states), str(states))
states.clear()
spin(1.5)
check("... meldet nicht dauernd neu", states == [], str(states))
mock.set_output(False)
check("Kachel folgt dem von Hand ausgeschalteten Ausgang", spin(limit, lambda: (SIM, False) in states))

print("Worker: Schalter wirkt nicht -> wirklicher Zustand wird nachgemeldet")
mock.set_output(True)
spin(limit, lambda: (SIM, True) in states)


def refuse(on):
    raise PowerSupplyValueError("Ausgang AUS nicht uebernommen")


original = mock.set_output
mock.set_output = refuse
states.clear()
worker.set_psu_output(SIM, False)
check("Fehlschlag meldet den echten Zustand (EIN)", states == [(SIM, True)], str(states))
mock.set_output = original
check("Verbindung bleibt trotz Fehlschlag bestehen", SIM in worker._psus)
mock.set_output(False)

print("Worker: Abfragerate")
spin(1.0)
measurements.clear()
spin(3.0)
rate = len(measurements) / 3.0
check("Netzteil wird mit ~5 Hz abgefragt (nicht mit 10 Hz)", 3.5 <= rate <= 6.5, f"{rate:.1f} Hz")

print("Kachel")
group = PsuControlGroup(SIM, "Netzteil")
sent: list[tuple] = []
group.set_voltage.connect(lambda d, v: sent.append(("U", v)))
group.set_current.connect(lambda d, a: sent.append(("I", a)))
group.set_output.connect(lambda d, on: sent.append(("OUT", on)))
group._voltage_spin.setValue(5.0)
group._current_spin.setValue(1.5)
check("Spannung/Strom setzen sind auch bei AUS bedienbar (erst einstellen, dann einschalten)",
      group._voltage_button.isEnabled() and group._current_button.isEnabled())
group._output_on_button.click()
check("EIN: erst Spannung, dann Strom, zuletzt der Ausgang", sent == [("U", 5.0), ("I", 1.5), ("OUT", True)], str(sent))
check("EIN: Strom wird nicht auf 0,1 A verbogen", ("I", 1.5) in sent)
sent.clear()
group._output_off_button.click()
check("AUS: nur der Ausgang, kein Strom 0 A", sent == [("OUT", False)], str(sent))
check("Spannung/Strom setzen bleiben auch nach AUS bedienbar",
      group._voltage_button.isEnabled() and group._current_button.isEnabled())
sent.clear()
group._voltage_button.click()
check("Spannung setzen bei AUS sendet nur den Sollwert", sent == [("U", 5.0)], str(sent))

print()
if FAILURES:
    print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
    sys.exit(1)
print("Alles ok.")
