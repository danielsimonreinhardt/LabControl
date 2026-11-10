"""Gemeinsame Pfad-Hilfsfunktion.

Als PyInstaller-.exe liegt __file__ im ephemeren Temp-Extraktionsordner; dort
gespeicherte Dateien (Testablaeufe, Geraete-Labels) wuerden beim Beenden
verloren gehen. Daher im gefrorenen Fall neben der .exe speichern, sonst
neben dem Skript.

Linux (Raspberry Pi, 10.11.2026): Dort laeuft LabControl aus dem Repo
(Entscheidung PI-9). Die Daten liegen dann nicht im Repo, sondern unter
$XDG_DATA_HOME/labcontrol (Standard ~/.local/share/labcontrol), damit ein
git pull sie nie beruehrt. Die Umgebungsvariable LABCONTROL_DATA_DIR legt den
Ordner auf jeder Plattform fest (z.B. auf eine USB-SSD, um die SD-Karte zu
schonen).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# True nur in der von PyInstaller gebauten .exe (Release), False im
# Dev-Betrieb (python lab_gui/main.py) -- von PyInstaller selbst gesetzt,
# siehe app_dir() unten. Zentrale Stelle fuer die Dev/Release-Unterscheidung,
# z.B. um den Simulationsmodus auf Release-Builds auszuschliessen
# (FEATURES.md Punkt 4).
IS_FROZEN = getattr(sys, "frozen", False)


DATA_DIR_ENV = "LABCONTROL_DATA_DIR"


def app_dir() -> Path:
    """Ordner fuer alle Daten der App (Einstellungen, Log, Testablaeufe, Aufnahmen)."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        path = Path(override).expanduser()
    elif IS_FROZEN:
        return Path(sys.executable).resolve().parent
    elif sys.platform == "win32":
        return Path(__file__).resolve().parent
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        path = Path(xdg) / "labcontrol"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Laeuft beim Import (Logging gibt es noch nicht): nicht abbrechen, neben dem Skript weiter.
        fallback = Path(__file__).resolve().parent
        print(f"LabControl: Datenordner {path} nicht anlegbar ({exc}), nutze {fallback}", file=sys.stderr)
        return fallback
    return path
