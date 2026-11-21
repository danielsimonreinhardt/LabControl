"""Oszilloskop-Ablaufschritte "Scharf schalten" und "Auf Erfassung warten" (1.0.1, PI-72 a).

Beide gibt es fuer das PicoScope (Geraeteart "picoscope") und die LAN-Oszilloskope ("scpiscope": UNI-T MSO1000HD,
R&S RTB2000). Sie laufen
nicht ueber den DeviceWorker, sondern ueber den gemeinsamen Erfassungskern im ScopeService (scope_service.arm,
derselbe wie fuer REST/MCP, PI-73 a):

- SCOPE_ARM stellt ein und schaltet scharf; der Schritt ist fertig, sobald das Geraet auf den Trigger wartet,
  die folgenden Schritte laufen weiter (z. B. Generator aus, 12 V ein).
- SCOPE_WAIT wartet hoechstens params["timeout_s"] auf den Trigger der scharfen Erfassung desselben
  Oszilloskops. Kein Trigger = Schritt FAIL mit Meldung. Danach liefert er einen Kennwert (wie eine
  Lese-Aktion: Pruefung und Variable), Kurve als CSV und Bild in den Bericht.

Parameter in TestStep.params (Format 5), Einheiten wie scope_api (V, s, %):
  SCOPE_ARM:  channel, range_v, probe, coupling, edge, level_v, duration_s, pre_trigger_pct
  SCOPE_WAIT: timeout_s, measurement, channel ("" = Kanal der scharfen Erfassung)
"""
from __future__ import annotations

from i18n import tr
from scope_api.analysis import MEASUREMENTS
from scope_api.base import MAX_ARM_TIMEOUT_S

SCOPE_ARM = "SCOPE_ARM"
SCOPE_WAIT = "SCOPE_WAIT"
# Aktionscode -> deutscher Basis-Anzeigename (Uebersetzungsschluessel)
SCOPE_ACTIONS = {
    SCOPE_ARM: "Scharf schalten",
    SCOPE_WAIT: "Auf Erfassung warten",
}
SCOPE_KINDS = ("picoscope", "scpiscope")
CHANNELS = {"picoscope": ("A", "B"), "scpiscope": ("CH1", "CH2", "CH3", "CH4")}
EDGES = ("rising", "falling")
EDGE_LABELS = {"rising": "steigend", "falling": "fallend"}
EDGE_SYMBOLS = {"rising": "↑", "falling": "↓"}
COUPLINGS = ("DC", "AC")
# Hoechstzeit eines Warteschritts (der Lauf haelt die Erfassung ohnehin nur bis MAX_ARM_TIMEOUT_S scharf)
MAX_WAIT_S = MAX_ARM_TIMEOUT_S

ARM_DEFAULTS = {"channel": "", "range_v": 5.0, "probe": 1.0, "coupling": "DC", "edge": "rising", "level_v": 1.0,
                "duration_s": 0.01, "pre_trigger_pct": 10.0}
WAIT_DEFAULTS = {"timeout_s": 10.0, "measurement": "vmax", "channel": ""}

MEASUREMENT_LABELS = {
    "vmax": "Maximum", "vmin": "Minimum", "vpp": "Spitze-Spitze", "vmean": "Mittelwert", "vrms": "Effektivwert",
    "vbase": "Grundpegel", "vtop": "Dachpegel", "amplitude": "Amplitude", "frequency": "Frequenz",
    "period": "Periode", "duty": "Tastverhältnis", "rise_time": "Anstiegszeit", "fall_time": "Abfallzeit",
    "overshoot": "Überschwingen", "undershoot": "Unterschwingen", "edge_count": "Flankenzahl",
}
CHECK_FIELD = "scope_value"


def is_scope_step(step) -> bool:
    return getattr(step, "step_type", "action") == "action" and step.action in SCOPE_ACTIONS


def channels_for(kind: str) -> tuple[str, ...]:
    return CHANNELS.get(kind, CHANNELS["picoscope"])


