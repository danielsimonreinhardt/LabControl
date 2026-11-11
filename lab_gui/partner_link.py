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
import time

from PySide6.QtCore import QObject, Signal

from partner_client import PartnerClient, PartnerError, PartnerSnapshot, normalize_url

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 1.0
# Belegung (PI-26 a): Lebenszeichen jede Sekunde; der Besitzer schaltet nach 3 s ohne ab, der Leitstand
# bricht nach 3 s ohne bestaetigtes Lebenszeichen ab -- beide Seiten mit derselben Frist.
LEASE_RENEW_S = 1.0
LEASE_TTL_S = 3.0


def _quiet(call, *args) -> None:
    """Aufruf ohne Fehlermeldung (Freigeben einer Belegung: klappt es nicht, laeuft sie beim Partner ab)."""
    try:
        call(*args)
    except Exception:  # noqa: BLE001
        pass

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
    "leased": "dort belegt",
    "unknown_lease": "Belegung abgelaufen",
}


def error_text(code: str) -> str:
    return ERROR_TEXTS.get(code, code)


class PartnerLink(QObject):
    # Neuer Stand vom Partner (None = keine Verbindung / Verbund aus)
    snapshot_changed = Signal(object)
    # verbunden?, Grund als Text ("" wenn verbunden)
    connection_changed = Signal(bool, str)
    # Anfrage-Nr. (aus send_action), device_id, Aktion, ok, Meldung
    action_finished = Signal(int, str, str, bool, str)
    # ok, Meldung
    all_off_finished = Signal(bool, str)
    # Belegung (V2): ok, Meldung -- Antwort auf acquire_lease
    lease_acquired = Signal(bool, str)
    # Belegung verloren (Lebenszeichen nicht bestaetigt): Grund
    lease_lost = Signal(str)
    # intern: (Generation der Belegung, Belegungs-ID oder "", Fehlertext)
    _lease_result = Signal(int, str, str)

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
        self._request_ids = itertools.count(1)
        # Belegung: ID (vom Befehls-Thread gelesen -- Zuweisung einer str ist atomar), Generation gegen
        # verspaetete Antworten, Stopp-Ereignis des Lebenszeichen-Threads
        self._lease_id = ""
        self._lease_gen = 0
        self._lease_gens = itertools.count(1)
        self._lease_stop = threading.Event()
        self._polled.connect(self._on_polled)
        self._lease_result.connect(self._on_lease_result)

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
        self.release_lease()
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
            request_id, device_id, action, value, channel, label = item
            details = ", ".join(part for part in (
                f"wert={value:g}" if value is not None else "",
                f"kanal={channel}" if channel is not None else "") if part)
            what = f"Verbund {label or device_id}: {action}" + (f" ({details})" if details else "")
            try:
                payload = client.action(device_id, action, value, channel, lease=self._lease_id)
            except PartnerError as exc:
                message = error_text(exc.code)
                if exc.code == "leased" and exc.payload.get("holder"):
                    message += f" ({exc.payload.get('holder')})"
                logger.warning("%s -> FEHLER %s", what, message)
                self.action_finished.emit(request_id, device_id, action, False, message)
                continue
            except Exception as exc:
                logger.exception("%s -> FEHLER", what)
                self.action_finished.emit(request_id, device_id, action, False, str(exc))
                continue
            if payload.get("ok") is None:
                logger.warning("%s -> unbestätigt (Gerät hat nicht rechtzeitig geantwortet)", what)
                self.action_finished.emit(request_id, device_id, action, False, "unbestätigt, Werte prüfen")
            else:
                logger.info("%s -> ok", what)
                self.action_finished.emit(request_id, device_id, action, True, str(payload.get("message") or ""))

    # -- Bedienen ----------------------------------------------------------------

    def send_action(self, device_id: str, action: str, value: float | None = None,
                    channel: int | None = None, label: str = "") -> int:
        """Reiht eine Bedienung ein. Liefert die Anfrage-Nr. (in action_finished), 0 wenn der
        Verbund aus ist."""
        if self._client is None:
            return 0
        request_id = next(self._request_ids)
        self._actions.put((request_id, device_id, action, value, channel, label))
        return request_id

    # -- Belegung und Lebenszeichen (PI-25 a, PI-26 a) -----------------------------

    def lease_active(self) -> bool:
        return bool(self._lease_id)

    def acquire_lease(self, devices: list[str], run: str) -> bool:
        """Belegt die Geraete beim Partner (eigener Thread). Antwort ueber lease_acquired; danach
        meldet sich diese Instanz alle LEASE_RENEW_S, bis release_lease(). False: Verbund aus."""
        client = self._client
        if client is None:
            return False
        self.release_lease()
        generation = next(self._lease_gens)
        self._lease_gen = generation

        def run_acquire() -> None:
            try:
                lease = client.lease_acquire(devices, run, LEASE_TTL_S)
            except PartnerError as exc:
                detail = exc.payload.get("device") or ""
                holder = exc.payload.get("holder") or ""
                text = error_text(exc.code) + (f": {detail}" if detail else "") + (f" ({holder})" if holder else "")
                self._lease_result.emit(generation, "", text)
                return
            except Exception as exc:
                self._lease_result.emit(generation, "", str(exc))
                return
            self._lease_result.emit(generation, lease, "")

        threading.Thread(target=run_acquire, name="PartnerLease", daemon=True).start()
        return True

    def _on_lease_result(self, generation: int, lease: str, error: str) -> None:
        client = self._client
        if generation < 0:   # Lebenszeichen-Thread: Belegung verloren
            if -generation == self._lease_gen and self._lease_id:
                self._lease_id = ""
                self._lease_gen = 0
                logger.error("Verbund: Belegung bei %s verloren: %s", self.url, error)
                self.lease_lost.emit(error)
            return
        if generation != self._lease_gen or client is None:
            if lease and client is not None:   # verspaetet (inzwischen abgebrochen) -> gleich freigeben
                threading.Thread(target=lambda: _quiet(client.lease_release, lease), daemon=True).start()
            return
        if not lease:
            logger.warning("Verbund: Belegung bei %s abgelehnt: %s", client.base_url, error)
            self.lease_acquired.emit(False, error)
            return
        self._lease_id = lease
        self._lease_stop = threading.Event()
        threading.Thread(target=self._heartbeat_loop, args=(client, lease, self._lease_stop, generation),
                         name="PartnerHeartbeat", daemon=True).start()
        logger.info("Verbund: Geräte bei %s belegt, Lebenszeichen alle %.0f s", client.base_url, LEASE_RENEW_S)
        self.lease_acquired.emit(True, "")

    def _heartbeat_loop(self, client: PartnerClient, lease: str, stop: threading.Event, generation: int) -> None:
        last_ok = time.monotonic()
        while not stop.wait(LEASE_RENEW_S):
            try:
                client.lease_renew(lease)
                last_ok = time.monotonic()
                continue
            except PartnerError as exc:
                if exc.code == "unknown_lease":
                    self._lease_lost_from_thread(generation, "Belegung beim Partner abgelaufen")
                    return
                problem = error_text(exc.code)
            except Exception as exc:   # nie den Thread verlieren
                problem = str(exc)
            if time.monotonic() - last_ok >= LEASE_TTL_S:
                self._lease_lost_from_thread(
                    generation, f"kein Lebenszeichen bestätigt seit {LEASE_TTL_S:.0f} s ({problem})")
                return

    def _lease_lost_from_thread(self, generation: int, reason: str) -> None:
        self._lease_result.emit(-generation, "", reason)

    def release_lease(self) -> None:
        """Belegung aufheben (Ablauf zu Ende/gestoppt). Idempotent."""
        lease, client = self._lease_id, self._client
        self._lease_id = ""
        self._lease_gen = 0
        self._lease_stop.set()
        if lease and client is not None:
            logger.info("Verbund: Belegung bei %s freigegeben", client.base_url)
            threading.Thread(target=lambda: _quiet(client.lease_release, lease), daemon=True).start()

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
