"""Persistente App-Einstellungen (Simulationsmodus, Dark Mode, Sprache,
geraete-individuelle Sicherheits-Grenzwerte, Desktop-Benachrichtigungen,
geraete-individuelle Panel-Hintergrundfarben, Dashboard-Kachel-Reihenfolge).

Analog zu device_registry.py lokal als JSON-Datei gespeichert, damit die
Einstellung Neustarts uebersteht.
"""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QObject, Signal

from i18n import DEFAULT_LANGUAGE
from live_state import DEFAULT_DEVICE_SHARE, default_share_config
from paths import IS_FROZEN, app_dir
from remote_actions import CONTROL_KINDS
from safety import SAFETY_LIMIT_FIELDS, default_device_limits, device_kind

SETTINGS_PATH = app_dir() / "settings.json"

# Bindeadressen der Netzwerk-Freigabe: entweder nur dieser PC oder alle
# Schnittstellen. Mehr Auswahl waere nur mehr Gelegenheit zum Vertippen.
SHARE_BIND_LOCAL = "127.0.0.1"
SHARE_BIND_LAN = "0.0.0.0"
SHARE_BIND_CHOICES = (SHARE_BIND_LOCAL, SHARE_BIND_LAN)
# Unter 1024 braucht es unter Unix Root-Rechte und unter Windows kollidiert
# man mit Systemdiensten -- fuer eine Labor-App gibt es keinen Grund dazu.
SHARE_PORT_MIN = 1024
SHARE_PORT_MAX = 65535
# Zeitfenster des Hauptschalters "Fernsteuerung aktiv" in Minuten. Nach oben
# begrenzt, damit aus einem Zeitfenster keine Dauerfreigabe wird (der Schalter
# selbst wird nicht gespeichert, siehe live_state.LiveState.set_remote_control).
SHARE_CONTROL_TIMEOUT_MIN = 1
SHARE_CONTROL_TIMEOUT_MAX = 480


def _clamp_port(port: int) -> int:
    return max(SHARE_PORT_MIN, min(SHARE_PORT_MAX, port))


def _control_allowed(device_id: str, read: bool, control: bool) -> bool:
    """Fernsteuerung setzt Lesefreigabe voraus (ohne Sicht auf die Kachel gibt
    es kein Steuern) und geht nur fuer fernsteuerbare Geraetearten -- CAN und
    Oszilloskop nie, siehe remote_actions.CONTROL_KINDS. Wird beim Lesen der
    settings.json ebenso angewandt wie im Setter, damit eine von Hand
    editierte Datei die Regel nicht umgehen kann."""
    return bool(control) and bool(read) and device_kind(device_id) in CONTROL_KINDS


def _clamp_control_timeout(minutes: int) -> int:
    return max(SHARE_CONTROL_TIMEOUT_MIN, min(SHARE_CONTROL_TIMEOUT_MAX, minutes))


