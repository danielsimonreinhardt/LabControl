"""Belegung von Geraeten durch den Verbund-Partner (Verbund Pi + PC, Stufe V2; PI-25 a, PI-26 a).

Der Leitstand (die Instanz, die einen gemeinsamen Testablauf ausfuehrt) belegt beim Besitzer die
Geraete, die der Ablauf nutzt. Solange die Belegung besteht:
  * nimmt der Besitzer Bedienungen fuer diese Geraete nur vom Leitstand an (Kopf LEASE_HEADER mit
    der Belegungs-ID); jeder andere Aufrufer bekommt 409 "leased" (share_api);
  * sperrt der Besitzer die Kacheln dieser Geraete fuer die Bedienung vor Ort ("belegt durch PC"),
    ALLE AUS, Handbedienung und Sicherheitsabschaltung bleiben (main_window/control_tab);
  * muss der Leitstand sich mindestens alle `ttl_s` melden (renew). Bleibt das Lebenszeichen aus,
    gibt pop_expired() die Belegung heraus und der Besitzer schaltet die Geraete ab -- dasselbe
    Prinzip wie beim Batterie-Simulator ohne 0x130.

Qt-frei und thread-sicher: acquire/renew/release ruft der HTTP-Server-Thread, pop_expired/snapshot
der GUI-Thread. Die Uhr ist injizierbar (Pruefungen).
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

LEASE_HEADER = "X-LabControl-Lease"
DEFAULT_TTL_S = 3.0
MIN_TTL_S = 1.0
MAX_TTL_S = 10.0
MAX_DEVICES = 20


@dataclass
class Lease:
    lease_id: str
    devices: tuple[str, ...]
    holder: str          # Aufrufer wie share_server.client_label ("10.0.0.2 Verbund:PC")
    run: str             # Name des Ablaufs (Anzeige "belegt durch PC · <run>")
    ttl_s: float
    expires: float
    created: float = field(default=0.0)

    def holder_name(self) -> str:
        """Fuer die Anzeige: der Rechnername nach "Verbund:", sonst die Adresse."""
        _, _, name = self.holder.partition("Verbund:")
        return name or self.holder


class LeaseConflict(Exception):
    def __init__(self, device_id: str, lease: Lease) -> None:
        super().__init__(device_id)
        self.device_id = device_id
        self.lease = lease


class LeaseTable:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._leases: dict[str, Lease] = {}

    def acquire(self, devices: list[str], holder: str, run: str, ttl_s: float = DEFAULT_TTL_S) -> Lease:
        """Neue Belegung. LeaseConflict, wenn ein Geraet schon (von einer anderen, noch gueltigen
        Belegung) belegt ist. Eine abgelaufene, noch nicht abgeholte Belegung zaehlt nicht als
        Konflikt -- sie wird aber erst ueber pop_expired abgeraeumt (damit der Besitzer abschaltet)."""
        ttl = max(MIN_TTL_S, min(MAX_TTL_S, float(ttl_s)))
        now = self._clock()
        with self._lock:
            for lease in self._leases.values():
                if lease.expires <= now:
                    continue
                for device_id in devices:
                    if device_id in lease.devices:
                        raise LeaseConflict(device_id, lease)
            lease = Lease(secrets.token_urlsafe(12), tuple(dict.fromkeys(devices)), holder, run[:80],
                          ttl, now + ttl, now)
            self._leases[lease.lease_id] = lease
            return lease

    def renew(self, lease_id: str) -> Lease | None:
        """Lebenszeichen. None, wenn die Belegung nicht (mehr) besteht oder schon abgelaufen ist."""
        now = self._clock()
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None or lease.expires <= now:
                return None
            lease.expires = now + lease.ttl_s
            return lease

    def release(self, lease_id: str) -> Lease | None:
        with self._lock:
            return self._leases.pop(lease_id, None)

    def pop_expired(self) -> list[Lease]:
        """Abgelaufene Belegungen entfernen und zurueckgeben (GUI-Thread schaltet deren Geraete ab)."""
        now = self._clock()
        with self._lock:
            expired = [lease for lease in self._leases.values() if lease.expires <= now]
            for lease in expired:
                del self._leases[lease.lease_id]
            return expired

    def holder_of(self, device_id: str) -> Lease | None:
        """Gueltige Belegung dieses Geraets (oder None)."""
        now = self._clock()
        with self._lock:
            for lease in self._leases.values():
                if lease.expires > now and device_id in lease.devices:
                    return lease
        return None

    def snapshot(self) -> dict[str, Lease]:
        """{device_id: Lease} aller gueltigen Belegungen (Kopien)."""
        now = self._clock()
        with self._lock:
            return {d: Lease(**vars(lease)) for lease in self._leases.values() if lease.expires > now
                    for d in lease.devices}

    def clear(self) -> list[Lease]:
        with self._lock:
            leases = list(self._leases.values())
            self._leases.clear()
            return leases
