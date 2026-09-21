"""MCP-Server fuer LabControl: gibt einem KI-Assistenten (z.B. Claude Code)
Werkzeuge, um freigegebene Messgeraete zu lesen und zu steuern.

Laeuft als EIGENER Prozess ausserhalb der LabControl-.exe und spricht deren
Netzwerk-Schnittstelle an (siehe lab_gui/share_api.py). Alle Sicherheits-
entscheidungen fallen dort, nicht hier: dieser Server ist nur ein Uebersetzer
von Werkzeugaufrufen in HTTP-Anfragen. Er kann nichts, was die HTTP-Schnittstelle
nicht ohnehin erlaubt -- und diese verlangt den Token, die Freigabe "Steuern"
je Geraet und den Hauptschalter "Fernsteuerung aktiv" in LabControl (Zugriffe vom
selben Rechner brauchen ihn nicht, solange die Ausnahme dafuer angehakt ist).

Konfiguration ueber Umgebungsvariablen:
  LABCONTROL_URL    Basisadresse, Standard http://127.0.0.1:8420
  LABCONTROL_TOKEN  Zugangstoken (LabControl -> Einstellungen -> Netzwerk)

Nur Standardbibliothek plus das Paket "mcp" (siehe requirements.txt).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

BASE_URL = os.environ.get("LABCONTROL_URL", "http://127.0.0.1:8420").rstrip("/")
TOKEN = os.environ.get("LABCONTROL_TOKEN", "")

# Muss ueber der Wartezeit des Servers liegen (share_remote.ALL_OFF_TIMEOUT_S =
# 15 s), sonst bricht dieser Client vor der Antwort des Geraets ab.
TIMEOUT_S = 25.0

# Was der Assistent tun kann, wenn eine Anfrage abgewiesen wird. Die Codes sind
# die "error"-Felder der Antworten von lab_gui/share_api.py.
HINTS = {
    "unauthorized": "Der Token stimmt nicht. Aktuellen Token in LabControl unter Einstellungen -> "
                    "Netzwerk ablesen und LABCONTROL_TOKEN in der MCP-Konfiguration anpassen.",
    "token_not_configured": "In LabControl ist noch kein Token erzeugt. Freigabe aktivieren oder "
                            "unter Einstellungen -> Netzwerk 'Neu erzeugen' druecken.",
    "remote_control_inactive": "Der Hauptschalter 'Fernsteuerung aktiv' (Einstellungen -> Netzwerk) ist "
                               "aus oder abgelaufen, und die Ausnahme 'Zugriffe von diesem PC brauchen den "
                               "Hauptschalter nicht' ist ebenfalls aus (oder LabControl laeuft auf einem "
                               "anderen Rechner). NUR DER NUTZER kann das aendern -- bitte darum bitten, "
                               "nicht versuchen zu umgehen.",
    "control_not_permitted": "Fuer dieses Geraet ist 'Steuern' in LabControl nicht angehakt.",
    "unknown_tile": "Unbekanntes oder nicht freigegebenes Geraet. list_devices zeigt, was freigegeben ist.",
    "locked": "Ein Testablauf laeuft oder die Sicherheitsabschaltung hat ausgeloest. Steuern ist gesperrt; "
              "lesen geht weiter. Nur 'all_off' bleibt moeglich.",
    "device_offline": "Das Geraet ist gerade nicht verbunden (USB/Kabel?).",
    "device_error": "Das Geraet hat den Befehl abgelehnt oder ist nicht erreichbar (siehe message).",
    "exceeds_safety_limit": "Der Sollwert liegt ueber dem in LabControl aktiven Sicherheits-Grenzwert dieses "
                            "Geraets. Kleineren Wert waehlen; den Grenzwert aendert nur der Nutzer.",
    "value_out_of_range": "Wert ausserhalb des zulaessigen Bereichs (min/max stehen in der Antwort).",
    "rate_limited": "Zu viele Befehle in kurzer Zeit. Kurz warten.",
    "busy": "Es sind bereits zwei Befehle unterwegs. Kurz warten.",
    "all_off_incomplete": "Nicht alle Geraete liessen sich abschalten (Liste 'failures'). Nutzer informieren.",
}

INSTRUCTIONS = """\
Steuert reale Laborgeraete (Netzteile, elektronische Last, microHIL) ueber LabControl.
Das ist Hardware: ein falscher Befehl kann Prueflinge beschaedigen.

Vorgehen:
- Zuerst get_status und list_devices lesen. Steuern geht nur, wenn 'remote_control.effective' true
  ist UND das Geraet control_available=true meldet. ('effective' ist wahr, wenn der Hauptschalter an
  ist ODER dieser Rechner ohne Hauptschalter steuern darf.) Ist es falsch, den Nutzer bitten, den
  Hauptschalter einzuschalten -- nicht umgehen.