class Settings(QObject):
    simulation_mode_changed = Signal(bool)
    dark_mode_changed = Signal(bool)
    dashboard_compact_changed = Signal(bool)
    language_changed = Signal(str)
    safety_limits_changed = Signal(dict)
    notifications_enabled_changed = Signal(bool)
    panel_colors_enabled_changed = Signal(bool)
    panel_color_changed = Signal(str, object)  # device_id, color_key (str | None)
    can_configs_changed = Signal(list)  # list[dict]: interface/channel/bitrate/label/dbc_path
    # Ein grobes Signal statt sechs feinen (Praezedenzfall can_configs_changed):
    # der HTTP-Server hat EINEN Lebenszyklus, sechs getrennte Neustart-Pfade
    # waeren nur fehleranfaelliger. Traegt den kompletten Freigabe-Block.
    share_config_changed = Signal(dict)

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
        # In Release-Builds (PyInstaller-.exe) hart gesperrt, auch wenn eine
        # aeltere settings.json (z.B. aus Dev-Betrieb) noch "true" enthaelt --
        # siehe FEATURES.md Punkt 4.
        if IS_FROZEN:
            return False
        return bool(self._data.get("simulation_mode", False))

    def set_simulation_mode(self, enabled: bool) -> None:
        if IS_FROZEN:
            return
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
    def panel_order(self) -> list[str]:
        """Zuletzt per Drag&Drop gewaehlte Dashboard-Kachel-Reihenfolge
        (Liste von device_ids, links nach rechts) -- siehe dashboard.py:
        DashboardWidget.set_panel_order()/panel_order_changed. Kein
        Live-Signal wie bei panel_color: die Reihenfolge betrifft nur das
        Dashboard selbst, kein zweiter Ort (Control-Tab o.ae.) muss
        synchron gehalten werden."""
        stored = self._data.get("panel_order")
        return list(stored) if isinstance(stored, list) else []

    def set_panel_order(self, order: list[str]) -> None:
        if order == self.panel_order:
            return
        self._data["panel_order"] = list(order)
        self._save()

    @property
    def control_tile_order(self) -> list[str]:
        """Zuletzt per Drag&Drop gewaehlte Control-Tab-Kachel-Reihenfolge
        (Liste von device_ids) -- siehe control_tab.py: ControlTab.
        set_tile_order()/tile_order_changed. Kein Live-Signal, exakt
        analog zu panel_order oben (nur der Control-Tab selbst liest/
        schreibt diese Reihenfolge)."""
        stored = self._data.get("control_tile_order")
        return list(stored) if isinstance(stored, list) else []

    def set_control_tile_order(self, order: list[str]) -> None:
        if order == self.control_tile_order:
            return
        self._data["control_tile_order"] = list(order)
        self._save()

    @property
    def can_configs(self) -> list[dict]:
        """Konfigurierte CAN-Interfaces:
        [{interface, channel, bitrate, label, dbc_path}, ...].

        Anders als bei Last/Netzteil gibt es fuer CAN keine Hotplug-
        Autodiscovery (siehe can_bus/README.md) -- die Liste ist die einzige
        Quelle, welche Interfaces device_worker.py ueberhaupt verbinden soll.

        "dbc_path" ist optional (fehlt oder leerer String, wenn keine DBC-
        Datei hinterlegt ist) -- Pfad zu einer .dbc-Datei, mit der
        device_worker.DeviceWorker empfangene CAN-Frames zusaetzlich zu den
        Rohdaten in benannte, skalierte Signale decodiert (siehe
        can_bus/dbc.py, FEATURES.md Punkt 3).
        """
        stored = self._data.get("can_configs")
        return copy.deepcopy(stored) if isinstance(stored, list) else []

    def set_can_configs(self, configs: list[dict]) -> None:
        self._data["can_configs"] = copy.deepcopy(configs)
        self._save()
        self.can_configs_changed.emit(copy.deepcopy(configs))

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

    # -- Netzwerk-Freigabe (siehe live_state.py, share_server.py) -----------

    @property
    def share_config(self) -> dict:
        """Kompletter Freigabe-Block, ueber die Defaults gemergt.

        Deep-Merge statt rohem Durchreichen -- exakt aus demselben Grund wie
        bei device_safety_limits(): eine aeltere oder von Hand editierte
        settings.json darf im HTTP-Server-Thread keinen KeyError ausloesen,
        wo niemand ihn abfaengt. Fehlende/kaputte Eintraege fallen auf den
        Default zurueck.
        """
        cfg = default_share_config()
        cfg["enabled"] = bool(self._data.get("share_enabled", cfg["enabled"]))
        cfg["read_requires_token"] = bool(
            self._data.get("share_read_requires_token", cfg["read_requires_token"])
        )
        cfg["access_log"] = bool(self._data.get("share_access_log", cfg["access_log"]))
        cfg["local_bypass"] = bool(self._data.get("share_local_bypass", cfg["local_bypass"]))
        bind = self._data.get("share_bind")
        if bind in SHARE_BIND_CHOICES:
            cfg["bind"] = bind
        try:
            cfg["port"] = _clamp_port(int(self._data.get("share_port", cfg["port"])))
        except (TypeError, ValueError):
            pass
        try:
            cfg["control_timeout_min"] = _clamp_control_timeout(
                int(self._data.get("share_control_timeout_min", cfg["control_timeout_min"]))
            )
        except (TypeError, ValueError):
            pass
        token = self._data.get("share_token")
        if isinstance(token, str):
            cfg["token"] = token
        stored = self._data.get("share_devices")
        if isinstance(stored, dict):
            for device_id, entry in stored.items():
                if not isinstance(device_id, str) or not isinstance(entry, dict):
                    continue
                flags = {
                    key: bool(entry.get(key, default))
                    for key, default in DEFAULT_DEVICE_SHARE.items()
                }
                flags["control"] = _control_allowed(device_id, flags["read"], flags["control"])
                cfg["devices"][device_id] = flags
        return cfg

    def _emit_share_config(self) -> None:
        self.share_config_changed.emit(self.share_config)

    def set_share_enabled(self, enabled: bool) -> None:
        if bool(enabled) == self.share_config["enabled"]:
            return
        self._data["share_enabled"] = bool(enabled)
        self._save()
        self._emit_share_config()

    def set_share_bind(self, bind: str) -> None:
        # Nur die zwei angebotenen Adressen -- die Sicherheitsentscheidung
        # "nur dieser PC" vs. "ganzes Netz" soll explizit und tippfehlerfrei
        # bleiben (im Einstellungen-Tab eine QComboBox, kein Freitext).
        if bind not in SHARE_BIND_CHOICES or bind == self.share_config["bind"]:
            return
        self._data["share_bind"] = bind
        self._save()
        self._emit_share_config()

    def set_share_port(self, port: int) -> None:
        try:
            port = _clamp_port(int(port))
        except (TypeError, ValueError):
            return
        if port == self.share_config["port"]:
            return
        self._data["share_port"] = port
        self._save()
        self._emit_share_config()

    def set_share_token(self, token: str) -> None:
        if not isinstance(token, str) or token == self.share_config["token"]:
            return
        self._data["share_token"] = token
        self._save()
        self._emit_share_config()

    def set_share_read_requires_token(self, required: bool) -> None:
        if bool(required) == self.share_config["read_requires_token"]:
            return
        self._data["share_read_requires_token"] = bool(required)
        self._save()
        self._emit_share_config()

    def set_share_local_bypass(self, enabled: bool) -> None:
        if bool(enabled) == self.share_config["local_bypass"]:
            return
        self._data["share_local_bypass"] = bool(enabled)
        self._save()
        self._emit_share_config()

    def set_share_access_log(self, enabled: bool) -> None:
        if bool(enabled) == self.share_config["access_log"]:
            return
        self._data["share_access_log"] = bool(enabled)
        self._save()
        self._emit_share_config()

    def set_share_control_timeout_min(self, minutes: int) -> None:
        try:
            minutes = _clamp_control_timeout(int(minutes))
        except (TypeError, ValueError):
            return
        if minutes == self.share_config["control_timeout_min"]:
            return
        self._data["share_control_timeout_min"] = minutes
        self._save()
        self._emit_share_config()

    def set_share_device(self, device_id: str, read: bool, control: bool) -> None:
        """Freigabe EINES Geraets. Analog zu set_panel_color geraete-individuell
        in einem Dict abgelegt (share_devices), nicht als eigener Schluessel."""
        entry = {"read": bool(read),
                 "control": _control_allowed(device_id, bool(read), bool(control))}
        if self.share_config["devices"].get(device_id) == entry:
            return
        stored = self._data.get("share_devices")
        devices = dict(stored) if isinstance(stored, dict) else {}
        if entry == DEFAULT_DEVICE_SHARE:
            devices.pop(device_id, None)  # Default nicht mitschleppen
        else:
            devices[device_id] = entry
        self._data["share_devices"] = devices
        self._save()
        self._emit_share_config()

    def reset_device_settings(self) -> None:
        """Loescht alle geraete-individuellen Einstellungen (Sicherheits-
        Grenzwerte + Panel-Farben) -- Teil des "Geraetezuordnung loeschen"-
        Buttons im Einstellungen-Tab (siehe settings_tab.py), zusammen mit
        DeviceRegistry.reset_all() (Labels).

        Meldet die Aenderung live fuer jedes betroffene Geraet (statt nur
        die Datei zu leeren), damit Dashboard/Control-Tab sofort wieder die
        Standardfarbe zeigen -- ueber set_panel_color(..., None) waere das
        hier NICHT passiert, da dessen "bereits gleich"-Kurzschluss (siehe
        dort) eine erneute Emission unterdrueckt haette, sobald die
        gespeicherte Farbe ohnehin schon None ist.
        """
        previous_colors = self._data.get("panel_colors")
        device_ids_with_color = list(previous_colors.keys()) if isinstance(previous_colors, dict) else []
        self._data["safety_limits"] = {}
        self._data["panel_colors"] = {}
        # Die Netzwerk-Freigabe ist ebenfalls geraete-individuell und muss
        # deshalb mit zurueckgesetzt werden -- sonst bliebe nach dem Loeschen
        # der Geraetezuordnung eine Kachel weiter nach aussen freigegeben,
        # obwohl der Button verspricht, alles Geraetebezogene zu entfernen.
        self._data["share_devices"] = {}
        self._save()
        self.safety_limits_changed.emit({})
        for device_id in device_ids_with_color:
            self.panel_color_changed.emit(device_id, None)
        self._emit_share_config()

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
