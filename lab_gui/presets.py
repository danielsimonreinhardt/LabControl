"""Software-Presets im Control-Tab: 5 feste, geraeteuebergreifende Preset-
Plaetze (siehe PresetBar in control_tab.py). Jeder Platz speichert je
verbundenem Geraet (device_id) Sollwerte UND Schaltstatus (Last-Eingang bzw.
PSU-Ausgang) gemeinsam -- ein Klick auf einen Preset-Platz stellt damit den
kompletten Messaufbau (mehrere Geraete gleichzeitig) auf einen zuvor
gespeicherten Zustand zurueck, inklusive Ein/Aus.

Ersetzt sowohl die fruehere geraeteseitige HCS-34xx-Preset-Funktion
(P1/P2/P3, siehe hcs34xx/driver.py: get_memory/set_memory/recall_memory) als
auch die erste Software-Preset-Iteration (benannte, frei anlegbare Presets
je Geraeteart, separat in jedem Geraete-Panel) -- beides durch 5 feste,
geraeteuebergreifende Preset-Plaetze in einer gemeinsamen Leiste oben im
Control-Tab ersetzt.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal

from paths import app_dir

PRESETS_PATH = app_dir() / "presets.json"
SLOT_COUNT = 5


class PresetStore(QObject):
    # slot index (0..SLOT_COUNT-1) -- Name oder Geraetedaten dieses Platzes
    # haben sich geaendert (Speichern/Umbenennen).
    preset_changed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._slots = self._load()

    @staticmethod
    def _load() -> list[dict]:
        try:
            raw = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
            stored = raw.get("slots", []) if isinstance(raw, dict) else []
        except (OSError, ValueError):
            stored = []
        slots = []
        for index in range(SLOT_COUNT):
            slot = stored[index] if index < len(stored) and isinstance(stored[index], dict) else {}
            devices = slot.get("devices")
            slots.append({
                "name": slot.get("name") or f"Preset {index + 1}",
                "devices": devices if isinstance(devices, dict) else {},
            })
        return slots

    def _save(self) -> None:
        try:
            PRESETS_PATH.write_text(
                json.dumps({"slots": self._slots}, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # Preset bleibt fuer die laufende Session gueltig, nur Persistenz betroffen

    def name(self, slot: int) -> str:
        return self._slots[slot]["name"]

    def devices(self, slot: int) -> dict:
        """Je Geraet (device_id) gespeicherter Zustand dieses Presets."""
        return dict(self._slots[slot]["devices"])

    def save(self, slot: int, devices: dict) -> None:
        self._slots[slot]["devices"] = devices
        self._save()
        self.preset_changed.emit(slot)

    def rename(self, slot: int, name: str) -> None:
        name = name.strip()
        if not name:
            return
        self._slots[slot]["name"] = name
        self._save()
        self.preset_changed.emit(slot)
