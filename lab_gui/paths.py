"""Gemeinsame Pfad-Hilfsfunktion.

Als PyInstaller-.exe liegt __file__ im ephemeren Temp-Extraktionsordner; dort
gespeicherte Dateien (Testablaeufe, Geraete-Labels) wuerden beim Beenden
verloren gehen. Daher im gefrorenen Fall neben der .exe speichern, sonst
neben dem Skript.
"""
from __future__ import annotations

import sys
from pathlib import Path

# True nur in der von PyInstaller gebauten .exe (Release), False im
# Dev-Betrieb (python lab_gui/main.py) -- von PyInstaller selbst gesetzt,
# siehe app_dir() unten. Zentrale Stelle fuer die Dev/Release-Unterscheidung,
# z.B. um den Simulationsmodus auf Release-Builds auszuschliessen
# (FEATURES.md Punkt 4).
IS_FROZEN = getattr(sys, "frozen", False)


def app_dir() -> Path:
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
