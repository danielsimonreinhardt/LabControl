"""Persistente App-Einstellungen (Simulationsmodus, Dark Mode, Sprache,
globale Sicherheits-Grenzwerte).

Analog zu device_registry.py lokal als JSON-Datei gespeichert, damit die
Einstellung Neustarts uebersteht.
"""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QObject, Signal

from i18n import DEFAULT_LANGUAGE
from paths import app_dir
from safety import default_safety_limits

SETTINGS_PATH = app_dir() / "settings.json"


class Settings(QObject):
    simulation_mode_changed = Signal(bool)
    dark_mode_changed = Signal(bool)
    dashboard_compact_changed = Signal(bool)
    language_changed = Signal(str)
    safety_limits_changed = Signal(dict)

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
    def dashboard_compact(self) -> bool:
        return bool(self._data.get("dashboard_compact", False))

    def set_dashboard_compact(self, enabled: bool) -> None:
        if enabled == self.dashboard_compact:
            return
        self._data["dashboard_compact"] = enabled
        self._save()
        self.dashboard_compact_changed.emit(enabled)

    @property
    def language(self) -> str:
        return str(self._data.get("language", DEFAULT_LANGUAGE))

    def set_language(self, language: str) -> None:
        if language == self.language:
            return
        self._data["language"] = language
        self._save()
        self.language_changed.emit(language)

    @property
    def safety_limits(self) -> dict:
        """Globale Sicherheits-Grenzwerte je Geraeteart (siehe safety.py).

        Deep-Merge des gespeicherten Stands ueber die Defaults, damit
        fehlende/kaputte Eintraege (aeltere settings.json, von Hand editiert)
        auf einen gueltigen Default zurueckfallen statt einen KeyError beim
        Zugriff ueber safety.SAFETY_LIMIT_FIELDS auszuloesen.
        """
        merged = default_safety_limits()
        stored = self._data.get("safety_limits")
        if isinstance(stored, dict):
            for kind, fields in stored.items():
                if kind not in merged or not isinstance(fields, dict):
                    continue
                for field, entry in fields.items():
                    if field not in merged[kind] or not isinstance(entry, dict):
                        continue
                    if "enabled" in entry:
                        merged[kind][field]["enabled"] = bool(entry["enabled"])
                    if "value" in entry:
                        try:
                            merged[kind][field]["value"] = float(entry["value"])
                        except (TypeError, ValueError):
                            pass
        return merged

    def set_safety_limit(self, kind: str, field: str, enabled: bool, value: float) -> None:
        current = self.safety_limits
        if kind not in current or field not in current[kind]:
            return
        if current[kind][field]["enabled"] == enabled and current[kind][field]["value"] == value:
            return
        current[kind][field] = {"enabled": enabled, "value": value}
        self._data["safety_limits"] = current
        self._save()
        self.safety_limits_changed.emit(copy.deepcopy(current))
