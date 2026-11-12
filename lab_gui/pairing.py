"""Kopplung zweier LabControl-Instanzen (Verbund): Token austauschen, ohne sie abzutippen.

Ablauf wie beim Bluetooth-Koppeln. A (fragt, z. B. der PC) kennt nur die Adresse von B (z. B. der Pi):

  1. A  POST /api/v1/pair {"host", "name", "port"}       -> B legt eine Anfrage an und antwortet mit
                                                             {"pair": <geheime ID>, "code": "123456"}
  2. A und B zeigen denselben 6-stelligen Code; an B bestaetigt der Nutzer mit "Koppeln" (oder lehnt ab).
     Der Code zeigt, dass die Anfrage am Bildschirm von B wirklich die von A ist -- eine fremde Anfrage
     haette einen anderen Code.
  3. A  GET  /api/v1/pair/<id>                           -> nach der Bestaetigung {"status": "accepted",
                                                             "token": <Token von B>, "port", "host"}
  4. A  POST /api/v1/pair/<id>/complete {"token": <Token von A>}
  5. Beide tragen den anderen als Verbund-Partner ein (Adresse, Token) und schalten den Verbund ein.
     B nimmt als Adresse die Absenderadresse der Anfrage und den von A gemeldeten Port.

Diese Routen brauchen keinen Token (den gibt es ja erst danach); geschuetzt sind sie durch die
Bestaetigung am Bildschirm, die geheime Anfrage-ID (128 Bit), eine Frist von PAIR_TTL_S und hoechstens
eine offene Anfrage gleichzeitig. Wie die Freigabe selbst laeuft das unverschluesselt im lokalen Netz.

Dieses Modul ist Qt-frei: PairingTable lebt in live_state.LiveState (Server-Thread und GUI-Thread),
die Client-Aufrufe stehen in partner_client.PartnerClient (pair_*).
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

PAIR_TTL_S = 60.0          # so lange wartet eine Anfrage auf die Bestaetigung bzw. den Abschluss
REJECT_COOLDOWN_S = 3.0    # nach Ablehnen/Abbruch keine neue Anfrage (gegen Dialog-Flut)
CODE_DIGITS = 6
MAX_NAME = 24


class PairBusy(Exception):
    """Es laeuft schon eine Kopplungsanfrage (oder gerade wurde eine abgelehnt)."""


@dataclass
class PairRequest:
    pair_id: str
    code: str
    client_ip: str
    host: str
    name: str
    port: int
    created: float
    status: str = "pending"      # pending -> accepted -> done | rejected | cancelled | expired
    shown: bool = False          # Bestaetigungsdialog geoeffnet (GUI-Thread)
    token_delivered: bool = False
    peer_token: str = ""         # Token von A, nach complete
    applied: bool = False        # Verbund-Einstellungen uebernommen (GUI-Thread)
    # Programm statt Verbund-Partner (PI-44 b): bekommt nach der Bestaetigung einmal einen eigenen Schluessel
    # (program_token, vom GUI-Thread erzeugt und gespeichert); ein Gegen-Token gibt es nicht
    kind: str = "partner"
    program_token: str = ""

    def label(self) -> str:
        return self.name or self.host or self.client_ip

    def address(self) -> str:
        return f"{self.client_ip}:{self.port}"


def format_code(code: str) -> str:
    """'123456' -> '123 456' (leichter zu vergleichen)."""
    return f"{code[:3]} {code[3:]}" if len(code) == CODE_DIGITS else code


class PairingTable:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._current: PairRequest | None = None
        self._blocked_until = 0.0

    def _expire(self, now: float) -> None:
        req = self._current
        if req is not None and req.status in ("pending", "accepted") and now - req.created > PAIR_TTL_S:
            req.status = "expired"

    # -- Server-Thread (share_api) ---------------------------------------------

    def request(self, client_ip: str, host: str, name: str, port: int, kind: str = "partner") -> PairRequest:
        now = self._clock()
        with self._lock:
            self._expire(now)
            if now < self._blocked_until or (self._current is not None
                                              and self._current.status in ("pending", "accepted")):
                raise PairBusy()
            self._current = PairRequest(
                pair_id=secrets.token_urlsafe(16),
                code=f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}",
                client_ip=str(client_ip), host=str(host)[:64], name=str(name)[:MAX_NAME],
                port=int(port), created=now, kind="program" if kind == "program" else "partner")
            return self._copy(self._current)

    def status(self, pair_id: str) -> PairRequest | None:
        """Stand der Anfrage fuer A; None = unbekannte ID. Nach der Bestaetigung einmal mit Token bzw.
        (Programm) einmal mit dem eigenen Schluessel -- danach ist die Programm-Kopplung abgeschlossen."""
        with self._lock:
            self._expire(self._clock())
            req = self._match(pair_id)
            if req is None:
                return None
            copy = self._copy(req)
            if req.status == "accepted":
                req.token_delivered = True
                if req.kind == "program":
                    req.status = "done"
                    req.applied = True        # gespeichert hat der GUI-Thread schon beim Bestaetigen
                    req.program_token = ""    # nur einmal ausliefern
            return copy

    def complete(self, pair_id: str, token: str) -> bool:
        with self._lock:
            self._expire(self._clock())
            req = self._match(pair_id)
            if req is None or req.status != "accepted" or not req.token_delivered or not token:
                return False
            req.peer_token = str(token)[:200]
            req.status = "done"
            return True

    def cancel(self, pair_id: str) -> bool:
        with self._lock:
            req = self._match(pair_id)
            if req is None or req.status not in ("pending", "accepted"):
                return False
            req.status = "cancelled"
            self._blocked_until = self._clock() + REJECT_COOLDOWN_S
            return True

    # -- GUI-Thread (MainWindow) ---------------------------------------------

    def take_new(self) -> PairRequest | None:
        """Neue Anfrage, fuer die noch kein Bestaetigungsdialog offen ist."""
        with self._lock:
            self._expire(self._clock())
            req = self._current
            if req is None or req.status != "pending" or req.shown:
                return None
            req.shown = True
            return self._copy(req)

    def current(self) -> PairRequest | None:
        with self._lock:
            self._expire(self._clock())
            return self._copy(self._current) if self._current is not None else None

    def decide(self, pair_id: str, accept: bool, program_token: str = "") -> bool:
        """program_token: bei einer Programm-Kopplung der neue eigene Schluessel (Pflicht beim Annehmen)."""
        with self._lock:
            self._expire(self._clock())
            req = self._match(pair_id)
            if req is None or req.status != "pending":
                return False
            if accept and req.kind == "program":
                if not program_token:
                    return False
                req.program_token = program_token
            req.status = "accepted" if accept else "rejected"
            if not accept:
                self._blocked_until = self._clock() + REJECT_COOLDOWN_S
            return True

    def take_done(self) -> PairRequest | None:
        """Abgeschlossene Kopplung, deren Einstellungen noch zu uebernehmen sind (einmal)."""
        with self._lock:
            req = self._current
            if req is None or req.status != "done" or req.applied:
                return None
            req.applied = True
            return self._copy(req)

    # -- intern --------------------------------------------------------------

    def _match(self, pair_id: str) -> PairRequest | None:
        req = self._current
        if req is None or not pair_id or not secrets.compare_digest(str(pair_id), req.pair_id):
            return None
        return req

    @staticmethod
    def _copy(req: PairRequest) -> PairRequest:
        return PairRequest(**req.__dict__)
