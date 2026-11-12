"""Claude (MCP) im Verbund (PI-32 a): Befehle an durchgereichte Partnergeraete ausfuehren.

Der MCP-Server spricht weiter nur mit diesem Rechner. share_api zeigt Aufrufern auf diesem Rechner die
Kacheln des Partners unter "@<rechner>/<id>" (share_api.partner_tiles); eine Aktion daran kommt ueber
RemoteBridge.partner_action_requested hier an.

Fuer Claude gelten dieselben Regeln wie fuer einen gemeinsamen Ablauf:

- Belegung: bevor ein Befehl an ein Partnergeraet geht, belegt dieser Rechner es beim Partner (Lauf
  "Claude (MCP)"). Am Partner ist die Kachel dann gesperrt ("belegt durch PC"), ALLE AUS, Handbedienung
  und Sicherheitsabschaltung gehen dort weiter. Weitere Geraete kommen dazu, indem die Belegung mit der
  erweiterten Liste neu angefordert wird.
- Lebenszeichen: PartnerLink meldet sich jede Sekunde; fehlt es 3 s (PC haengt, Kabel ab), schaltet der
  Partner die belegten Geraete ab.
- Ende: die Belegung endet nach IDLE_RELEASE_S ohne Claude-Befehl, bei ALLE AUS, Sicherheitsabschaltung,
  Start eines Testablaufs und wenn der Verbund umgestellt wird. Freigeben schaltet nichts ab.

PartnerLink kennt genau eine Belegung. Ablauf und Claude schliessen sich ohnehin aus (waehrend eines
Ablaufs lehnt dieser Rechner Fernsteuer-Befehle mit "locked" ab); vor einem Ablauf gibt
MainWindow die Claude-Belegung frei (release).
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Slot

import remote_actions
from partner_link import PartnerLink
from share_remote import RemoteBridge

logger = logging.getLogger(__name__)

LEASE_RUN = "Claude (MCP)"
# Ohne Claude-Befehl so lange halten, dann freigeben (Partnerkacheln wieder frei bedienbar).
IDLE_RELEASE_S = 600.0
IDLE_CHECK_MS = 10_000


class PartnerPassthrough(QObject):
    def __init__(self, partner: PartnerLink, bridge: RemoteBridge, blocked: Callable[[], str],
                 parent: QObject | None = None) -> None:
        """blocked(): "" wenn Fernsteuer-Befehle gerade erlaubt sind, sonst der Ablehnungscode
        ("locked" bei Sicherheitsabschaltung/Testablauf, "inactive" ohne Hauptschalter)."""
        super().__init__(parent)
        self._partner = partner
        self._bridge = bridge
        self._blocked = blocked
        self._devices: set[str] = set()          # beim Partner fuer Claude belegt (IDs beim Partner)
        self._acquiring: set[str] | None = None  # angefordert, Antwort steht aus
        self._queue: list[tuple] = []            # Befehle, die auf die Belegung warten
        self._requests: dict[int, int] = {}      # Anfrage-Nr. PartnerLink -> Anfrage-Nr. RemoteBridge
        self._last_use = 0.0
        bridge.partner_action_requested.connect(self.handle)
        partner.action_finished.connect(self._on_action_finished)
        self._idle = QTimer(self)
        self._idle.setInterval(IDLE_CHECK_MS)
        self._idle.timeout.connect(self._check_idle)
        self._idle.start()

    # -- Zustand (MainWindow, Pruefungen) ----------------------------------------

    def devices(self) -> set[str]:
        return set(self._devices)

    def expects_lease(self) -> bool:
        """Wartet eine Belegungsanfrage auf Antwort? (MainWindow leitet lease_acquired dann hierher.)"""
        return self._acquiring is not None

    # -- Befehle -------------------------------------------------------------------

    @Slot(int, str, str, str, str, float, int, str)
    def handle(self, request_id: int, remote_id: str, device_id: str, kind: str, action: str,
               value: float, channel: int, client: str) -> None:
        reason = self._blocked()
        if reason:
            self._bridge.complete(request_id, False, reason, "", 0.0)
            return
        if not self._partner.enabled or not self._partner.is_connected():
            self._bridge.complete(request_id, False, "partner_unreachable", self._partner.reason(), 0.0)
            return
        self._last_use = time.monotonic()
        item = (request_id, remote_id, device_id, kind, action, value, channel)
        if remote_id in self._devices and self._partner.lease_active():
            self._send(item)
            return
        self._queue.append(item)
        if self._acquiring is None:
            self._acquire()

    def _acquire(self) -> None:
        wanted = self._devices | {item[1] for item in self._queue}
        logger.warning("Verbund: Claude (MCP) belegt bei %s: %s", self._partner.name(), ", ".join(sorted(wanted)))
        self._acquiring = wanted
        if not self._partner.acquire_lease(sorted(wanted), LEASE_RUN):
            self._acquiring = None
            self._fail_queue("partner_unreachable", "Verbund aus")

    @Slot(bool, str)
    def on_lease_acquired(self, ok: bool, message: str) -> None:
        """Von MainWindow weitergereicht, solange expects_lease() wahr ist."""
        wanted, self._acquiring = self._acquiring, None
        if wanted is None:
            return
        if not ok:
            # acquire_lease hat die alte Belegung bereits aufgegeben
            if self._devices:
                logger.warning("Verbund: Claude-Belegung bei %s aufgegeben (%s)", self._partner.name(),
                               ", ".join(sorted(self._devices)))
            self._devices = set()
            self._fail_queue("leased" if "belegt" in message else "partner_error", message)
            return
        self._devices = wanted
        waiting, self._queue = self._queue, []
        for item in waiting:
            if item[1] in self._devices:
                self._send(item)
            else:
                self._queue.append(item)   # kam waehrend der Anforderung dazu
        if self._queue:
            self._acquire()

    @Slot(str)
    def on_lease_lost(self, reason: str) -> None:
        if not self._devices and self._acquiring is None:
            return
        logger.error("Verbund: Claude-Belegung bei %s verloren: %s", self._partner.name(), reason)
        self._devices = set()
        self._acquiring = None
        self._fail_queue("partner_error", reason)

    def release(self, reason: str) -> None:
        """Claude-Belegung freigeben (schaltet nichts ab). Idempotent."""
        if not self._devices and self._acquiring is None and not self._queue:
            return
        logger.info("Verbund: Claude-Belegung bei %s freigegeben (%s)", self._partner.name(), reason)
        self._devices = set()
        self._acquiring = None
        self._fail_queue("locked", reason)
        self._partner.release_lease()

    def _send(self, item: tuple) -> None:
        request_id, remote_id, device_id, kind, action, value, channel = item
        definition = remote_actions.REMOTE_ACTIONS.get(kind, {}).get(action)
        if definition is None:
            self._bridge.complete(request_id, False, "unknown_action", "", 0.0)
            return
        partner_req = self._partner.send_action(
            remote_id, action, value if definition.needs_value else None,
            channel if definition.channels else None, f"Claude: {device_id}")
        if not partner_req:
            self._bridge.complete(request_id, False, "partner_unreachable", "Verbund aus", 0.0)
            return
        self._requests[partner_req] = request_id

    @Slot(int, str, str, bool, str)
    def _on_action_finished(self, partner_req: int, _device_id: str, _action: str, ok: bool, message: str) -> None:
        request_id = self._requests.pop(partner_req, None)
        if request_id is None:
            return   # Befehl eines Testablaufs (MainWindow._on_partner_action_finished)
        self._bridge.complete(request_id, ok, "" if ok else "partner_error", message, 0.0)

    def _fail_queue(self, code: str, message: str) -> None:
        waiting, self._queue = self._queue, []
        for item in waiting:
            self._bridge.complete(item[0], False, code, message, 0.0)

    def _check_idle(self) -> None:
        if self._devices and time.monotonic() - self._last_use > IDLE_RELEASE_S:
            self.release(f"{IDLE_RELEASE_S / 60:.0f} min kein Befehl von Claude")
