"""Prueft die Sollwert-/Grenzwertfelder der Batterie-Simulator-Kachel (BattSimControlGroup):
eine Eingabe darf nicht vom gemeldeten Zustand ueberschrieben werden, bevor sie gesendet ist
(Fehlerbild: Wert sprang auf den alten Treiberwert, z.B. 4,2 V, zurueck). Offscreen, ohne Hardware.

Aufruf:  python tools/check_battsim_tile.py   (aus dem Repo-Stamm)
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QLocale.setDefault(QLocale(QLocale.Language.C))   # Dezimalpunkt beim Tippen
app = QApplication.instance() or QApplication([])
import control_tab as ct  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def state(setpoint: float, limit: float = 4.3) -> dict:
    return {"remote": True, "output_on": False, "failed": False, "voltage_limit": limit, "status_name": "Fernsteuerung",
            "cells": [{"index": i + 1, "setpoint": setpoint, "enable": False, "voltage": 0.0, "enabled": False,
                       "error": False} for i in range(14)]}


def type_into(spin, text: str) -> None:
    spin.setFocus()
    app.processEvents()
    spin.selectAll()
    QTest.keyClicks(spin, text)


def make():
    group = ct.BattSimControlGroup("battsim:x", "BS")
    group.show()
    sent: list[tuple[int, float]] = []
    limits: list[float] = []
    group.set_cell_voltage.connect(lambda _d, n, v: sent.append((n, v)))
    group.voltage_limit_changed.connect(lambda _d, v: limits.append(v))
    cell_buttons = [b for b in group.findChildren(ct.IconButton) if b.parent() is group._grid_host]
    return group, sent, limits, cell_buttons


print("Zellen-Sollwert: Druecken auf den Haken nimmt dem Feld den Fokus, dazwischen kommt ein Zustandsupdate")
group, sent, limits, buttons = make()
group.set_state(state(4.2))
spin = group._cell_spins[0]
check("Feld zeigt den gemeldeten Sollwert", spin.value() == 4.2, str(spin.value()))
type_into(spin, "3.3")
check("Eingabe steht im Feld", abs(spin.value() - 3.3) < 1e-9, str(spin.value()))
buttons[0].setFocus()          # Maus-Druck: Fokus weg, clicked folgt erst beim Loslassen
app.processEvents()
group.set_state(state(4.2))    # Zustandsupdate dazwischen
check("Feld wird nicht auf 4,2 V zurueckgesetzt", abs(spin.value() - 3.3) < 1e-9, str(spin.value()))
buttons[0].click()
check("gesendet wird die Eingabe (3,3 V), nicht der alte Wert", sent == [(1, spin.value())] and abs(sent[0][1] - 3.3) < 1e-9, str(sent))

print("Danach: Bestaetigung durch den Zustand, spaetere Aenderung von aussen wird wieder uebernommen")
group.set_state(state(4.2))    # Befehl noch nicht verarbeitet: Eingabe bleibt stehen
check("vor der Bestaetigung bleibt die Eingabe", abs(spin.value() - 3.3) < 1e-9, str(spin.value()))
group.set_state(state(3.3))    # Geraet/Treiber hat uebernommen
check("Zustand bestaetigt (3,3 V)", abs(spin.value() - 3.3) < 1e-9)
group.set_state(state(2.5))    # z.B. ueber MCP geaendert
check("Aenderung von aussen wird uebernommen", abs(spin.value() - 2.5) < 1e-9, str(spin.value()))

print("Unveraenderte Felder folgen dem Zustand weiter")
group, sent, limits, buttons = make()
group.set_state(state(4.2))
group.set_state(state(3.9))
check("alle Felder folgen", all(abs(s.value() - 3.9) < 1e-9 for s in group._cell_spins))

print("Pfeil-Schritte (Knopf am Feld) zaehlen als Eingabe")
group, sent, limits, buttons = make()
group.set_state(state(4.2))
group._cell_spins[1].setValue(4.0)
group.set_state(state(4.2))
check("Pfeil-Wert wird nicht ueberschrieben", abs(group._cell_spins[1].value() - 4.0) < 1e-9, str(group._cell_spins[1].value()))
check("andere Felder unberuehrt", abs(group._cell_spins[2].value() - 4.2) < 1e-9)

print("Eingabe verfaellt nach BATTSIM_EDIT_HOLD_S ohne Senden")
group, sent, limits, buttons = make()
group.set_state(state(4.2))
group._cell_spins[3].setValue(3.1)
real_monotonic = ct.time.monotonic
ct.time.monotonic = lambda: real_monotonic() + ct.BATTSIM_EDIT_HOLD_S + 1
try:
    group.set_state(state(4.2))
finally:
    ct.time.monotonic = real_monotonic
check("Feld zeigt wieder den Geraetezustand", abs(group._cell_spins[3].value() - 4.2) < 1e-9, str(group._cell_spins[3].value()))

print("Grenzwert: gleiches Verhalten")
group, sent, limits, buttons = make()
group.set_state(state(1.0, limit=4.3))
type_into(group._limit_spin, "3.8")
group._limit_button.setFocus()
app.processEvents()
group.set_state(state(1.0, limit=4.3))
check("Grenzwert-Eingabe bleibt", abs(group._limit_spin.value() - 3.8) < 1e-9, str(group._limit_spin.value()))
group._limit_button.click()
check("gesendet wird 3,8 V", limits == [group._limit_spin.value()] and abs(limits[0] - 3.8) < 1e-9, str(limits))
group.set_state(state(1.0, limit=4.3))   # alter Wert, bevor der Worker ihn uebernommen hat
check("Grenzwert springt nicht zurueck", abs(group._limit_spin.value() - 3.8) < 1e-9, str(group._limit_spin.value()))
group.set_state(state(1.0, limit=3.8))
check("bestaetigt: Bereich der Zellfelder folgt (max 3,8 V)", abs(group._cell_spins[0].maximum() - 3.8) < 1e-9,
      str(group._cell_spins[0].maximum()))

print("Enter sendet wie der Haken")
from PySide6.QtCore import Qt  # noqa: E402

group, sent, limits, buttons = make()
group.set_state(state(4.2))
spin = group._cell_spins[4]
type_into(spin, "3.3")
QTest.keyClick(spin, Qt.Key.Key_Return)
check("Enter im Zellfeld sendet die Eingabe", sent == [(5, spin.value())] and abs(sent[0][1] - 3.3) < 1e-9, str(sent))
group.set_state(state(4.2))
check("Eingabe bleibt nach Enter stehen (bis bestaetigt)", abs(spin.value() - 3.3) < 1e-9, str(spin.value()))

type_into(group._cell_spins[6], "2.9")
QTest.keyClick(group._cell_spins[6].lineEdit(), Qt.Key.Key_Enter)   # Ziffernblock-Enter, ans Textfeld
check("Enter (Ziffernblock) im Textfeld sendet", sent[-1][0] == 7 and abs(sent[-1][1] - 2.9) < 1e-9, str(sent))

count = len(sent)
type_into(group._cell_spins[8], "3.1")
buttons[0].setFocus()          # Wegklicken ohne Enter: nichts senden
app.processEvents()
check("Fokuswechsel ohne Enter sendet nichts", len(sent) == count, str(sent))

all_sent: list[float] = []
group.set_all_voltages.connect(lambda _d, v: all_sent.append(v))
type_into(group._all_spin, "3.6")
QTest.keyClick(group._all_spin, Qt.Key.Key_Return)
check("Enter im Feld 'Alle Zellen' sendet", len(all_sent) == 1 and abs(all_sent[0] - 3.6) < 1e-9, str(all_sent))

type_into(group._limit_spin, "3.7")
QTest.keyClick(group._limit_spin, Qt.Key.Key_Return)
check("Enter im Grenzwert-Feld sendet", len(limits) == 1 and abs(limits[0] - 3.7) < 1e-9, str(limits))

print()
if FAILURES:
    print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
    sys.exit(1)
print("Alles ok.")
