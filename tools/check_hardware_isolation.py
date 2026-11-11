"""Prueft, dass keine Pruefung echte Geraete anfasst.

Jede tools/check_*.py, die ein MainWindow oder einen DeviceWorker baut, muss die Geraetesuche
abschalten -- ueber tools/no_hardware.py (no_hardware.block()), die eigenen discover_ports-Ersetzungen
aelterer Pruefungen oder das Abschalten der _reconnect_*-Methoden am Worker. Hintergrund: der
Simulationsmodus sucht echte Geraete weiter; check_touch_ui hatte keine Sperre und stoerte am Pi die
laufende Last (11.11.2026), am PC haette es freie Geraete verbunden und beim Schliessen ALLE AUS bzw.
die Netzteil-Freigabe gesendet.

Rein statisch, keine Qt-Anwendung.  Aufruf:  python tools/check_hardware_isolation.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BUILDS = re.compile(r"\b(MainWindow|DeviceWorker)\(")
GUARDS = (
    "no_hardware.block()",                                   # gemeinsame Sperre
    "KoradKEL102.discover_ports = staticmethod(lambda: [])",  # aeltere Pruefungen (gleiche Ersetzungen)
    "KoradKEL102.discover_ports = classmethod(lambda cls: [])",  # check_reconnect_async (Treiber-Attrappen)
    '"_reconnect_loads", "_reconnect_psus"',                 # Worker-Methoden abgeschaltet (Batterie-Simulator)
)
# Bauen einen DeviceWorker, starten ihn aber nie (keine Suchrunde) -- ausdruecklich geprueft:
NEVER_STARTED = {"check_serial_unplug.py"}


def main() -> int:
    failures = []
    checked = 0
    for path in sorted(TOOLS.glob("check_*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if not BUILDS.search(text):
            continue
        checked += 1
        if path.name in NEVER_STARTED:
            ok = "worker.start()" not in text and ".start()" not in text.split("DeviceWorker(", 1)[1]
        else:
            ok = any(guard in text for guard in GUARDS)
        print(f"  {'OK  ' if ok else 'FEHL'} {path.name}")
        if not ok:
            failures.append(path.name)
    print()
    if failures:
        print(f"{len(failures)} PRUEFUNG(EN) OHNE HARDWARE-SPERRE: {failures} -- no_hardware.block() vor MainWindow/DeviceWorker")
        return 1
    print(f"Alles ok. ({checked} Pruefungen mit MainWindow/DeviceWorker, alle mit Sperre)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
