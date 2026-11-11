"""Verbund Pi + PC, Stufe V1 (PI-23 a, PI-27 a): Verbindung zur Partner-Instanz.

Was diese Klasse tut:
  * fragt den Partner im Sekundentakt ab (Status + alle freigegebenen Kacheln mit Werten) und
    meldet das Ergebnis als PartnerSnapshot an die Oberflaeche (Reiter "Verbund", Status-Chip);
  * schickt Bedienungen an Partnergeraete -- der Reihe nach in EINEM Thread, damit "Spannung
    setzen" vor "Ausgang EIN" ankommt;
  * schickt ALLE AUS an den Partner -- in einem eigenen Thread, also nie hinter wartenden
    Bedienungen (wie share_remote es fuer die eigene Seite garantiert).

Was sie bewusst NICHT tut: pruefen, ob eine Bedienung zulaessig ist. Das macht der Besitzer des
Geraets (Token, Freigabe, Hauptschalter, Grenzwerte, Sperre) -- hier wird nur weitergereicht und
das Ergebnis gemeldet.

Threads: Abfrage-Thread, Befehls-Thread, ALLE-AUS-Threads. Keiner beruehrt Qt-Objekte; Ergebnisse
gehen ueber Signale dieses QObjects, das im GUI-Thread lebt (emit aus einem Python-Thread wird
zur Queued Connection, wie in share_remote.py).
"""
from __future__ import annotations

import itertools
import logging
import queue
import threading

from PySide6.QtCore import QObject, Signal

from partner_client import PartnerClient, PartnerError, PartnerSnapshot, normalize_url

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 1.0

# Fehlercodes der API in Worten (Statuszeile, Kachel). Unbekannte Codes erscheinen roh.
ERROR_TEXTS = {
    "unreachable": "nicht erreichbar",
    "bad_response": "keine LabControl-Antwort",
    "unauthorized": "Token falsch",
    "token_not_configured": "dort kein Token eingerichtet",
    "unknown_tile": "Gerät nicht freigegeben",
    "control_not_permitted": "Steuern dort nicht freigegeben",
    "remote_control_inactive": "Fernsteuerung dort aus",
    "locked": "dort gesperrt (Testlauf oder Sicherheitsabschaltung)",
    "device_offline": "Gerät dort getrennt",
    "value_out_of_range": "Wert außerhalb des Bereichs",
    "exceeds_safety_limit": "über dem Sicherheits-Grenzwert",
    "rate_limited": "zu viele Befehle",
    "busy": "dort beschäftigt",
    "device_error": "Gerät meldet Fehler",
    "all_off_incomplete": "ALLE AUS dort unvollständig",
}


def error_text(code: str) -> str:
    return ERROR_TEXTS.get(code, code)


