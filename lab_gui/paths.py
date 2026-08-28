"""Gemeinsame Pfad-Hilfsfunktion.

Als PyInstaller-.exe liegt __file__ im ephemeren Temp-Extraktionsordner; dort
gespeicherte Dateien (Testablaeufe, Geraete-Labels) wuerden beim Beenden
verloren gehen. Daher im gefrorenen Fall neben der .exe speichern, sonst
neben dem Skript.
"""
from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
