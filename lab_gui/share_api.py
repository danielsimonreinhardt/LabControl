"""Routing, Zugriffspruefung und JSON-Aufbau der Netzwerk-Freigabe.

Bewusst OHNE Qt und ohne jedes Widget-Modul der App: rein geht ein
Schnappschuss-Dict (live_state.LiveState.snapshot()), raus kommt eine
Response. Dadurch laesst sich die gesamte Routing- und Auth-Logik ohne
QApplication und ohne offenen Socket pruefen -- genau deshalb ist sie vom
Server-Objekt (share_server.py) getrennt.

Sicherheitsleitplanken, die hier und nirgends sonst durchgesetzt werden:
  * Die Freigabeliste wird an EINER Stelle ausgewertet (visible_tiles) und
    gilt fuer jeden Pfad, auch fuer /display und /. Eine nicht freigegebene
    Kachel ist auch bei geratener ID unsichtbar.
  * "nicht freigegeben" und "existiert nicht" liefern die IDENTISCHE
    404-Antwort -- sonst liesse sich die Freigabeliste abtasten.
  * Token-Vergleich ausschliesslich mit hmac.compare_digest.
  * Kein Dateisystem-Zugriff: der Router ist eine explizite Tabelle, es gibt
    keinen Pfad, der auf eine Datei abbildet. Damit entfaellt die ganze
    Directory-Traversal-Klasse, statt sie filtern zu muessen.
  * Kein CORS-Header. Access-Control-Allow-Origin wuerde jeder Webseite, die
    der Nutzer im Browser oeffnet, Zugriff auf die API geben. Native Clients
    (MCP-Server, iOS-App) brauchen das nicht.

Lesen ist GET, Schreiben (Fernsteuerung) ist POST:
  POST /api/v1/tiles/{id}/actions   eine Aktion an einem Geraet
  POST /api/v1/all-off              ALLE AUS

Schreiben ist strenger als Lesen und wird in dieser Reihenfolge abgewiesen:
  Token (immer, nur als Authorization-Header, nie als ?token= -- URLs landen
  in Logs und Browser-Verlaeufen) -> Kachel sichtbar (404) -> Kachel fuer
  Steuern freigegeben (403) -> Hauptschalter "Fernsteuerung aktiv" an (403)
  -> Sperrzustand frei (409) -> Geraet online (409) -> Aktion/Wert/Kanal
  gueltig (400) -> Sicherheits-Grenzwert nicht ueberschritten (400).
ALLE AUS ist die einzige Ausnahme: es verlangt nur den Token und geht auch
bei ausgeschaltetem Hauptschalter, laufendem Testlauf und nach einer
Sicherheitsabschaltung -- es ist die Richtung, in der nichts kaputtgehen kann.

Ausgefuehrt wird ueber einen `executor` (im Betrieb share_remote.RemoteBridge),
den dispatch() als Parameter bekommt. So bleibt dieses Modul Qt-frei und mit
einem Stub pruefbar.

Oszilloskope (scope_api, Umsetzungsvorschlag 2026-09-28) haben eigene Routen,
ausgefuehrt ueber `scopes` (im Betrieb scope_service.ScopeService):
  GET  /api/v1/scopes                      Oszilloskope mit Zustand
  GET  /api/v1/scopes/{id}                 ein Oszilloskop
  GET  /api/v1/scopes/{id}/capabilities    Faehigkeiten
  POST /api/v1/scopes/{id}/acquire         eine Erfassung (blockiert bis Ergebnis)
  POST /api/v1/scopes/{id}/release         Verbindung sofort trennen
  GET  /api/v1/captures/{cid}              Ausschnitt einer Erfassung
  POST /api/v1/captures/{cid}/measure      Kennwerte neu berechnen
Erfassen braucht Token + Lesen + das eigene Haekchen "Messen" (E1), aber NICHT
den Hauptschalter: ein Oszilloskop hat keinen Ausgang. Ein Testablauf, der
das Oszilloskop nutzt, hat Vorrang (409 scope_in_test_run).
"""
from __future__ import annotations

import hmac
import json
import logging
import math
import secrets
import time
from urllib.parse import parse_qs, unquote, urlsplit

import remote_actions
from field_catalog import field_info, field_order
from share_render import render_display, render_index, render_text

from scope_api.base import ScopeError

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
API_VERSION = 1

