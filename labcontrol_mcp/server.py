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

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent, ToolAnnotations

BASE_URL = os.environ.get("LABCONTROL_URL", "http://127.0.0.1:8420").rstrip("/")
TOKEN = os.environ.get("LABCONTROL_TOKEN", "")

# Muss ueber der Wartezeit des Servers liegen (share_remote.ALL_OFF_TIMEOUT_S =
# 15 s), sonst bricht dieser Client vor der Antwort des Geraets ab.
TIMEOUT_S = 25.0
# Oszilloskop-Erfassungen: Trigger-Timeout (max. 30 s) + Oeffnen (~4,5 s, dazu
# bis 2 s Pause nach dem letzten Schliessen) + Uebertragung.
SCOPE_TIMEOUT_S = 60.0

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
    # Oszilloskope
    "unknown_scope": "Unbekanntes oder nicht freigegebenes Oszilloskop. list_scopes zeigt, was freigegeben ist.",
    "measure_not_permitted": "Fuer dieses Oszilloskop ist 'Messen' in LabControl (Einstellungen -> Netzwerk) "
                             "nicht angehakt. Nur der Nutzer kann das aendern.",
    "scope_in_test_run": "Ein Testablauf nutzt das Oszilloskop gerade. Nach dessen Ende erneut versuchen.",
    "scope_busy": "Das Oszilloskop ist gerade beschaeftigt (andere Erfassung oder LabControl selbst). "
                  "Ein paar Sekunden warten.",
    "scope_busy_external": "Das Oszilloskop ist angeschlossen, aber von einem anderen Programm belegt "
                           "(vermutlich PicoScope 7). Den Nutzer bitten, es zu schliessen.",
    "invalid_request": "Anfrage ungueltig (siehe message). get_scope_capabilities nennt, was geht.",
    "setting_not_supported": "Das Geraet kann diese Einstellung nicht; die Antwort nennt den naechsten "
                             "erlaubten Wert (z.B. max_range_v, min_interval_s).",
    "level_out_of_range": "Trigger-Pegel liegt ausserhalb des Messbereichs der Quelle: Pegel oder Bereich anpassen.",
    "duration_too_long": "Die Dauer passt nicht in den Speicher; max_duration_s nennt das Maximum.",
    "trigger_timeout": "Kein Trigger in der Wartezeit. Pegel/Flanke/Quelle pruefen, mit trigger_mode='auto' "
                       "nachsehen, was anliegt, oder den Nutzer nach dem Signal fragen.",
    "unknown_capture": "Erfassung nicht (mehr) im Speicher (es werden die letzten 50 gehalten). "
                       "Die CSV-Datei (csv_path) kann noch vorhanden sein.",
    "unknown_measurement": "Unbekannter Kennwert; 'allowed' listet die moeglichen.",
}