def _number(value, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    return float(value)


def clean_params(action: str, params: dict | None, kind: str = "picoscope") -> dict:
    """Nur die Parameter der Aktion, mit Vorgaben aufgefuellt (andere Schluessel fallen weg, "refs" bleibt)."""
    params = dict(params or {})
    if action == SCOPE_ARM:
        result = dict(ARM_DEFAULTS)
        for key in ("range_v", "probe", "level_v", "duration_s", "pre_trigger_pct"):
            result[key] = _number(params.get(key), ARM_DEFAULTS[key])
        channel = str(params.get("channel") or "").upper()
        result["channel"] = channel if channel in channels_for(kind) else channels_for(kind)[0]
        result["coupling"] = str(params.get("coupling", "DC")).upper() if str(params.get("coupling", "DC")).upper() \
            in COUPLINGS else "DC"
        result["edge"] = str(params.get("edge", "rising")).lower() if str(params.get("edge", "rising")).lower() \
            in EDGES else "rising"
        result["pre_trigger_pct"] = min(100.0, max(0.0, result["pre_trigger_pct"]))
    elif action == SCOPE_WAIT:
        result = dict(WAIT_DEFAULTS)
        result["timeout_s"] = min(MAX_WAIT_S, max(0.0, _number(params.get("timeout_s"), WAIT_DEFAULTS["timeout_s"])))
        measurement = str(params.get("measurement") or WAIT_DEFAULTS["measurement"])
        result["measurement"] = measurement if measurement in MEASUREMENTS else WAIT_DEFAULTS["measurement"]
        channel = str(params.get("channel") or "").upper()
        result["channel"] = channel if channel in channels_for(kind) else ""
    else:
        return params
    if params.get("refs"):
        result["refs"] = params["refs"]
    return result


def arm_body(params: dict, kind: str) -> dict:
    """Anfrage an scope_service.arm (wie acquire): ein Kanal = Trigger-Quelle, Einzelerfassung, scharf bis
    zum Warteschritt bzw. Laufende (hoechstens MAX_ARM_TIMEOUT_S); nur einfache Kennwerte (den gewaehlten
    berechnet der Warteschritt gezielt nach, scope_service.measure); keine Huellkurve (bleibt im Lauf)."""
    p = clean_params(SCOPE_ARM, params, kind)
    return {
        "channels": [{"name": p["channel"], "range_v": p["range_v"], "probe": p["probe"], "coupling": p["coupling"]}],
        "timing": {"duration_s": p["duration_s"], "pre_trigger_pct": p["pre_trigger_pct"]},
        "trigger": {"mode": "single", "source": p["channel"], "level_v": p["level_v"], "edge": p["edge"],
                    "timeout_s": MAX_ARM_TIMEOUT_S},
        "measurements": ["vmin", "vmax", "vpp"],
        "envelope_points": 0,
    }


def unit(params: dict | None) -> str:
    """Einheit des Kennwerts eines Warteschritts (V, s, Hz, % oder leer)."""
    return MEASUREMENTS.get(str((params or {}).get("measurement") or WAIT_DEFAULTS["measurement"]), "")


def measurement_label(name: str) -> str:
    return tr(MEASUREMENT_LABELS.get(name, name))


def measured_value(report: dict, params: dict, fallback_channel: str,
                   kind: str = "picoscope") -> tuple[float | None, str, str]:
    """(Wert, Kanal, Grund falls None) des gewaehlten Kennwerts aus einem analysis.report; ohne eigenen Kanal
    gilt fallback_channel (Kanal der scharfen Erfassung)."""
    p = clean_params(SCOPE_WAIT, params, kind)
    channels = report.get("channels") or {}
    channel = p["channel"] or fallback_channel or next(iter(channels), "")
    entry = channels.get(channel)
    if entry is None:
        return None, channel, tr("Kanal {channel} nicht erfasst", channel=channel)
    value = entry.get(p["measurement"])
    if value is None:
        return None, channel, str((entry.get("unavailable") or {}).get(p["measurement"], "")) or tr("nicht bestimmbar")
    return float(value), channel, ""


def summary(action: str, params: dict | None, kind: str = "picoscope") -> str:
    """Kurztext fuer Editorzeile, Ausfuehren-Ansicht, Baustein-Vorschau und Bericht."""
    if action == SCOPE_ARM:
        p = clean_params(action, params, kind)
        probe = f", {p['probe']:g}:1" if p["probe"] != 1.0 else ""
        return (f"{p['channel']} ±{p['range_v']:g} V{probe}, {EDGE_SYMBOLS[p['edge']]} {p['level_v']:g} V, "
                f"{_si(p['duration_s'], 's')}, " + tr("{pct:g} % vor", pct=p["pre_trigger_pct"]))
    if action == SCOPE_WAIT:
        p = clean_params(action, params, kind)
        channel = f" {p['channel']}" if p["channel"] else ""
        return tr("≤ {seconds:g} s, {measurement}{channel}", seconds=p["timeout_s"],
                  measurement=measurement_label(p["measurement"]), channel=channel)
    return "–"


def _si(value: float, unit_text: str) -> str:
    for factor, prefix in ((1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n")):
        if abs(value) >= factor or factor == 1e-9:
            return f"{value / factor:g} {prefix}{unit_text}"
    return f"{value:g} {unit_text}"
