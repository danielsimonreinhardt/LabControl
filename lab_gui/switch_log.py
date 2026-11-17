"""Letzte Schaltung je Geraet und Aktion (1.0.1, PI-77 a).

Der Besitzer eines Geraets (die LabControl-Instanz, an der es steckt) merkt sich fuer jede erfolgreich
ausgefuehrte Aktion, die durch LabControl geht, wann und woher sie kam: Kachel, Preset, Ablauf, MCP/REST,
gekoppeltes Programm, Verbund-Partner, ALLE AUS. Die Frontplatte des Geraets zaehlt nicht (nicht erkennbar).
Nach einem Neustart ist nichts bekannt -- der Schritt "Warten seit Aktion" wartet dann die volle Zeit.

Zeitbasis ist die monotone Uhr dieses Rechners. Ueber das Netz (share_api, Kachelfeld "last_switch") geht nur
das ALTER in Sekunden, nie ein Zeitpunkt: so spielen die Uhren von PC und Pi keine Rolle. Der Empfaenger
rechnet mit dem Zeitpunkt, an dem die Antwort ankam, zurueck -- die Uebertragungszeit macht das Alter also
hoechstens kleiner (= laenger warten), nie groesser.

Thread-sicher: geschrieben wird im Worker-Thread (DeviceWorker) und im GUI-Thread (J-Link), gelesen im
GUI-Thread (TestRunner) und im Server-Thread (live_state.snapshot). Qt-frei.
"""
from __future__ import annotations

import threading
import time
from typing import NamedTuple

# Herkunft einer Schaltung (deutsche Schluessel wie im Log; Anzeige ueber tr())
SOURCE_TILE = "Kachel"
SOURCE_RUN = "Ablauf"
SOURCE_REMOTE = "MCP/REST"
SOURCE_PROGRAM = "Programm"
SOURCE_VERBUND = "Verbund"
SOURCE_ALL_OFF = "Alle aus"


class LastSwitch(NamedTuple):
    """Antwort auf "wann zuletzt?" fuer den Schritt "Warten seit Aktion".

    at:          monotonic() der letzten Schaltung, None = unbekannt
    source:      Herkunft (SOURCE_*, Anzeigetext); bei at=None der Grund als Anzeigetext ("" = seit dem
                 Start von LabControl nicht geschaltet)
    fresh_since: monotonic(), ab dem dieser Stand gilt -- lokal "jetzt", beim Partner der Zeitpunkt der
                 letzten Abfrage. Ein Stand von VOR dem Schrittbeginn kann eine Schaltung kurz davor noch
                 nicht kennen; der Runner wartet dann auf einen neueren.
    """
    at: float | None
    source: str
    fresh_since: float


class SwitchLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (device_id, action) -> (monotonic(), Quelle, Geraeteart)
        self._entries: dict[tuple[str, str], tuple[float, str, str]] = {}

    def note(self, kind: str, device_id: str, action: str, source: str, at: float | None = None) -> None:
        """Erfolgreich ausgefuehrte Aktion verbuchen (nur nach der Quittung aufrufen)."""
        if not device_id or not action:
            return
        with self._lock:
            self._entries[(device_id, action)] = (time.monotonic() if at is None else at, source, kind)

    def last(self, kind: str, device_id: str, action: str) -> tuple[float, str] | None:
        """(monotonic(), Quelle) der letzten Schaltung. Leere device_id ("automatisch"): juengste Schaltung
        der Aktion an irgendeinem Geraet der Art -- vorsichtig wie testcase_runner._last_action_time."""
        with self._lock:
            if device_id:
                hit = self._entries.get((device_id, action))
                return (hit[0], hit[1]) if hit else None
            hits = [(at, source) for (_device, act), (at, source, k) in self._entries.items()
                    if act == action and k == kind]
        return max(hits) if hits else None

    def lookup(self, kind: str, device_id: str, action: str) -> LastSwitch:
        now = time.monotonic()
        hit = self.last(kind, device_id, action)
        return LastSwitch(hit[0], hit[1], now) if hit else LastSwitch(None, "", now)

    def ages(self) -> dict[str, dict[str, dict]]:
        """Fuer die Freigabe: {device_id: {action: {"age_s", "source"}}} -- Alter statt Zeitpunkt."""
        now = time.monotonic()
        with self._lock:
            entries = list(self._entries.items())
        result: dict[str, dict[str, dict]] = {}
        for (device_id, action), (at, source, _kind) in entries:
            result.setdefault(device_id, {})[action] = {"age_s": round(max(0.0, now - at), 3), "source": source}
        return result


OWNER_WITHOUT_TIMES = "owner_without_times"   # Grund: Besitzer mit aelterer Version meldet keine Schaltzeiten


def from_tile(tile: dict, action: str, received: float, fresh_since: float) -> LastSwitch:
    """Kachel vom Verbund-Partner (PartnerSnapshot.tiles) -> LastSwitch auf DIESER Uhr, Quelle roh.

    received: monotonic() bei Ankunft der Antwort (Alter von dort zurueckgerechnet = vorsichtig),
    fresh_since: monotonic() beim Absenden der Abfrage."""
    switches = tile.get("last_switch")
    if not isinstance(switches, dict):
        return LastSwitch(None, OWNER_WITHOUT_TIMES, fresh_since)
    entry = switches.get(action)
    if not isinstance(entry, dict):
        return LastSwitch(None, "", fresh_since)
    age = entry.get("age_s")
    if not isinstance(age, (int, float)) or isinstance(age, bool) or age < 0:
        return LastSwitch(None, "", fresh_since)
    return LastSwitch(received - float(age), str(entry.get("source") or ""), fresh_since)