INSTRUCTIONS = """\
Steuert reale Laborgeraete (Netzteile, elektronische Last, microHIL, Funktionsgenerator) ueber
LabControl und misst mit Oszilloskopen. Das ist Hardware: ein falscher Befehl kann Prueflinge
beschaedigen.

Vorgehen:
- Zuerst get_status und list_devices lesen. Steuern geht nur, wenn 'remote_control.effective' true
  ist UND das Geraet control_available=true meldet. ('effective' ist wahr, wenn der Hauptschalter an
  ist ODER dieser Rechner ohne Hauptschalter steuern darf.) Ist es falsch, den Nutzer bitten, den
  Hauptschalter einzuschalten -- nicht umgehen.
- Nur das tun, worum der Nutzer gebeten hat. Ausgaenge nicht von dir aus einschalten.
- Nach jedem Schreiben den Zustand mit read_device gegenpruefen (Messwert, nicht nur 'ok').
- Bei Auffaelligkeiten (unerwartete Werte, Fehlermeldungen) sofort all_off und den Nutzer informieren.
- Netzteil HCS-34xx: 'Ausgang AUS' ist nur Strom = 0 A; PSU_CURR > 0 schaltet den Ausgang wieder EIN.

Oszilloskope (list_scopes, get_scope_capabilities, acquire, get_capture, measure, plot_capture,
release_scope):
- Messen schaltet nichts, braucht aber das Haekchen 'Messen' je Oszilloskop (nicht den Hauptschalter).
- Vor der ersten Messung an einem neuen Messpunkt den Nutzer nach Tastkopf (1:1/10:1) und erwartetem
  Pegel fragen und den Bereich (range_v) passend waehlen -- das Oszilloskop kann die Verdrahtung nicht pruefen.
- Alles in V, s, Hz. acquire liefert Kennwerte, Warnungen und eine Huellkurve, keine Rohdaten; Details
  ueber get_capture (Ausschnitt) oder die CSV-Datei (csv_path), Neuauswertung ohne neue Messung ueber measure.
  Mit plot_capture die Kurve als Bild ansehen, auch gezoomt -- vor allem bei unerwarteten Werten, Stoerungen,
  Schwingen oder Einschwingvorgaengen, die Kennwerte allein nicht zeigen.
- 'overrange' oder Warnungen ernst nehmen (Bereich erhoehen). Ein Kennwert None hat seinen Grund in 'unavailable'.
- Die Verbindung bleibt 60 s nach der letzten Erfassung offen (PicoScope 7 kann das Geraet dann nicht
  oeffnen). Nach einer Messreihe release_scope aufrufen.
"""

server = MCPServer("labcontrol", instructions=INSTRUCTIONS)


