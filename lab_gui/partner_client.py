"""HTTP-Client fuer den Verbund-Partner (PI-23 a): die andere LabControl-Instanz, deren Geraete
diese hier ueber deren Netzwerk-Freigabe liest und bedient.

Bewusst ohne Qt und nur mit der Standardbibliothek: laeuft im Abfrage-Thread von partner_link.py
und laesst sich gegen einen echten ShareServer pruefen, ohne Fenster.

Nichts Neues auf der Leitung: dieselben Endpunkte wie fuer jeden anderen Client (MCP-Server,
ESP32-Anzeige). Die Pruefungen (Token, Freigabe, Hauptschalter, Grenzwerte) macht der Besitzer
des Geraets -- dieser Client verlaesst sich auf keine eigene Pruefung (Grundsatz 1 im Board:
jedes Geraet gehoert dem Rechner, an dem es steckt).

Einzige Ergaenzung: der Kopf PEER_HEADER mit dem Rechnernamen dieser Instanz. Daran erkennt der
Partner, dass eine Anfrage aus dem Verbund kommt -- fuers Log ("Verbund <name>") und damit ALLE AUS
nicht im Kreis zurueckgereicht wird (main_window._on_share_all_off).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from lease_table import LEASE_HEADER

PEER_HEADER = "X-LabControl-Peer"
DEFAULT_PORT = 8420
TIMEOUT_S = 4.0
# Ein Befehl wartet beim Besitzer bis zu share_remote.REMOTE_TIMEOUT_S (8 s) auf das Geraet,
# ALLE AUS bis ALL_OFF_TIMEOUT_S (15 s) -- etwas Luft fuer die Leitung dazu.
ACTION_TIMEOUT_S = 10.0
ALL_OFF_TIMEOUT_S = 18.0


class PartnerError(Exception):
    """Partner nicht erreichbar oder Anfrage abgewiesen. `code` ist der Fehlercode der API
    ("unauthorized", "remote_control_inactive", "locked", ...) oder "unreachable"/"bad_response"."""

    def __init__(self, code: str, message: str = "", status: int = 0, payload: dict | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.status = status
        self.payload = payload or {}


@dataclass
class PartnerSnapshot:
    """Ein Abfrage-Ergebnis: Status des Partners und seine freigegebenen Kacheln."""
    host: str = ""
    version: str = ""
    simulation: bool = False
    lock: str = "free"
    safety: str = "off"
    test_running: bool = False
    remote_effective: bool = False
    remote_remaining_s: float = 0.0
    tiles: list[dict] = field(default_factory=list)
    # PI-29: LAN-Oszilloskope, die der Partner eingetragen hat (Adressen)
    lan_scopes: list[str] = field(default_factory=list)
    # Uhr des Partners minus eigene Uhr in s (Mitte der Anfrage), None = Partner meldet keine "clock"
    clock_offset_s: float | None = None


def normalize_url(address: str) -> str:
    """'raspberrypi', '192.168.178.181:8420' oder 'http://pi:8420/' -> 'http://host:port'.
    Leere oder unbrauchbare Eingabe -> ''."""
    text = (address or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    scheme, _, rest = text.partition("://")
    if scheme not in ("http", "https") or not rest or "/" in rest:
        return ""
    host, sep, port = rest.rpartition(":")
    if not sep or "]" in port:          # kein Port angegeben (oder IPv6 ohne Port)
        return f"{scheme}://{rest}:{DEFAULT_PORT}"
    if not port.isdigit() or not 0 < int(port) < 65536 or not host:
        return ""
    return f"{scheme}://{host}:{int(port)}"


def resolve_partner_ip(address: str) -> str:
    """IPv4-Adresse des Partners aus der eingetragenen Adresse ('' wenn leer/unaufloesbar). Rechnernamen
    werden per DNS aufgeloest -- das kann dauern, deshalb nicht im GUI-Thread aufrufen (PI-39 b)."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit
    url = normalize_url(address)
    host = urlsplit(url).hostname if url else ""
    if not host:
        return ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        return socket.gethostbyname(host)
    except OSError:
        return ""


