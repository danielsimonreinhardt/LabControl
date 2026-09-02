"""Verwaltet stabile, vom Nutzer vergebene Labels fuer Geraete-Instanzen.

Eine Device-ID (siehe device_worker.py: _resolve_device_ids) bleibt ueber
Neustarts und Portwechsel stabil (basiert i.d.R. auf der USB-Seriennummer).
Labels werden lokal in einer JSON-Datei gespeichert, damit z.B. zwei
baugleiche Netzteile dauerhaft unterscheidbar bleiben ("Bank A"/"Bank B"),
auch wenn sie an unterschiedlichen USB-Ports stecken oder das Geraet
zwischenzeitlich getrennt war.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal, Slot

from paths import app_dir

LABELS_PATH = app_dir() / "device_labels.json"

KIND_DISPLAY = {
    "load": "Last",
    "psu": "Netzteil",
}


class DeviceRegistry(QObject):
    # kind, device_id, label -- Geraet zum ersten Mal in dieser Session gesehen
    device_known = Signal(str, str, str)
    # kind, device_id -- Geraet aktuell nicht (mehr) verbunden
    device_offline = Signal(str, str)
    # kind, device_id, new_label
    label_changed = Signal(str, str, str)

    def __init__(self) -> None:
        super().__init__()
        self._labels: dict[str, str] = self._load()

    @staticmethod
    def _load() -> dict[str, str]:
        try:
            return json.loads(LABELS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            LABELS_PATH.write_text(
                json.dumps(self._labels, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # Label bleibt fuer die laufende Session gueltig, nur Persistenz betroffen

    @Slot(str, str)
    def on_device_added(self, kind: str, device_id: str) -> None:
        if device_id not in self._labels:
            existing = sum(1 for k, _ in self._parse_keys() if k == kind)
            self._labels[device_id] = f"{KIND_DISPLAY.get(kind, kind)} {existing + 1}"
            self._save()
        self.device_known.emit(kind, device_id, self._labels[device_id])

    @Slot(str, str)
    def on_device_removed(self, kind: str, device_id: str) -> None:
        self.device_offline.emit(kind, device_id)

    def rename(self, kind: str, device_id: str, new_label: str) -> None:
        new_label = new_label.strip()
        if not new_label:
            return
        self._labels[device_id] = new_label
        self._save()
        self.label_changed.emit(kind, device_id, new_label)

    def known_devices(self) -> list[tuple[str, str, str]]:
        """Alle jemals gespeicherten Geraete (kind, device_id, label),
        unabhaengig vom aktuellen Verbindungsstatus.

        Wird beim App-Start genutzt (siehe main_window._replay_known_
        devices), damit ein vor dem letzten Neustart bekanntes, gerade
        NICHT verbundenes Geraet sofort wieder als ausgegraute Dashboard-
        Kachel erscheint (siehe dashboard._DevicePanel.set_online), statt
        dass diese Information erst mit dem naechsten echten Verbinden
        entstuende -- vorher ging sie beim Beenden der App verloren, da nur
        der aktuelle Prozess (nicht die JSON-Datei) den "einmal gesehen"-
        Status kannte."""
        return [(kind, device_id, self._labels[device_id]) for kind, device_id in self._parse_keys()]

    def reset_all(self) -> list[tuple[str, str]]:
        """Loescht ALLE gespeicherten Labels (Button "Geraetezuordnung
        loeschen" im Einstellungen-Tab, siehe settings_tab.py).

        Gibt die zuvor bekannten (kind, device_id)-Paare zurueck, damit der
        Aufrufer (main_window._on_reset_devices_requested) jedem davon per
        on_device_added() sofort wieder einen frischen Standardnamen
        vergeben und live neu ausrollen kann (dieselbe Nummerierungslogik
        wie beim allerersten Verbinden) -- betrifft auch aktuell getrennte
        Geraete (z.B. ausgegraute Dashboard-Kacheln, siehe dashboard.py),
        deren Label sonst bis zum naechsten echten Wiederverbinden veraltet
        bliebe."""
        known = list(self._parse_keys())
        self._labels = {}
        self._save()
        return known

    def _parse_keys(self):
        # Labels tragen keinen "kind" mehr in sich (Key = device_id = "kind:...");
        # device_id-Praefix bis zum ersten ':' entspricht dem kind.
        for key in self._labels:
            prefix, _, _ = key.partition(":")
            yield prefix, key
