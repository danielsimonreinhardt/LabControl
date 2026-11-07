"""Prueft, dass jede Schreibaktion am Batterie-Simulator im Log steht -- mit Quelle
(Kachel im Fenster, Testablauf; Fernsteuerung loggt execute_remote_action selbst).
Hintergrund (07.11.2026): Die Bedienung der Kachel stand nicht im Log, ein gesendeter
Zustand liess sich keiner Quelle zuordnen. DeviceWorker im Simulationsmodus, ohne Hardware.

Aufruf:  python tools/check_battsim_log.py   (aus dem Repo-Stamm)
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

app = QCoreApplication.instance() or QCoreApplication([])
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
for name in ("_reconnect_loads", "_reconnect_psus", "_reconnect_hils", "_reconnect_fgs",
             "_reconnect_picoscope", "_probe_scpiscopes"):
    setattr(worker, name, lambda: None)
worker.start()
spin(1.0)
sid = dw.SIM_BATTSIM_ID


def lines_with(text: str) -> list[str]:
    return [line for line in handler.lines if text in line]


# Kachel (Slots des Fensters)
worker.set_battsim_remote(sid, True)
worker.set_battsim_all_voltages(sid, 1.0)
worker.set_battsim_all_enabled(sid, True)
worker.set_battsim_output(sid, True)
worker.set_battsim_cell_enabled(sid, 3, False)
worker.set_battsim_cell_voltage(sid, 2, 3.3)
spin(0.3)
tile = lines_with("per Kachel")
check(len(tile) == 6, f"6 Zeilen 'per Kachel' ({len(tile)})")
check(any("BS_REMOTE_ON" in t and t.endswith("-> ok") for t in tile), "BS_REMOTE_ON per Kachel -> ok")
check(any("BS_ALL_VOLT (wert=1.0, kanal=0)" in t for t in tile), "BS_ALL_VOLT mit Wert")
check(any("BS_CELL_OFF (wert=0.0, kanal=3)" in t for t in tile), "BS_CELL_OFF mit Kanal 3")
check(any("BS_CELL_VOLT (wert=3.3, kanal=2)" in t for t in tile), "BS_CELL_VOLT mit Wert und Kanal")
check(all(sid in t for t in tile), "Geraete-ID in jeder Zeile")

# abgelehnter Wert: FEHLER mit Grund
handler.lines.clear()
worker.set_battsim_cell_voltage(sid, 4, 4.9)
spin(0.2)
rej = lines_with("per Kachel")
check(len(rej) == 1 and "FEHLER" in rej[0] and "[" in rej[0], f"abgelehnter Wert -> FEHLER mit Grund ({rej})")

# Testablauf
handler.lines.clear()
worker.execute_action(sid, "battsim", "BS_OUT_OFF", 0.0, 0)
worker.execute_action(sid, "battsim", "BS_CELL_READ", 0.0, 1)
spin(0.2)
run = lines_with("per Testablauf")
check(len(run) == 1 and "BS_OUT_OFF" in run[0] and run[0].endswith("-> ok"), f"Testablauf schreibt Zeile ({run})")
check(not any("BS_CELL_READ" in line for line in handler.lines), "Lese-Aktion wird nicht protokolliert")

# Fernsteuerung: genau eine Zeile (die bestehende), keine doppelte
handler.lines.clear()
worker.execute_remote_action(1, sid, "battsim", "BS_ALL_OFF", 0.0, 0)
spin(0.2)
check(len(lines_with("BS_ALL_OFF")) == 1 and lines_with("Fernsteuerung:"), "Fernsteuerung: eine Zeile, nicht doppelt")

# nicht verbunden
handler.lines.clear()
worker.set_battsim_remote("battsim:gibt-es-nicht", True)
spin(0.1)
check(any("FEHLER" in line and "nicht verbunden" in line for line in lines_with("per Kachel")),
      "unbekannter Simulator -> FEHLER nicht verbunden")

worker.shutdown_can_nodes()
worker.set_simulation_mode(False)
spin(0.3)

print()
if FAILURES:
    print(f"{len(FAILURES)} Pruefung(en) FEHLGESCHLAGEN: {', '.join(FAILURES)}")
    sys.exit(1)
print("Alle Pruefungen bestanden.")
