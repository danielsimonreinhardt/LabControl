"""Persistente App-Einstellungen (Simulationsmodus, Dark Mode, Sprache,
geraete-individuelle Sicherheits-Grenzwerte, Desktop-Benachrichtigungen,
geraete-individuelle Panel-Hintergrundfarben).

Analog zu device_registry.py lokal als JSON-Datei gespeichert, damit die
Einstellung Neustarts uebersteht.
"""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QObject, Signal

from i18n import DEFAULT_LANGUAGE
from paths import app_dir
from safety import SAFETY_LIMIT_FIELDS, default_device_limits, device_kind

SETTINGS_PATH = app_dir() / "settings.json"


class Settings(QObject):
    simulation_mode_changed = Signal(bool)
    dark_mode_changed = Signal(bool)
    dashboard_compact_changed = Signal(bool)
    language_changed = Signal(str)
    safety_limits_changed = Signal(dict)
    notifications_enabled_changed = Signal(bool)
    panel_colors_enabled_changed = Signal(bool)
    panel_color_changed = Signal(str, object)  # device_id, color_key (str | None)

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
    def notifications_enabled(self) -> bool:
        return bool(self._data.get("notifications_enabled", True))

    def set_notifications_enabled(self, enabled: bool) -> None:
        if enabled == self.notifications_enabled:
            return
        self._data["notifications_enabled"] = enabled
        self._save()
        self.notifications_enabled_changed.emit(enabled)

    @property
    def panel_colors_enabled(self) -> bool:
        return bool(self._data.get("panel_colors_enabled", False))

    def set_panel_colors_enabled(self, enabled: bool) -> None:
        if enabled == self.panel_colors_enabled:
            return
        self._data["panel_colors_enabled"] = enabled
        self._save()
        self.panel_colors_enabled_changed.emit(enabled)

    def panel_color(self, device_id: str) -> str | None:
        """Gespeicherte Panel-Farbe EINES Geraets (device_id), unabhaengig
        vom An/Aus-Schalter panel_colors_enabled -- siehe panel_color.py."""
        stored = self._data.get("panel_colors")
        value = stored.get(device_id) if isinstance(stored, dict) else None
        return value if isinstance(value, str) else None

    def set_panel_color(self, device_id: str, color_key: str | None) -> None:
        if color_key == self.panel_color(device_id):
            return
        stored = self._data.get("panel_colors")
        colors = dict(stored) if isinstance(stored, dict) else {}
        if color_key is None:
            colors.pop(device_id, None)
        else:
            colors[device_id] = color_key
        self._data["panel_colors"] = colors
        self._save()
        self.panel_color_changed.emit(device_id, color_key)

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
        """Rohe, geraete-individuelle Sicherheits-Grenzwerte: device_id ->
        {field: {"enabled": bool, "value": float}} (siehe safety.py).

        Anders als bei den anderen Settings-Properties kein Deep-Merge ueber
        Defaults -- welche device_ids ueberhaupt existieren, ist erst zur
        Laufzeit bekannt (siehe device_safety_limits() fuer den Zugriff auf
        EIN konkretes, ggf. noch unbekanntes Geraet mit Default-Fallback).
        Wird unveraendert an SafetyMonitor durchgereicht.
        """
        stored = self._data.get("safety_limits")
        return copy.deepcopy(stored) if isinstance(stored, dict) else {}

    def device_safety_limits(self, device_id: str, kind: str) -> dict:
        """Grenzwerte fuer EIN Geraet, ueber die Kind-Defaults gemergt.

        Deep-Merge des gespeicherten Stands ueber die Defaults, damit
        fehlende/kaputte Eintraege (aeltere settings.json, von Hand editiert,
        oder ein Geraet ohne bisherige eigene Konfiguration) auf einen
        gueltigen Default zurueckfallen statt einen KeyError auszuloesen.
        """
        merged = default_device_limits(kind)
        stored = self._data.get("safety_limits", {})
        entry_map = stored.get(device_id) if isinstance(stored, dict) else None
        if isinstance(entry_map, dict):
            for field, entry in entry_map.items():
                if field not in merged or not isinstance(entry, dict):
                    continue
                if "enabled" in entry:
                    merged[field]["enabled"] = bool(entry["enabled"])
                if "value" in entry:
                    try:
                        merged[field]["value"] = float(entry["value"])
                    except (TypeError, ValueError):
                        pass
        return merged

    def set_safety_limit(self, device_id: str, field: str, enabled: bool, value: float) -> None:
        kind = device_kind(device_id)
        valid_fields = {f for f, *_ in SAFETY_LIMIT_FIELDS.get(kind, [])}
        if field not in valid_fields:
            return
        current = self.device_safety_limits(device_id, kind)
        if current[field]["enabled"] == enabled and current[field]["value"] == value:
            return
        current[field] = {"enabled": enabled, "value": value}
        all_limits = self._data.get("safety_limits")
        if not isinstance(all_limits, dict):
            all_limits = {}
        all_limits[device_id] = current
        self._data["safety_limits"] = all_limits
        self._save()
        self.safety_limits_changed.emit(copy.deepcopy(all_limits))