class PartnerLink(QObject):
    # Neuer Stand vom Partner (None = keine Verbindung / Verbund aus)
    snapshot_changed = Signal(object)
    # verbunden?, Grund als Text ("" wenn verbunden)
    connection_changed = Signal(bool, str)
    # device_id, Aktion, ok, Meldung
    action_finished = Signal(str, str, bool, str)
    # ok, Meldung
    all_off_finished = Signal(bool, str)

    # intern: (Generation, Snapshot | None, Fehlercode, Meldung) aus dem Abfrage-Thread
    _polled = Signal(int, object, str, str)

    def __init__(self, own_name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._own_name = own_name
        self._config: dict = {"enabled": False, "address": "", "token": "", "name": ""}
        self._client: PartnerClient | None = None
        self._generation = itertools.count(1)
        self._current_gen = 0
        self._stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._actions: queue.Queue = queue.Queue()
        self._action_thread: threading.Thread | None = None
        self._snapshot: PartnerSnapshot | None = None
        self._connected = False
        self._reason = "aus"
        self._polled.connect(self._on_polled)

    # -- Konfiguration -----------------------------------------------------------

    def apply(self, config: dict) -> None:
        """Aus Settings.partner_config_changed. Startet/stoppt die Abfrage bei jeder Aenderung."""
        self._config = dict(config)
        self._shutdown_threads()
        self._snapshot = None
        url = normalize_url(config.get("address", "")) if config.get("enabled") else ""
        if not url:
            self._client = None
            self._set_connection(False, "aus" if not config.get("enabled") else "Adresse fehlt")
            self.snapshot_changed.emit(None)
            return
        self._client = PartnerClient(url, config.get("token", ""), self._own_name)
        self._current_gen = next(self._generation)
        self._stop = threading.Event()
        self._actions = queue.Queue()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, args=(self._client, self._current_gen, self._stop),
            name="PartnerPoll", daemon=True)
        self._action_thread = threading.Thread(
            target=self._action_loop, args=(self._client, self._actions, self._stop),
            name="PartnerActions", daemon=True)
        self._poll_thread.start()
        self._action_thread.start()
        self._set_connection(False, "verbinde …")
        logger.info("Verbund: Partner %s eingetragen", url)

    def stop(self) -> None:
        """Beim Beenden der App: Threads anhalten (sie sind daemon, warten aber nicht lange)."""
        self._shutdown_threads()

    def _shutdown_threads(self) -> None:
        self._stop.set()
        self._actions.put(None)   # Befehls-Thread aufwecken
        self._current_gen = 0
        self._poll_thread = self._action_thread = None

    # -- Zustand -----------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def url(self) -> str:
        return self._client.base_url if self._client else ""

    def is_connected(self) -> bool:
        return self._connected

    def reason(self) -> str:
        return self._reason

    def snapshot(self) -> PartnerSnapshot | None:
        return self._snapshot

    def name(self) -> str:
        """Anzeigename: eingetragener Name, sonst Rechnername des Partners, sonst "Partner"."""
        if self._config.get("name"):
            return self._config["name"]
        if self._snapshot is not None and self._snapshot.host:
            return self._snapshot.host
        return "Partner"

    def _set_connection(self, connected: bool, reason: str) -> None:
        changed = connected != self._connected or reason != self._reason
        self._connected, self._reason = connected, reason
        if changed:
            self.connection_changed.emit(connected, reason)

    def _on_polled(self, generation: int, snapshot, code: str, message: str) -> None:
        if generation != self._current_gen:
            return   # Ergebnis einer alten Konfiguration
        if snapshot is None:
            was = self._connected
            self._snapshot = None
            reason = error_text(code) + (f" ({message})" if message and code == "unreachable" else "")
            self._set_connection(False, reason)
            if was:
                logger.warning("Verbund: Verbindung zu %s verloren: %s", self.url, reason)
            self.snapshot_changed.emit(None)
            return
        if not self._connected:
            logger.info("Verbund: verbunden mit %s (%s, LabControl %s, %d Geräte)",
                        snapshot.host or "?", self.url, snapshot.version, len(snapshot.tiles))
        self._snapshot = snapshot
        self._set_connection(True, "")
        self.snapshot_changed.emit(snapshot)

    # -- Threads -----------------------------------------------------------------

    def _poll_loop(self, client: PartnerClient, generation: int, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                snap = client.snapshot()
                self._polled.emit(generation, snap, "", "")
            except PartnerError as exc:
                self._polled.emit(generation, None, exc.code, str(exc))
            except Exception as exc:   # nie den Thread verlieren
                logger.exception("Verbund: Abfrage fehlgeschlagen")
                self._polled.emit(generation, None, "bad_response", str(exc))
            stop.wait(POLL_INTERVAL_S)

    def _action_loop(self, client: PartnerClient, actions: queue.Queue, stop: threading.Event) -> None:
        while not stop.is_set():
            item = actions.get()
            if item is None or stop.is_set():
                return
            device_id, action, value, channel, label = item
            details = ", ".join(part for part in (
                f"wert={value:g}" if value is not None else "",
                f"kanal={channel}" if channel is not None else "") if part)
            what = f"Verbund {label or device_id}: {action}" + (f" ({details})" if details else "")
            try:
                payload = client.action(device_id, action, value, channel)
            except PartnerError as exc:
                message = error_text(exc.code)
                logger.warning("%s -> FEHLER %s", what, message)
                self.action_finished.emit(device_id, action, False, message)
                continue
            except Exception as exc:
                logger.exception("%s -> FEHLER", what)
                self.action_finished.emit(device_id, action, False, str(exc))
                continue
            if payload.get("ok") is None:
                logger.warning("%s -> unbestätigt (Gerät hat nicht rechtzeitig geantwortet)", what)
                self.action_finished.emit(device_id, action, False, "unbestätigt, Werte prüfen")
            else:
                logger.info("%s -> ok", what)
                self.action_finished.emit(device_id, action, True, str(payload.get("message") or ""))

    # -- Bedienen ----------------------------------------------------------------

    def send_action(self, device_id: str, action: str, value: float | None = None,
                    channel: int | None = None, label: str = "") -> bool:
        """Reiht eine Bedienung ein. False, wenn der Verbund aus ist."""
        if self._client is None:
            return False
        self._actions.put((device_id, action, value, channel, label))
        return True

    def all_off(self, reason: str) -> bool:
        """ALLE AUS am Partner (PI-27 a). Eigener Thread, damit es nie hinter Bedienungen wartet.
        False, wenn der Verbund aus ist (dann gibt es nichts weiterzureichen)."""
        client = self._client
        if client is None:
            return False

        def run() -> None:
            try:
                payload = client.all_off()
            except PartnerError as exc:
                failures = exc.payload.get("failures") if isinstance(exc.payload, dict) else None
                message = error_text(exc.code) + (f": {', '.join(failures)}" if failures else "")
                logger.error("Verbund: ALLE AUS an %s FEHLGESCHLAGEN (%s): %s", client.base_url, reason, message)
                self.all_off_finished.emit(False, message)
                return
            except Exception as exc:
                logger.exception("Verbund: ALLE AUS an %s fehlgeschlagen", client.base_url)
                self.all_off_finished.emit(False, str(exc))
                return
            ok = payload.get("ok") is True
            logger.warning("Verbund: ALLE AUS an %s (%s) -> %s", client.base_url, reason,
                           "ok" if ok else "unbestätigt")
            self.all_off_finished.emit(ok, "" if ok else "unbestätigt")

        logger.warning("Verbund: ALLE AUS an %s wird gesendet (%s)", client.base_url, reason)
        threading.Thread(target=run, name="PartnerAllOff", daemon=True).start()
        return True