class PartnerClient:
    def __init__(self, base_url: str, token: str, own_name: str) -> None:
        self.base_url = base_url
        self._token = token
        self._own_name = own_name

    def _request(self, method: str, path: str, body: dict | None = None,
                 timeout: float = TIMEOUT_S, lease: str = "") -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header(PEER_HEADER, self._own_name)
        if lease:
            request.add_header(LEASE_HEADER, lease)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise PartnerError("unreachable", str(reason)) from None
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            raise PartnerError("bad_response", f"HTTP {status}", status) from None
        if not isinstance(payload, dict):
            raise PartnerError("bad_response", f"HTTP {status}", status)
        if status >= 400:
            raise PartnerError(str(payload.get("error") or f"http_{status}"),
                               str(payload.get("message") or ""), status, payload)
        return status, payload

    def snapshot(self) -> PartnerSnapshot:
        """Status und alle freigegebenen Kacheln mit Werten (zwei Anfragen)."""
        sent = time.time()
        _, status = self._request("GET", "/api/v1/status")
        received = time.time()
        if status.get("app") != "LabControl":
            raise PartnerError("bad_response", "kein LabControl")
        _, values = self._request("GET", "/api/v1/values")
        remote = status.get("remote_control") or {}
        tiles = values.get("tiles")
        clock = status.get("clock")
        scopes = status.get("lan_scopes")
        return PartnerSnapshot(
            host=str(status.get("host") or ""),
            version=str(status.get("version") or ""),
            simulation=bool(status.get("simulation")),
            lock=str(status.get("lock") or "free"),
            safety=str(status.get("safety") or "off"),
            test_running=bool(status.get("test_running")),
            remote_effective=bool(remote.get("effective")),
            remote_remaining_s=float(remote.get("remaining_s") or 0.0),
            tiles=[t for t in tiles if isinstance(t, dict)] if isinstance(tiles, list) else [],
            lan_scopes=[str(s) for s in scopes] if isinstance(scopes, list) else [],
            clock_offset_s=(float(clock) - (sent + received) / 2
                            if isinstance(clock, (int, float)) and not isinstance(clock, bool) else None),
        )

    def action(self, device_id: str, action: str, value: float | None = None,
               channel: int | None = None, lease: str = "") -> dict:
        """Eine Aktion am Partnergeraet. Liefert die Antwort ({"ok": True, ...} oder bei 202
        {"ok": None, "status": "pending"}); Abweisungen als PartnerError. `lease`: Belegungs-ID,
        wenn das Geraet von dieser Instanz belegt ist (Pflicht fuer belegte Geraete)."""
        body: dict = {"action": action}
        if value is not None:
            body["value"] = value
        if channel is not None:
            body["channel"] = channel
        _, payload = self._request("POST", f"/api/v1/tiles/{device_id}/actions", body, ACTION_TIMEOUT_S,
                                   lease=lease)
        return payload

    def meta(self, device_id: str, meta: dict) -> dict:
        """Name/Kachelfarbe eines Partnergeraets aendern (PI-43 b, share_api._tile_meta)."""
        _, payload = self._request("POST", f"/api/v1/tiles/{device_id}/meta", dict(meta), ACTION_TIMEOUT_S)
        return payload

    # -- Belegung (PI-25 a, PI-26 a; Gegenstueck: share_api._lease_*) ------------------

    def lease_acquire(self, devices: list[str], run: str, ttl_s: float) -> str:
        """Belegt die Geraete beim Partner; liefert die Belegungs-ID."""
        _, payload = self._request("POST", "/api/v1/leases", {"devices": devices, "run": run, "ttl_s": ttl_s})
        lease = payload.get("lease")
        if not isinstance(lease, str) or not lease:
            raise PartnerError("bad_response", "keine Belegungs-ID")
        return lease

    def lease_renew(self, lease: str) -> None:
        self._request("POST", f"/api/v1/leases/{lease}/renew", {}, timeout=2.0)

    def lease_release(self, lease: str) -> None:
        self._request("POST", f"/api/v1/leases/{lease}/release", {})

    # -- Kopplung (pairing.py; ohne Token, den gibt es erst danach) ---------------------

    def pair_request(self, host: str, name: str, port: int) -> dict:
        """Kopplungsanfrage an den anderen Rechner: {"pair", "code", "expires_s", "host"}."""
        _, payload = self._request("POST", "/api/v1/pair", {"host": host, "name": name, "port": port})
        if not isinstance(payload.get("pair"), str) or not isinstance(payload.get("code"), str):
            raise PartnerError("bad_response", "keine Kopplungs-ID")
        return payload

    def pair_status(self, pair_id: str) -> dict:
        """{"status": pending|accepted|rejected|cancelled|expired|done, nach "accepted" auch token/port/host}."""
        _, payload = self._request("GET", f"/api/v1/pair/{pair_id}", timeout=3.0)
        return payload

    def pair_complete(self, pair_id: str, token: str) -> None:
        self._request("POST", f"/api/v1/pair/{pair_id}/complete", {"token": token})

    def pair_cancel(self, pair_id: str) -> None:
        self._request("POST", f"/api/v1/pair/{pair_id}/cancel", {}, timeout=2.0)

    def all_off(self) -> dict:
        _, payload = self._request("POST", "/api/v1/all-off", {}, ALL_OFF_TIMEOUT_S)
        return payload
