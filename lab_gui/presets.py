"""Software-Presets fuer den Control-Tab: benannte Sollwert-Kombinationen,
geraetetyp-uebergreifend (Last/Netzteil) statt an ein einzelnes physisches
Geraet gebunden.

Ersetzt die frueheren geraeteseitigen HCS-34xx-Presets (P1/P2/P3, siehe
hcs34xx/driver.py: get_memory/set_memory/recall_memory) durch rein
software-basierte, in einer JSON-Datei abgelegte Presets -- analog zum
etablierten Muster aus device_registry.py/settings.py.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal

from paths import app_dir

PRESETS_PATH = app_dir() / "presets.json"


class PresetStore(QObject):
    # kind ("load"/"psu") -- Presetliste dieser Geraeteart hat sich geaendert.
    presets_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._data = self._load()

    @staticmethod
    def _load() -> dict:
        try:
            return json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            PRESETS_PATH.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # Preset bleibt fuer die laufende Session gueltig, nur Persistenz betroffen

    def presets_for(self, kind: str) -> list[dict]:
        """Presets einer Geraeteart, sortiert nach Name."""
        stored = self._data.get(kind)
        presets = stored if isinstance(stored, list) else []
        return sorted(presets, key=lambda p: p.get("name", ""))

    def save_preset(self, kind: str, name: str, fields: dict) -> None:
        """Legt ein Preset an oder ueberschreibt ein gleichnamiges bestehendes."""
        name = name.strip()
        if not name:
            return
        presets = [p for p in self.presets_for(kind) if p.get("name") != name]
        presets.append({"name": name, **fields})
        self._data[kind] = presets
        self._save()
        self.presets_changed.emit(kind)

    def delete_preset(self, kind: str, name: str) -> None:
        presets = [p for p in self.presets_for(kind) if p.get("name") != name]
        self._data[kind] = presets
        self._save()
        self.presets_changed.emit(kind)