# Obergrenzen gegen versehentliche oder boesartige Grossanfragen.
MAX_TILES_PER_REQUEST = 20
MAX_REFRESH_S = 3600
DEFAULT_REFRESH_S = 5

JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"
TEXT_TYPE = "text/plain; charset=utf-8"

# Obergrenze fuer den Rumpf einer POST-Anfrage. Eine Aktion ist ein paar
# Dutzend Byte gross; alles darueber ist ein Fehler oder ein Angriff.
MAX_BODY_BYTES = 4096


class Response:
    """Was der Handler in share_server.py auf die Leitung schreibt."""

    __slots__ = ("status", "content_type", "body", "headers")

    def __init__(self, status: int, content_type: str, body: bytes,
                 headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.content_type = content_type
        self.body = body
        self.headers = headers or {}


class RemoteResult:
    """Ergebnis einer Schreib-Aktion, wie der Executor es meldet.

    status: "ok" (Geraet hat bestaetigt), "error" (siehe code/message) oder
    "pending" (kein Ergebnis innerhalb der Wartezeit -- das Kommando ist
    unterwegs, sein Ausgang unbekannt; der Client prueft per GET nach).
    code bei "error": locked, inactive, rate_limited, busy, device_error,
    all_off_incomplete.
    """

    __slots__ = ("status", "code", "message", "value")

    def __init__(self, status: str, code: str = "", message: str = "", value: float = 0.0) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.value = value


def new_token() -> str:
    """Neuer Zugangstoken (~192 Bit).

    URL-sicher, weil er beim LESEN auch als ?token=... uebergeben werden darf --
    manche minimalen ESP32-HTTP-Clients koennen keinen Authorization-Header
    setzen. Zum Schreiben gilt nur der Header.
    """
    return secrets.token_urlsafe(24)


def _json(status: int, payload: dict, headers: dict[str, str] | None = None) -> Response:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Response(status, JSON_TYPE, body, headers)


def _error(status: int, code: str, **extra) -> Response:
    payload = {"v": API_VERSION, "error": code}
    payload.update(extra)
    return _json(status, payload)


# -- Freigabe ---------------------------------------------------------------

def visible_tiles(snapshot: dict) -> dict[str, dict]:
    """Die Geraete, die ueberhaupt nach aussen sichtbar sind.

    EINZIGE Stelle, an der die Freigabeliste ausgewertet wird -- jeder
    Endpoint geht hier durch, damit keiner sie versehentlich umgeht.
    """
    devices = snapshot.get("devices", {})
    shared = snapshot.get("share", {}).get("devices", {})
    return {
        device_id: entry
        for device_id, entry in devices.items()
        if shared.get(device_id, {}).get("read")
    }


def _controllable(snapshot: dict, device_id: str) -> bool:
    """Ist diese Kachel in den Einstellungen fuer Steuern freigegeben UND von
    einer fernsteuerbaren Geraeteart? Die zweite Haelfte ist ein zweites Netz
    unter Settings._control_allowed -- eine CAN-Kachel darf auch dann nicht
    steuerbar erscheinen, wenn jemand share_devices von Hand editiert."""
    flags = snapshot.get("share", {}).get("devices", {}).get(device_id, {})
    entry = snapshot.get("devices", {}).get(device_id, {})
    return bool(flags.get("control")) and entry.get("kind") in remote_actions.CONTROL_KINDS


def _measure_permitted(snapshot: dict, device_id: str) -> bool:
    """Ist dieses Oszilloskop fuer Erfassungen freigegeben (Haekchen "Messen")?
    Wie bei _controllable zusaetzlich an die Geraeteart gebunden."""
    flags = snapshot.get("share", {}).get("devices", {}).get(device_id, {})
    entry = snapshot.get("devices", {}).get(device_id, {})
    return bool(flags.get("measure")) and entry.get("kind") in remote_actions.SCOPE_KINDS


def remote_effective(snapshot: dict) -> bool:
    """Ist die Fernsteuerung FUER DIESEN AUFRUFER freigegeben?

    Entweder der Hauptschalter ist an -- oder der Aufruf kommt vom selben Rechner
    (snapshot["local"], gesetzt in dispatch) und die Einstellung "Zugriffe von
    diesem PC brauchen den Hauptschalter nicht" ist an. Nur diese eine Sperre
    entfaellt fuer lokale Aufrufer; Token, Steuern-Freigabe, Sperrzustand, Wert-
    und Grenzwertpruefung gelten fuer jeden.
    """
    if snapshot.get("remote", {}).get("active"):
        return True
    return bool(snapshot.get("local")) and bool(snapshot.get("share", {}).get("local_bypass"))


def _control_block_reason(snapshot: dict, device_id: str) -> str:
    """Warum ist Steuern gerade NICHT moeglich? \"\" = moeglich.

    Reihenfolge = Reihenfolge der Abweisungen in _tile_action, damit GET und
    POST dieselbe Antwort geben."""
    if not _controllable(snapshot, device_id):
        return "control_not_permitted"
    if not remote_effective(snapshot):
        return "remote_control_inactive"
    if snapshot.get("lock", "free") != "free":
        return "locked"
    if not snapshot.get("devices", {}).get(device_id, {}).get("online", True):
        return "device_offline"
    return ""


# -- Zugriffspruefung -------------------------------------------------------

def _presented_token(header_get, query: dict) -> str:
    """Token aus dem Authorization-Header oder aus ?token= .

    Der Query-Parameter ist die ESP32-Zugestaendnis: eine URL bekommt man in
    jeden noch so kleinen HTTP-Client, einen Header nicht immer.
    """
    auth = header_get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    values = query.get("token")
    return values[0] if values else ""


def check_auth(snapshot: dict, header_get, query: dict) -> Response | None:
    """None = erlaubt, sonst die abweisende Antwort.

    Gilt fuer alle LESENDEN Routen. Schreibende Routen gehen stattdessen
    durch check_write_auth: dass Lesen tokenfrei sein darf, ist eine
    Einstellung, dass Schreiben es nie ist, gehoert nicht zur Konfiguration.
    """
    share = snapshot.get("share", {})
    if not share.get("read_requires_token"):
        return None
    token = share.get("token") or ""
    if not token:
        # Token verlangt, aber keiner hinterlegt: das ist ein Konfigurations-
        # fehler auf Serverseite, kein Fehler des Clients -- deshalb 503 mit
        # eigenem Code statt eines irrefuehrenden 401, an dem der Nutzer
        # endlos Tokens ausprobieren wuerde.
        return _error(503, "token_not_configured")
    if hmac.compare_digest(_presented_token(header_get, query), token):
        return None
    return _json(401, {"v": API_VERSION, "error": "unauthorized"},
                 {"WWW-Authenticate": 'Bearer realm="LabControl"'})


# -- Kachel-Darstellung -----------------------------------------------------

def _tile_summary(device_id: str, entry: dict, snapshot: dict) -> dict:
    kind = entry.get("kind", "")
    return {
        "id": device_id,
        "kind": kind,
        "label": entry.get("label", device_id),
        "online": bool(entry.get("online", True)),
        "read": True,   # in visible_tiles bereits geprueft
        "control": _controllable(snapshot, device_id),
    }


def _tile_detail(device_id: str, entry: dict, snapshot: dict) -> dict:
    kind = entry.get("kind", "")
    labels = snapshot.get("labels", {})
    values = entry.get("fields", {})
    fields = []
    for key in field_order(kind, values):
        name, unit = field_info(kind, key)
        item = {"key": key, "name": labels.get((kind, key), name), "unit": unit}
        value = values[key]
        # Numerisch -> "value", Text/Enum -> "text". NIE beides, damit ein
        # Client auf Vorhandensein pruefen kann statt auf den Typ zu raten.
        if isinstance(value, str):
            item["text"] = value
        else:
            item["value"] = value
        fields.append(item)
    tile = _tile_summary(device_id, entry, snapshot)
    tile.update({
        "age_s": entry.get("age_s", 0.0),
        "stale": bool(entry.get("stale")),
        "lock": snapshot.get("lock", "free"),
        # Nennwerte des Geraets (GMAX), falls gemeldet -- der Rahmen, in dem Sollwerte
        # gueltig sind. Nur Netzteile melden sie.
        **({"ratings": entry["ratings"]} if entry.get("ratings") else {}),
        # Was an dieser Kachel steuerbar ist -- leer, wenn sie nicht fuer
        # Steuern freigegeben ist. "control_available" sagt, ob es JETZT geht,
        # "control_blocked" nennt sonst den Grund (dieselben Codes wie die
        # Fehlerantworten von POST .../actions).
        "actions": remote_actions.describe(tile["kind"], entry.get("ratings")) if tile["control"] else [],
        "control_available": _control_block_reason(snapshot, device_id) == "",
        "control_blocked": _control_block_reason(snapshot, device_id) if tile["control"] else "",
        "fields": fields,
    })
    return tile


def _requested_ids(query: dict, key: str) -> list[str] | None:
    raw = query.get(key)
    if not raw:
        return None
    ids: list[str] = []
    for chunk in raw[0].split(","):
        chunk = chunk.strip()
        if chunk and chunk not in ids:
            ids.append(chunk)
    return ids[:MAX_TILES_PER_REQUEST]


def _select(snapshot: dict, ids: list[str] | None) -> list[dict]:
    tiles = visible_tiles(snapshot)
    chosen = list(tiles) if ids is None else [i for i in ids if i in tiles]
    return [_tile_detail(i, tiles[i], snapshot) for i in chosen]


def _int_param(query: dict, key: str, default: int, low: int, high: int) -> int:
    raw = query.get(key)
    if not raw:
        return default
    try:
        return max(low, min(high, int(raw[0])))
    except (TypeError, ValueError):
        return default


# -- Endpoints --------------------------------------------------------------

def _status(snapshot: dict, app_info: dict) -> Response:
    share = snapshot.get("share", {})
    return _json(200, {
        "v": API_VERSION,
        "app": "LabControl",
        "version": app_info.get("version", ""),
        "lock": snapshot.get("lock", "free"),
        # Roher Watchdog-Zustand ("off"/"armed"/"tripped") zusaetzlich zum
        # abgeleiteten lock: nur so kann ein Client "keine Grenzwerte
        # konfiguriert" von "scharf und in Ordnung" unterscheiden.
        "safety": snapshot.get("safety", "off"),
        "test_running": bool(snapshot.get("test_running")),
        "simulation": bool(app_info.get("simulation")),
        "time": int(time.time()),
        "uptime_s": round(app_info.get("uptime_s", 0.0), 1),
        "tiles": len(visible_tiles(snapshot)),
        # Hauptschalter der Fernsteuerung. Ohne ihn geht KEIN Schreiben
        # ausser ALLE AUS; er schaltet sich nach remaining_s selbst ab.
        "remote_control": {
            "active": bool(snapshot.get("remote", {}).get("active")),
            "remaining_s": snapshot.get("remote", {}).get("remaining_s", 0.0),
            # "effective": gilt die Freigabe fuer DIESEN Aufrufer -- Hauptschalter an
            # ODER lokaler Aufruf mit aktiver Ausnahme. Das ist die Zahl, nach der sich
            # ein Client richten soll, nicht "active".
            "effective": remote_effective(snapshot),
            "local": bool(snapshot.get("local")),
            "local_bypass": bool(snapshot.get("share", {}).get("local_bypass")),
        },
        "auth": {
            "read_token_required": bool(share.get("read_requires_token")),
            # Keine Einstellung, sondern eine Zusicherung: Schreiben wird nie
            # ohne Token moeglich sein.
            "control_token_required": True,
        },
    })


def _tiles(snapshot: dict) -> Response:
    tiles = visible_tiles(snapshot)
    return _json(200, {
        "v": API_VERSION,
        "lock": snapshot.get("lock", "free"),
        "tiles": [_tile_summary(i, e, snapshot) for i, e in tiles.items()],
    })


def _tile(snapshot: dict, device_id: str) -> Response:
    tiles = visible_tiles(snapshot)
    entry = tiles.get(device_id)
    if entry is None:
        # Identische Antwort fuer "nicht freigegeben" und "gibt es nicht" --
        # siehe Modul-Docstring.
        return _error(404, "unknown_tile")
    payload = _tile_detail(device_id, entry, snapshot)
    payload["v"] = API_VERSION
    return _json(200, payload)


def _values(snapshot: dict, query: dict) -> Response:
    tiles = _select(snapshot, _requested_ids(query, "tiles"))
    return _json(200, {
        "v": API_VERSION,
        "lock": snapshot.get("lock", "free"),
        "tiles": tiles,
    })


def _display(snapshot: dict, query: dict) -> Response:
    ids = _requested_ids(query, "tiles") or _requested_ids(query, "tile")
    tiles = _select(snapshot, ids)
    lock = snapshot.get("lock", "free")
    if (query.get("fmt") or [""])[0] == "text":
        return Response(200, TEXT_TYPE, render_text(tiles, lock).encode("utf-8"))
    refresh = _int_param(query, "refresh", DEFAULT_REFRESH_S, 0, MAX_REFRESH_S)
    return Response(200, HTML_TYPE, render_display(tiles, refresh, lock).encode("utf-8"))


def _index(snapshot: dict, app_info: dict) -> Response:
    tiles = visible_tiles(snapshot)
    summaries = [_tile_summary(i, e, snapshot) for i, e in tiles.items()]
    html_text = render_index(summaries, app_info.get("base_url", ""))
    return Response(200, HTML_TYPE, html_text.encode("utf-8"))


# -- Schreiben (Fernsteuerung) -----------------------------------------------

def check_write_auth(snapshot: dict, header_get) -> Response | None:
    """Token fuer SCHREIBENDE Anfragen. None = erlaubt.

    Anders als beim Lesen: immer erforderlich (auch wenn Lesen tokenfrei
    erlaubt ist) und ausschliesslich im Authorization-Header. Ein ?token= in
    der URL wuerde in Zugriffslogs, Proxy-Logs und Browser-Verlaeufen landen.
    Ein Header erzwingt ausserdem bei jeder Webseite, die es per Browser
    versucht, einen CORS-Preflight, den dieser Server nie beantwortet.
    """
    token = snapshot.get("share", {}).get("token") or ""
    if not token:
        return _error(503, "token_not_configured")
    auth = header_get("Authorization") or ""
    presented = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if hmac.compare_digest(presented, token):
        return None
    return _json(401, {"v": API_VERSION, "error": "unauthorized"},
                 {"WWW-Authenticate": 'Bearer realm="LabControl"'})


def _parse_body(body: bytes) -> dict | None:
    if not body or len(body) > MAX_BODY_BYTES:
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _result_response(result: RemoteResult, ok_payload: dict) -> Response:
    """Uebersetzt das Ergebnis des Executors in eine HTTP-Antwort."""
    if result.status == "ok":
        return _json(200, {"v": API_VERSION, "ok": True, **ok_payload})
    if result.status == "pending":
        # 202 statt eines Fehlers: das Kommando ist abgeschickt und kann
        # durchaus noch ausgefuehrt werden. Der Client muss nachsehen.
        return _json(202, {"v": API_VERSION, "ok": None, "status": "pending", **ok_payload})
    table = {
        "locked": (409, "locked"),
        "inactive": (403, "remote_control_inactive"),
        "rate_limited": (429, "rate_limited"),
        "busy": (429, "busy"),
        "device_error": (502, "device_error"),
    }
    status, code = table.get(result.code, (502, result.code or "device_error"))
    extra = {"message": result.message} if result.message else {}
    headers = {"Retry-After": "1"} if status == 429 else None
    return _json(status, {"v": API_VERSION, "ok": False, "error": code, **extra}, headers)


def _tile_action(snapshot: dict, device_id: str, body: bytes, executor, client: str) -> Response:
    if executor is None:
        return _error(501, "not_implemented")
    entry = visible_tiles(snapshot).get(device_id)
    if entry is None:
        # Wie beim Lesen: "nicht freigegeben" und "gibt es nicht" sind
        # ununterscheidbar.
        return _error(404, "unknown_tile")
    payload = _parse_body(body)
    if payload is None:
        return _error(400, "invalid_body", max_bytes=MAX_BODY_BYTES)

    reason = _control_block_reason(snapshot, device_id)
    if reason == "control_not_permitted":
        return _error(403, reason)
    if reason == "remote_control_inactive":
        return _error(403, reason)
    if reason == "locked":
        return _error(409, "locked", lock=snapshot.get("lock", "free"))
    if reason == "device_offline":
        return _error(409, reason)

    kind = entry.get("kind", "")
    action = payload.get("action")
    if not isinstance(action, str):
        return _error(400, "unknown_action", allowed=list(remote_actions.REMOTE_ACTIONS.get(kind, {})))
    definition, code, detail = remote_actions.validate(
        kind, action, payload.get("value"), payload.get("channel"), entry.get("ratings"))
    if definition is None:
        return _error(400, code, **detail)

    value = float(payload["value"]) if definition.needs_value else 0.0
    channel = int(payload["channel"]) if definition.channels else 0

    violation = remote_actions.limit_violation(
        definition, value, snapshot.get("safety_limits", {}).get(device_id, {}))
    if violation is not None:
        field, limit = violation
        return _error(400, "exceeds_safety_limit", limit_field=field, limit=limit, unit=definition.unit)

    result = executor.submit_action(device_id, kind, action, value, channel, client,
                                    bool(snapshot.get("local")))
    return _result_response(result, {"device": device_id, "action": action,
                                     **({"value": value} if definition.needs_value else {}),
                                     **({"channel": channel} if definition.channels else {}),
                                     **({"message": result.message} if result.message else {})})


def _all_off(executor, client: str) -> Response:
    if executor is None:
        return _error(501, "not_implemented")
    result = executor.submit_all_off(client)
    if result.status == "error" and result.code == "all_off_incomplete":
        failures = [f for f in result.message.split(";") if f]
        return _json(502, {"v": API_VERSION, "ok": False, "error": "all_off_incomplete",
                           "failures": failures})
    return _result_response(result, {})


# -- Oszilloskope (scope_api) --------------------------------------------------

# HTTP-Status je ScopeError-Code (Codes siehe scope_api/base.py und scope_service.py).
SCOPE_ERROR_STATUS = {
    "invalid_request": 400, "setting_not_supported": 400, "level_out_of_range": 400,
    "duration_too_long": 400, "unknown_measurement": 400,
    "unknown_capture": 404,
    "device_offline": 409, "scope_in_test_run": 409, "scope_busy_external": 409,
    "scope_busy": 429,
    "device_error": 502,
    "trigger_timeout": 504,
}


def _scope_tiles(snapshot: dict) -> dict[str, dict]:
    return {d: e for d, e in visible_tiles(snapshot).items() if e.get("kind") in remote_actions.SCOPE_KINDS}


def _scope_error(exc: ScopeError) -> Response:
    status = SCOPE_ERROR_STATUS.get(exc.code, 502)
    headers = {"Retry-After": "2"} if status == 429 else None
    return _json(status, {"v": API_VERSION, "ok": False, **exc.to_dict()}, headers)


def _scope_summary(device_id: str, entry: dict, snapshot: dict, scopes) -> dict:
    permitted = _measure_permitted(snapshot, device_id)
    blocked = "measure_not_permitted" if not permitted else scopes.blocked_reason(device_id)
    return {
        "id": device_id,
        "label": entry.get("label", device_id),
        "online": bool(entry.get("online", True)),
        "measure": permitted,
        "measure_available": blocked == "",
        "measure_blocked": blocked,
        **scopes.describe(device_id),
    }


def _scopes_list(snapshot: dict, scopes) -> Response:
    return _json(200, {"v": API_VERSION, "scopes": [
        _scope_summary(d, e, snapshot, scopes) for d, e in _scope_tiles(snapshot).items()]})


def _scope_get(snapshot: dict, scopes, device_id: str, what: str) -> Response:
    entry = _scope_tiles(snapshot).get(device_id)
    if entry is None:
        return _error(404, "unknown_scope")
    if what == "":
        return _json(200, {"v": API_VERSION, **_scope_summary(device_id, entry, snapshot, scopes)})
    try:
        return _json(200, {"v": API_VERSION, "id": device_id, **scopes.capabilities(device_id)})
    except ScopeError as exc:
        return _scope_error(exc)


def _float_param(query: dict, key: str) -> float | None:
    raw = query.get(key)
    if not raw:
        return None
    try:
        value = float(raw[0])
    except ValueError:
        raise ScopeError("invalid_request", f"'{key}' muss eine Zahl sein.") from None
    if not math.isfinite(value):
        raise ScopeError("invalid_request", f"'{key}' muss eine Zahl sein.")
    return value


def _capture_permitted(snapshot: dict, scopes, capture_id: str) -> Response | None:
    """Erfassungen sind nur ueber ein sichtbares, fuer Messen freigegebenes
    Oszilloskop erreichbar -- sonst dieselbe 404 wie fuer eine unbekannte ID."""
    try:
        device_id = scopes.capture_device(capture_id)
    except ScopeError as exc:
        return _scope_error(exc)
    if device_id not in _scope_tiles(snapshot) or not _measure_permitted(snapshot, device_id):
        return _json(404, {"v": API_VERSION, "ok": False, "error": "unknown_capture"})
    return None


def _capture_get(snapshot: dict, scopes, capture_id: str, query: dict) -> Response:
    denied = _capture_permitted(snapshot, scopes, capture_id)
    if denied is not None:
        return denied
    try:
        result = scopes.excerpt(
            capture_id, (query.get("channel") or [None])[0], _float_param(query, "t_start_s"),
            _float_param(query, "t_stop_s"), _int_param(query, "max_points", 500, 2, 100_000),
            (query.get("mode") or ["minmax"])[0])
    except ScopeError as exc:
        return _scope_error(exc)
    return _json(200, {"v": API_VERSION, **result})


def _scope_post(snapshot: dict, scopes, device_id: str, what: str, body: bytes, client: str) -> Response:
    entry = _scope_tiles(snapshot).get(device_id)
    if entry is None:
        return _error(404, "unknown_scope")
    if not _measure_permitted(snapshot, device_id):
        return _error(403, "measure_not_permitted")
    if what == "release":
        result = scopes.release(device_id)
        logger.info("Oszilloskop von %s: %s trennen -> %s", client, device_id,
                    "getrennt" if result.get("released") else "war nicht verbunden")
        return _json(200, {"v": API_VERSION, "ok": True, "id": device_id, **result})
    payload = _parse_body(body)
    if payload is None:
        return _error(400, "invalid_body", max_bytes=MAX_BODY_BYTES)
    started = time.monotonic()
    try:
        report = scopes.acquire(device_id, payload)
    except ScopeError as exc:
        logger.info("Oszilloskop-Erfassung von %s: %s -> Fehler (%s)", client, device_id, exc.code)
        return _scope_error(exc)
    logger.info("Oszilloskop-Erfassung von %s: %s -> %s (%.1f s)", client, device_id,
                report.get("capture_id"), time.monotonic() - started)
    return _json(200, {"v": API_VERSION, "ok": True, **report})


def _capture_measure(snapshot: dict, scopes, capture_id: str, body: bytes) -> Response:
    denied = _capture_permitted(snapshot, scopes, capture_id)
    if denied is not None:
        return denied
    payload = _parse_body(body) if body else {}
    if payload is None:
        return _error(400, "invalid_body", max_bytes=MAX_BODY_BYTES)
    try:
        return _json(200, {"v": API_VERSION, "ok": True, **scopes.measure(capture_id, payload)})
    except ScopeError as exc:
        return _scope_error(exc)


def _scope_route(path: str) -> tuple[str, str, str] | None:
    """("scopes", "", "") | ("scope", id, ""|"capabilities"|"acquire"|"release")
    | ("capture", cid, ""|"measure") | None fuer alle anderen Pfade."""
    if path == f"{API_PREFIX}/scopes":
        return "scopes", "", ""
    for prefix, kind in ((f"{API_PREFIX}/scopes/", "scope"), (f"{API_PREFIX}/captures/", "capture")):
        if path.startswith(prefix):
            parts = path[len(prefix):].split("/")
            if len(parts) == 1 and parts[0]:
                return kind, parts[0], ""
            if len(parts) == 2 and parts[0] and parts[1]:
                return kind, parts[0], parts[1]
            return kind, "", "invalid"
    return None


_SCOPE_GET = {("scopes", ""), ("scope", ""), ("scope", "capabilities"), ("capture", "")}
_SCOPE_POST = {("scope", "acquire"), ("scope", "release"), ("capture", "measure")}


def _dispatch_scope(method: str, route, header_get, snapshot: dict, query: dict, body: bytes,
                    scopes, client: str) -> Response:
    kind, ident, what = route
    wanted = _SCOPE_POST if method == "POST" else _SCOPE_GET
    if (kind, what) not in wanted:
        # Erst die Route, dann der Token -- wie bei den uebrigen Pfaden.
        if (kind, what) in _SCOPE_GET:
            return _json(405, {"v": API_VERSION, "error": "method_not_allowed"}, {"Allow": "GET, HEAD"})
        if (kind, what) in _SCOPE_POST:
            return _json(405, {"v": API_VERSION, "error": "method_not_allowed"}, {"Allow": "POST"})
        return _error(404, "not_found")
    if scopes is None:
        return _error(501, "not_implemented")
    if method == "POST":
        denied = check_write_auth(snapshot, header_get)
        if denied is not None:
            return denied
        if kind == "scope":
            return _scope_post(snapshot, scopes, ident, what, body, client)
        return _capture_measure(snapshot, scopes, ident, body)
    denied = check_auth(snapshot, header_get, query)
    if denied is not None:
        return denied
    if kind == "scopes":
        return _scopes_list(snapshot, scopes)
    if kind == "scope":
        return _scope_get(snapshot, scopes, ident, what)
    return _capture_get(snapshot, scopes, ident, query)


# -- Dispatch ---------------------------------------------------------------

def _post_route(path: str) -> tuple[str, str] | None:
    """Erkennt die schreibenden Routen: ("all_off", "") oder ("action", id)."""
    if path == f"{API_PREFIX}/all-off":
        return "all_off", ""
    prefix, suffix = f"{API_PREFIX}/tiles/", "/actions"
    if path.startswith(prefix) and path.endswith(suffix) and len(path) > len(prefix) + len(suffix):
        device_id = path[len(prefix):-len(suffix)]
        if "/" not in device_id:
            return "action", device_id
    return None


_GET_ONLY_PATHS = ("/", "/display", f"{API_PREFIX}/status", f"{API_PREFIX}/tiles",
                   f"{API_PREFIX}/values")


def _dispatch_post(path: str, header_get, snapshot: dict, body: bytes, executor, client: str) -> Response:
    route = _post_route(path)
    if route is None:
        # Erst die Route, dann der Token: ein POST auf einen Lese-Pfad ist
        # ein Methodenfehler und verraet nichts, was eine Authentifizierung
        # schuetzen muesste.
        tile_prefix = f"{API_PREFIX}/tiles/"
        is_tile_read = path.startswith(tile_prefix) and "/" not in path[len(tile_prefix):]
        if path in _GET_ONLY_PATHS or is_tile_read:
            return _json(405, {"v": API_VERSION, "error": "method_not_allowed"},
                         {"Allow": "GET, HEAD"})
        return _error(404, "not_found")
    denied = check_write_auth(snapshot, header_get)
    if denied is not None:
        return denied
    kind, device_id = route
    if kind == "all_off":
        return _all_off(executor, client)
    return _tile_action(snapshot, device_id, body, executor, client)


def dispatch(method: str, raw_path: str, header_get, state, app_info: dict,
             body: bytes = b"", executor=None, client: str = "", local: bool = False,
             scopes=None) -> Response:
    """Eine Anfrage -> eine Antwort. Die einzige oeffentliche Einstiegsstelle.

    `header_get(name)` liefert einen Anfrage-Header (oder None), `state` ist
    ein Objekt mit .snapshot() (im Betrieb live_state.LiveState, im Test ein
    Stub). `body`/`executor`/`client` betreffen nur POST: der Rumpf, das
    Objekt, das Aktionen ausfuehrt (share_remote.RemoteBridge), und die
    Adresse des Aufrufers fuer das Protokoll. `local`: der Aufruf kommt vom selben
    Rechner (siehe share_server._is_local). `scopes`: Oszilloskop-Dienst
    (scope_service.ScopeService) fuer die /scopes- und /captures-Routen.
    Wirft nie -- jeder Fehler wird
    zu einer Antwort, weil ein durchgereichter Fehler im Server-Thread
    niemanden erreichen wuerde.
    """
    if method not in ("GET", "HEAD", "POST"):
        return _error(405, "method_not_allowed")

    split = urlsplit(raw_path)
    path = unquote(split.path).rstrip("/") or "/"
    query = parse_qs(split.query)

    snapshot = state.snapshot()
    snapshot["local"] = bool(local)

    scope_route = _scope_route(path)
    if scope_route is not None:
        try:
            return _dispatch_scope(method, scope_route, header_get, snapshot, query, body, scopes, client)
        except ScopeError as exc:
            return _scope_error(exc)
        except Exception:  # noqa: BLE001 -- dispatch wirft nie (siehe Docstring)
            logger.exception("Oszilloskop-Anfrage %s %s gescheitert", method, path)
            return _error(500, "internal_error")

    if method == "POST":
        return _dispatch_post(path, header_get, snapshot, body, executor, client)

    denied = check_auth(snapshot, header_get, query)
    if denied is not None:
        return denied

    if path == "/":
        return _index(snapshot, app_info)
    if path == "/display":
        return _display(snapshot, query)
    if path == f"{API_PREFIX}/status":
        return _status(snapshot, app_info)
    if path == f"{API_PREFIX}/tiles":
        return _tiles(snapshot)
    if path == f"{API_PREFIX}/values":
        return _values(snapshot, query)
    if path.startswith(f"{API_PREFIX}/tiles/"):
        device_id = path[len(f"{API_PREFIX}/tiles/"):]
        if "/" in device_id:
            return _error(404, "not_found")
        return _tile(snapshot, device_id)
    return _error(404, "not_found")
