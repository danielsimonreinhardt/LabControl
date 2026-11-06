"""Prueft die Handbedienung des Netzteils (seit 0.26.0) in Worker und Kachel.
Simuliertes Netzteil (MockHCS34xx), keine Hardware.

Aufruf:  python tools/check_psu_manual.py   (aus dem Repo-Stamm)
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


dw.KoradKEL102.discover_ports = staticmethod(lambda: [])
dw.HCS34xx.discover_ports = staticmethod(lambda: [])
dw.MicroHIL.discover = staticmethod(lambda: None)
dw.JDS66xx.discover_ports = staticmethod(lambda: [])
dw.picoscope_usb_present = lambda: False

SIM = dw.SIM_PSU_ID
worker = dw.DeviceWorker(simulation_mode=True)
measurements: list[float] = []
outputs: list[tuple[str, bool]] = []
setpoints: list[tuple[str, float, float]] = []
manual_states: list[tuple[str, bool]] = []
worker.psu_measurement.connect(lambda d, v, i, cc: measurements.append(time.monotonic()))
worker.psu_output_state.connect(lambda d, on: outputs.append((d, on)))
worker.psu_setpoints.connect(lambda d, v, i: setpoints.append((d, v, i)))
worker.psu_manual_state.connect(lambda d, m: manual_states.append((d, m)))
worker.start()
spin(1.0)
mock = worker._psus[SIM]

print("Kontrolle durch LabControl (Normalzustand)")
worker.set_psu_voltage(SIM, 12.0)
worker.set_psu_current(SIM, 3.0)
worker.set_psu_output(SIM, False)
measurements.clear()
spin(1.5)
check("Netzteil wird abgefragt", len(measurements) >= 4, str(len(measurements)))

print("Auf Handbedienung umschalten")
outputs.clear()
manual_states.clear()
worker.set_psu_manual(SIM, True)
check("Zustand 'Handbedienung' wird gemeldet", manual_states == [(SIM, True)], str(manual_states))
check("Strom 0 A", mock.get_setpoint()[1] == 0.0, str(mock.get_setpoint()))
check("Spannung auf dem Minimum (0 V nimmt das Geraet nicht an)", mock.get_setpoint()[0] == dw.PSU_MIN_VOLTAGE,
      str(mock.get_setpoint()))
check("Ausgang EIN (das Geraet hat keine Ausgangstaste)", mock.get_output() is True)
check("... und die Kachel erfaehrt es", (SIM, True) in outputs, str(outputs))
measurements.clear()
spin(1.5)
check("Keine Abfrage mehr in der Handbedienung", measurements == [], str(len(measurements)))

print("Befehle werden in der Handbedienung abgelehnt")
worker.set_psu_voltage(SIM, 20.0)
check("Spannung setzen wirkungslos", mock.get_setpoint()[0] == dw.PSU_MIN_VOLTAGE, str(mock.get_setpoint()))
worker.set_psu_output(SIM, False)
check("Ausgang schalten wirkungslos", mock.get_output() is True)
ok, message, _ = worker._dispatch_action(SIM, "psu", "PSU_VOLT", 5.0, 0)
check("Fernsteuerung/Testablauf bekommt eine klare Absage", not ok and "Handbedienung" in message, f"{ok} {message}")
ok, message, _ = worker._dispatch_action(SIM, "psu", "PSU_OUT_OFF", 0.0, 0)
check("... auch beim Ausgang", not ok and "Handbedienung" in message, f"{ok} {message}")

print("Alle aus / Beenden fassen ein Netzteil in Handbedienung nicht an")
mock.set_voltage(7.0)   # Wert 'von Hand am Geraet' eingestellt
mock.set_current(1.0)
worker.all_outputs_off("Test")
check("Alle aus laesst das Geraet in Handbedienung unberuehrt",
      mock.get_output() is True and mock.get_setpoint() == (7.0, 1.0), f"{mock.get_output()} {mock.get_setpoint()}")
worker.all_outputs_off(dw.REASON_WINDOW_CLOSE)
check("Beenden laesst es ebenfalls unberuehrt", mock.get_output() is True and mock.get_setpoint() == (7.0, 1.0))

print("Zurueck zur Kontrolle durch LabControl")
setpoints.clear()
outputs.clear()
manual_states.clear()
worker.set_psu_manual(SIM, False)
check("Zustand 'Kontrolle' wird gemeldet", manual_states == [(SIM, False)], str(manual_states))
check("Sollwerte des Geraets werden uebernommen (nichts gesendet)", setpoints == [(SIM, 7.0, 1.0)], str(setpoints))
check("Geraet bleibt unveraendert", mock.get_output() is True and mock.get_setpoint() == (7.0, 1.0))
check("Ausgangszustand wird uebernommen", (SIM, True) in outputs, str(outputs))
measurements.clear()
spin(1.5)
check("Abfrage laeuft wieder", len(measurements) >= 4, str(len(measurements)))
worker.set_psu_voltage(SIM, 9.0)
check("Befehle gehen wieder", mock.get_setpoint()[0] == 9.0)

print("Beenden der App (window close): Netzteil wird freigegeben statt ausgeschaltet")
worker.set_psu_output(SIM, False)
worker.set_psu_voltage(SIM, 12.0)
worker.set_psu_current(SIM, 3.0)
outputs.clear()
worker.all_outputs_off(dw.REASON_WINDOW_CLOSE)
check("Spannung Minimum, Strom 0 A", mock.get_setpoint() == (dw.PSU_MIN_VOLTAGE, 0.0), str(mock.get_setpoint()))
check("Ausgang EIN", mock.get_output() is True)
check("... und gemeldet", outputs == [(SIM, True)], str(outputs))

print("Normales 'Alle aus' schaltet weiterhin aus")
worker.set_psu_output(SIM, True)
worker.all_outputs_off("Test")
check("Ausgang AUS", mock.get_output() is False)

print("Freigabe scheitert -> bleibt unter Kontrolle von LabControl")
manual_states.clear()
original = mock.set_output


def refuse(on):
    raise PowerSupplyValueError("nicht uebernommen")


mock.set_output = refuse
worker.set_psu_manual(SIM, True)
mock.set_output = original
check("Meldet 'keine Handbedienung'", manual_states == [(SIM, False)], str(manual_states))
check("Verbindung besteht weiter und wird abgefragt", SIM in worker._psus and SIM not in worker._psu_manual)

print("Kachel")
group = PsuControlGroup(SIM, "Netzteil")
signals: list[tuple] = []
group.set_manual.connect(lambda d, m: signals.append(("manual", m)))
group.set_voltage.connect(lambda d, v: signals.append(("U", v)))
widgets = (
    group._voltage_spin, group._voltage_button, group._current_spin, group._current_button,
    group._ovp_spin, group._ovp_button, group._ocp_spin, group._ocp_button,
    group._output_on_button, group._output_off_button,
)
check("Umschalter ist nicht eingeschaltet", not group._manual_button.isChecked())
check("Hinweis ist ausgeblendet", group._manual_hint.isHidden())
group._manual_button.click()
check("Klick meldet die Handbedienung", signals == [("manual", True)], str(signals))
group.set_manual_state(True)
check("In der Handbedienung sind alle Felder gesperrt", not any(w.isEnabled() for w in widgets))
check("Hinweis ist sichtbar", not group._manual_hint.isHidden())
check("Umschalter bleibt bedienbar", group._manual_button.isEnabled() and group._manual_button.isChecked())
signals.clear()
group.set_manual_state(False)
check("Rueckwechsel sperrt nichts mehr", all(w.isEnabled() for w in widgets))
check("... ohne ein Signal zu senden", signals == [], str(signals))
group.set_setpoints(7.5, 1.2)
check("Sollwerte vom Geraet landen in den Feldern, ohne etwas zu senden",
      group._voltage_spin.value() == 7.5 and group._current_spin.value() == 1.2 and signals == [], str(signals))
signals.clear()
group._manual_button.click()
group.set_manual_state(False)   # Worker lehnt ab
check("Abgelehnte Freigabe stellt den Umschalter zurueck", not group._manual_button.isChecked())

print()
if FAILURES:
    print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
    sys.exit(1)
print("Alles ok.")