- Nur das tun, worum der Nutzer gebeten hat. Ausgaenge nicht von dir aus einschalten.
- Nach jedem Schreiben den Zustand mit read_device gegenpruefen (Messwert, nicht nur 'ok').
- Bei Auffaelligkeiten (unerwartete Werte, Fehlermeldungen) sofort all_off und den Nutzer informieren.
- Netzteil HCS-34xx: 'Ausgang AUS' ist nur Strom = 0 A; PSU_CURR > 0 schaltet den Ausgang wieder EIN.
"""

server = MCPServer("labcontrol", instructions=INSTRUCTIONS)


def _request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(BASE_URL + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if TOKEN:
        request.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, _parse(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _parse(exc.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise ToolError(
            f"LabControl unter {BASE_URL} nicht erreichbar ({exc}). Laeuft die App, ist die "
            f"Netzwerk-Freigabe aktiviert und stimmen Adresse und Port?"
        ) from exc


def _parse(raw: bytes) -> dict:
    try:
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    except (UnicodeDecodeError, ValueError):
        return {"raw": raw.decode("utf-8", "replace")[:500]}


def _fail(status: int, body: dict) -> ToolError:
    """Uebersetzt eine abgewiesene Anfrage in einen Werkzeugfehler mit Hinweis."""
    code = body.get("error", f"http_{status}")
    details = {k: v for k, v in body.items() if k not in ("v", "ok", "error")}
    text = f"{code} (HTTP {status})"
    if details:
        text += f": {json.dumps(details, ensure_ascii=False)}"
    if code in HINTS:
        text += f"\nHinweis: {HINTS[code]}"
    return ToolError(text)


def _checked(method: str, path: str, payload: dict | None = None) -> dict:
    status, body = _request(method, path, payload)
    if status in (200, 202):
        return body
    raise _fail(status, body)


READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)


@server.tool(annotations=READ_ONLY)
def get_status() -> dict:
    """Zustand von LabControl: Version, Sperrzustand ('lock': free / test_running /
    safety_tripped), Sicherheits-Watchdog ('safety') und ob die Fernsteuerung fuer DICH gerade freigegeben
    ist ('remote_control.effective'; 'active'/'remaining_s' betreffen den Hauptschalter, 'local' sagt, ob
    der Aufruf vom selben Rechner kommt). Zuerst aufrufen."""
    return _checked("GET", "/api/v1/status")


@server.tool(annotations=READ_ONLY)
def list_devices() -> dict:
    """Alle freigegebenen Geraete mit aktuellen Messwerten. Je Geraet: 'fields' (Messwerte mit
    Einheit), 'online'/'stale', und -- falls fuer Steuern freigegeben -- 'actions' (erlaubte Aktionen mit
    Wertebereich und Kanaelen) sowie 'control_available' bzw. der Sperrgrund in 'control_blocked'."""
    return _checked("GET", "/api/v1/values")


@server.tool(annotations=READ_ONLY)
def read_device(device_id: str) -> dict:
    """Aktuelle Messwerte EINES Geraets (z.B. 'psu:COM6', 'load:48DC385B3435'). Nach jedem Schreiben
    aufrufen, um die Wirkung zu pruefen. 'age_s' ist das Alter des Werts in Sekunden."""
    return _checked("GET", "/api/v1/tiles/" + urllib.parse.quote(device_id, safe=":"))


@server.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False))
def control_device(device_id: str, action: str, value: float | None = None,
                   channel: int | None = None) -> dict:
    """Fuehrt EINE Aktion an einem Geraet aus. Die erlaubten 'action'-Codes samt Wertebereich und
    Kanalzahl stehen je Geraet in list_devices ('actions'). Beispiele:
      Netzteil  PSU_VOLT value=5.0 (V) | PSU_CURR value=0.5 (A) | PSU_OUT_ON | PSU_OUT_OFF
      Last      CURR value=2.0 (A) | VOLT (V) | RES (Ohm) | POW (W) | OUT_ON | OUT_OFF
      microHIL  HIL_OUT_ON/HIL_OUT_OFF channel=1..8 | HIL_RELAY_ON/HIL_RELAY_OFF channel=1..4 |
                HIL_AOUT channel=1..2 value=mV
    'value' nur bei Aktionen mit Wert, 'channel' nur beim microHIL. Bei Erfolg kommt 'ok': true zurueck
    (das Geraet hat bestaetigt); mit 'status': 'pending' ist der Befehl unterwegs, sein Ausgang aber
    unbekannt -- dann mit read_device nachsehen. Fehler kommen als Werkzeugfehler mit Hinweis."""
    payload: dict = {"action": action}
    if value is not None:
        payload["value"] = value
    if channel is not None:
        payload["channel"] = channel
    return _checked("POST", "/api/v1/tiles/" + urllib.parse.quote(device_id, safe=":") + "/actions", payload)


@server.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def all_off() -> dict:
    """NOTAUS: schaltet ALLE Ausgaenge aller Geraete ab (Last-Eingang, Netzteil-Strom auf 0 A,
    microHIL-Ausgaenge). Geht immer -- auch bei ausgeschaltetem Hauptschalter, laufendem Testablauf und
    nach einer Sicherheitsabschaltung. Bei Zweifeln oder Auffaelligkeiten sofort aufrufen."""
    return _checked("POST", "/api/v1/all-off", {})


if __name__ == "__main__":
    server.run("stdio")
