"""Schriftfamilien fuer selbst gezeichnete Grafiken (App-Symbol, Oszilloskop-Bild).

"Segoe UI" gibt es nur unter Windows. Unter Linux (Raspberry Pi, 10.11.2026)
nimmt Qt dann die naechste vorhandene Familie aus der Liste; DejaVu Sans ist
auf Raspberry Pi OS vorinstalliert.
"""
from __future__ import annotations

UI_FONT_FAMILIES = ["Segoe UI", "DejaVu Sans", "Liberation Sans", "Arial", "sans-serif"]