def _request(method: str, path: str, payload: dict | None = None,
             timeout: float = TIMEOUT_S) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(BASE_URL + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if TOKEN:
        request.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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


def _fetch_png(path: str) -> bytes:
    """GET auf eine Bild-Route. Fehler kommen wie sonst als JSON und werden zu ToolError."""
    request = urllib.request.Request(BASE_URL + path, method="GET")
    if TOKEN:
        request.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data = response.read()
            if response.headers.get_content_type() != "image/png":
                raise ToolError(f"Unerwartete Antwort ({response.headers.get_content_type()}) statt PNG.")
            return data
    except urllib.error.HTTPError as exc:
        raise _fail(exc.code, _parse(exc.read())) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise ToolError(
            f"LabControl unter {BASE_URL} nicht erreichbar ({exc}). Laeuft die App, ist die "
            f"Netzwerk-Freigabe aktiviert und stimmen Adresse und Port?"
        ) from exc


def _checked(method: str, path: str, payload: dict | None = None, timeout: float = TIMEOUT_S) -> dict:
    status, body = _request(method, path, payload, timeout)
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
      Funktionsgenerator  FG_FREQ (Hz) | FG_AMPL (V, Spitze-Spitze) | FG_OFFS (V) | FG_DUTY (%) |
                FG_WAVE_SINE/SQUARE/PULSE/TRIANGLE/DC | FG_OUT_ON/FG_OUT_OFF, jeweils channel=1..2
    'value' nur bei Aktionen mit Wert, 'channel' nur beim microHIL und Funktionsgenerator. Bei Erfolg kommt 'ok': true zurueck
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


# -- Oszilloskope --------------------------------------------------------------


def _scope_path(scope_id: str, suffix: str = "") -> str:
    return "/api/v1/scopes/" + urllib.parse.quote(scope_id, safe=":") + suffix


@server.tool(annotations=READ_ONLY)
def list_scopes() -> dict:
    """Freigegebene Oszilloskope mit Zustand: 'measure' (fuer Erfassungen freigegeben),
    'measure_available'/'measure_blocked' (geht es JETZT, sonst Grund), 'state' (idle / connected /
    acquiring / test / offline), Modell und Seriennummer. 'disconnect_in_s': Restzeit bis zum
    automatischen Trennen einer offenen Verbindung."""
    return _checked("GET", "/api/v1/scopes")


@server.tool(annotations=READ_ONLY)
def get_scope_capabilities(scope_id: str) -> dict:
    """Was ein Oszilloskop kann: Kanaele mit Bereichen (+-V Vollausschlag am Eingang, ohne Tastkopf)
    und Kopplungen, max. Abtastrate, Speichertiefe, Bandbreite, Aufloesung, Triggerarten/-quellen und
    Hinweise ('notes'). Vor der ersten Erfassung lesen und die Einstellungen danach waehlen."""
    return _checked("GET", _scope_path(scope_id, "/capabilities"))


@server.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
def acquire(scope_id: str, channels: list[dict], duration_s: float | None = None,
            sample_interval_s: float | None = None, samples: int | None = None,
            pre_trigger_pct: float = 0.0, trigger_mode: str = "auto", trigger_source: str | None = None,
            trigger_level_v: float = 0.0, trigger_edge: str = "rising", trigger_timeout_s: float = 1.0,
            measurements: list[str] | None = None, envelope_points: int = 200) -> dict:
    """Eine Erfassung mit vollstaendiger Einstellung (nichts bleibt von vorher stehen).

    channels: Liste von {"name": "A", "range_v": 5, "coupling": "DC"|"AC", "probe": 1|10}; range_v ist
      der Vollausschlag +-V an der Tastkopfspitze, gewaehlt wird der naechstgroessere Geraetebereich.
    Zeit: GENAU EINES von duration_s (feinste Aufloesung, die in den Speicher passt) oder
      sample_interval_s; optional samples. pre_trigger_pct: Anteil der Erfassung vor dem Trigger (t=0).
    Trigger: trigger_mode 'none' (freilaufend) | 'auto' (wartet trigger_timeout_s, erfasst dann trotzdem)
      | 'single' (nur mit Trigger, sonst Fehler trigger_timeout); Quelle = erster Kanal, falls nicht
      angegeben; Pegel in V an der Tastkopfspitze; Flanke 'rising'|'falling'.
    measurements: z.B. ["vpp","frequency","duty","rise_time"] (Standard: vmin, vmax, vpp, vmean, vrms,
      frequency, duty). Weitere: vbase, vtop, amplitude, period, fall_time, overshoot, undershoot, edge_count.
    Antwort: capture_id, triggered, tatsaechliche Einstellungen ('actual'), Kennwerte je Kanal mit
    'overrange' und Gruenden fuer fehlende Werte ('unavailable'), 'warnings', eine Min/Max-Huellkurve mit
    envelope_points Abschnitten (0 = keine) und csv_path mit allen Rohdaten. Dauert bei der ersten
    Erfassung ~5 s laenger (Verbinden)."""
    timing: dict = {"pre_trigger_pct": pre_trigger_pct}
    if duration_s is not None:
        timing["duration_s"] = duration_s
    if sample_interval_s is not None:
        timing["sample_interval_s"] = sample_interval_s
    if samples is not None:
        timing["samples"] = samples
    trigger: dict = {"mode": trigger_mode, "level_v": trigger_level_v, "edge": trigger_edge,
                     "timeout_s": trigger_timeout_s}
    if trigger_source is not None:
        trigger["source"] = trigger_source
    payload: dict = {"channels": channels, "timing": timing, "trigger": trigger,
                     "envelope_points": envelope_points}
    if measurements:
        payload["measurements"] = measurements
    return _checked("POST", _scope_path(scope_id, "/acquire"), payload, timeout=SCOPE_TIMEOUT_S)


@server.tool(annotations=READ_ONLY)
def get_capture(capture_id: str, channel: str | None = None, t_start_s: float | None = None,
                t_stop_s: float | None = None, max_points: int = 500, mode: str = "minmax") -> dict:
    """Ausschnitt einer Erfassung (Zeit t in s, t=0 ist der Trigger). Passt das Fenster in
    max_points (hoechstens 2000), kommen Rohwerte ('raw'); sonst verdichtet: mode 'minmax' (Min/Max je
    Abschnitt, Spitzen bleiben erhalten) oder 'mean'. Fuer vollstaendige Rohdaten die CSV-Datei
    (csv_path) direkt lesen."""
    query = {"max_points": max_points, "mode": mode}
    if channel:
        query["channel"] = channel
    if t_start_s is not None:
        query["t_start_s"] = t_start_s
    if t_stop_s is not None:
        query["t_stop_s"] = t_stop_s
    return _checked("GET", "/api/v1/captures/" + urllib.parse.quote(capture_id, safe="") + "?"
                    + urllib.parse.urlencode(query))


@server.tool(annotations=READ_ONLY)
def measure(capture_id: str, measurements: list[str] | None = None, channel: str | None = None,
            t_start_s: float | None = None, t_stop_s: float | None = None) -> dict:
    """Kennwerte einer gespeicherten Erfassung neu berechnen, optional nur fuer einen Kanal und ein
    Zeitfenster -- ohne neue Messung. Namen wie bei acquire."""
    payload: dict = {}
    if measurements:
        payload["measurements"] = measurements
    if channel:
        payload["channel"] = channel
    if t_start_s is not None:
        payload["t_start_s"] = t_start_s
    if t_stop_s is not None:
        payload["t_stop_s"] = t_stop_s
    return _checked("POST", "/api/v1/captures/" + urllib.parse.quote(capture_id, safe="") + "/measure", payload)


@server.tool(annotations=READ_ONLY)
def plot_capture(capture_id: str, channels: list[str] | None = None, t_start_s: float | None = None,
                 t_stop_s: float | None = None, width: int = 1000, height: int = 560) -> list[Image | TextContent]:
    """Eine gespeicherte Erfassung als Bild (PNG) ansehen -- wie ein Oszilloskop-Schirm: Kopfzeile mit
    Trigger und Abtastintervall, je Kanal eine Legende mit Bereich, min/max, Vss, Effektivwert und
    Frequenz, t = 0 am Trigger (senkrechte Strichlinie), Triggerpegel als waagerechte Strichlinie.
    Die Spannungsachse ist gemeinsam fuer alle Kanaele und auf die Daten skaliert. Je Pixelspalte
    werden Min und Max gezeichnet, kurze Spitzen bleiben sichtbar. Mit t_start_s/t_stop_s auf einen
    Ausschnitt zoomen (z.B. eine Flanke), mit channels auf einzelne Kanaele beschraenken.
    width 400..2000, height 250..1200 Pixel. Liest nur -- misst nicht neu."""
    query: dict = {"width": width, "height": height}
    if channels:
        query["channels"] = ",".join(channels)
    if t_start_s is not None:
        query["t_start_s"] = t_start_s
    if t_stop_s is not None:
        query["t_stop_s"] = t_stop_s
    png = _fetch_png("/api/v1/captures/" + urllib.parse.quote(capture_id, safe="") + "/plot?"
                     + urllib.parse.urlencode(query))
    window = ""
    if t_start_s is not None or t_stop_s is not None:
        window = f", Ausschnitt {t_start_s if t_start_s is not None else 'Anfang'} .. " \
                 f"{t_stop_s if t_stop_s is not None else 'Ende'} s"
    return [Image(data=png, format="png"),
            TextContent(type="text", text=f"Erfassung {capture_id}{window} ({len(png)} Byte PNG).")]


@server.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def release_scope(scope_id: str) -> dict:
    """Verbindung zum Oszilloskop sofort trennen (sonst nach 60 s ohne Erfassung automatisch), damit
    z.B. die PicoScope-7-App es wieder oeffnen kann. Gespeicherte Erfassungen bleiben erhalten."""
    return _checked("POST", _scope_path(scope_id, "/release"), {})


if __name__ == "__main__":
    server.run("stdio")
