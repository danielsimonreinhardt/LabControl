"""Persistente App-Einstellungen (aktuell: Simulationsmodus fuer Debugging ohne Hardware).

Analog zu device_registry.py lokal als JSON-Datei gespeichert, damit die
Einstellung Neustarts uebersteht.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal

from i18n import DEFAULT_LANGUAGE
from paths import app_dir

SETTINGS_PATH = app_dir() / "settings.json"


class Settings(QObject):
    simulation_mode_changed = Signal(bool)
    dark_mode_changed = Signal(bool)
    language_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._data = self._load()

    @staticmethod
    def _load() -> dict:
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            SETTINGS_PATH.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except OSError:
            pass  # Einstellung bleibt fuer die laufende Session gueltig, nur Persistenz betroffen

    @property
    def simulation_mode(self) -> bool:
        return bool(self._data.get("simulation_mode", False))

    def set_simulation_mode(self, enabled: bool) -> None:
        if enabled == self.simulation_mode:
            return
        self._data["simulation_mode"] = enabled
        self._save()
        self.simulation_mode_changed.emit(enabled)

    @property
    def dark_mode(self) -> bool:
        return bool(self._data.get("dark_mode", False))

    def set_dark_mode(self, enabled: bool) -> None:
        if enabled == self.dark_mode:
            return
        self._data["dark_mode"] = enabled
        self._save()
        self.dark_mode_changed.emit(enabled)

    @property
    def language(self) -> str:
        return str(self._data.get("language", DEFAULT_LANGUAGE))

    def set_language(self, language: str) -> None:
        if language == self.language:
            return
        self._data["language"] = language
        self._save()
        self.language_changed.emit(language)
