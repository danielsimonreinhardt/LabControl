"""Katalog der ueber die Netzwerk-Schnittstelle erlaubten Schreib-Aktionen
(Fernsteuerung, siehe share_api.py).

Bewusst Qt-frei, aus demselben Grund wie field_catalog.py: share_api laeuft im
HTTP-Server-Thread und darf kein Widget-Modul importieren. Die Aktionscodes
und Wertebereiche entsprechen testcase_model.ACTION_VALUE_RANGE (dort dieselbe
Quelle fuer den Testeditor) -- testcase_model importiert aber i18n und damit
Qt und kommt hier deshalb nicht in Frage. Gegen Auseinanderlaufen sichert
tools/check_network_share.py ab: der Abschnitt "remote" vergleicht beide
Kataloge Eintrag fuer Eintrag.

Bewusst eine ALLOWLIST und keine Weiterleitung von device_worker.
_dispatch_action: dort gibt es Aktionen, die per Netzwerk nichts verloren haben
(CAN-Frames senden, PicoScope-Messungen, Lese-Aktionen des Testablaufs).
Nicht enthalten und damit von aussen nicht ausloesbar:
  * CAN_SEND (beliebige Frames auf einem Fahrzeug-/Prueflingsbus)
  * PICO_* (Oszilloskop-Sitzungen, exklusives Handle)
  * ARB/PSU_ARB (Arbitraersignal: laeuft als Folge von Sollwerten im
    TestRunner, nicht als Einzelaktion)
  * HIL_*_READ (Werte liefert ohnehin GET /api/v1/tiles/{id})

Netzteil-Besonderheit (siehe hcs34xx/README.md und BUGS.md #1b): das HCS-34xx
kennt kein echtes Ausgang-AUS, "AUS" ist ein Stromsollwert von 0 A. Umgekehrt
schaltet PSU_CURR mit einem Wert > 0 den Ausgang WIEDER EIN, auch wenn er
zuvor per PSU_OUT_OFF "ausgeschaltet" wurde. Die Beschreibung der Aktionen
weist im API darauf hin.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from jds66xx.driver import MAX_AMPLITUDE_V, MAX_FREQUENCY_HZ, MAX_OFFSET_V, MIN_FREQUENCY_HZ
from microhil.driver import AOUT_COUNT, AOUT_MAX_MV, OUT_COUNT, RELAY_COUNT

# Geraetearten, die ueberhaupt fernsteuerbar sind. CAN und Oszilloskop bleiben
# bewusst draussen (siehe Modul-Docstring).
CONTROL_KINDS = ("load", "psu", "hil", "fg")


@dataclass(frozen=True)
class RemoteAction:
    code: str
    label: str            # deutscher Basisname, sprachneutral wie field_catalog
    unit: str = ""
    minimum: float = 0.0
    maximum: float = 0.0
    needs_value: bool = False
    channels: int = 0     # 0 = kein Kanal; sonst gueltig sind 1..channels
    limit_field: str = ""  # zugehoeriger Sicherheits-Grenzwert (safety.py)
    # Nennwert des Geraets (GMAX), der das statische Maximum ERSETZT, sobald bekannt:
    # "max_voltage" / "max_current". Das statische Maximum ist nur der Rahmen fuer
    # ein Geraet, dessen Nennwerte (noch) nicht gemeldet wurden.
    rating_field: str = ""
    note: str = ""


def _a(*args, **kwargs) -> RemoteAction:
    return RemoteAction(*args, **kwargs)


REMOTE_ACTIONS: dict[str, dict[str, RemoteAction]] = {
    "load": {a.code: a for a in (
        _a("CURR", "Konstantstrom (CC) setzen", "A", 0, 40, True, limit_field="max_current"),
        _a("VOLT", "Konstantspannung (CV) setzen", "V", 0, 150, True, limit_field="max_voltage"),
        _a("RES", "Konstantwiderstand (CR) setzen", "Ohm", 0, 7500, True),
        _a("POW", "Konstantleistung (CW) setzen", "W", 0, 300, True, limit_field="max_power"),
        _a("OUT_ON", "Ausgang EIN"),
        _a("OUT_OFF", "Ausgang AUS"),
    )},
    "psu": {a.code: a for a in (
        _a("PSU_VOLT", "Spannung setzen", "V", 1, 60, True, limit_field="max_voltage",
           rating_field="max_voltage"),
        _a("PSU_CURR", "Strom setzen", "A", 0, 40, True, limit_field="max_current",
           rating_field="max_current",
           note="0 A schaltet den Ausgang aus, ein Wert > 0 A wieder ein "
                "(das HCS-34xx hat kein echtes Ausgang-AUS)"),
        _a("PSU_OUT_ON", "Ausgang EIN",
           note="hebt den Strom auf mindestens 0,1 A, die Spannung bleibt"),
        _a("PSU_OUT_OFF", "Ausgang AUS", note="setzt den Strom auf 0 A"),
    )},
    "hil": {a.code: a for a in (
        _a("HIL_OUT_ON", "Digitalausgang EIN", channels=OUT_COUNT),
        _a("HIL_OUT_OFF", "Digitalausgang AUS", channels=OUT_COUNT),
        _a("HIL_RELAY_ON", "Relais EIN", channels=RELAY_COUNT),
        _a("HIL_RELAY_OFF", "Relais AUS", channels=RELAY_COUNT),
        _a("HIL_AOUT", "Analogausgang setzen", "mV", 0, AOUT_MAX_MV, True, channels=AOUT_COUNT),
    )},
    "fg": {a.code: a for a in (
        _a("FG_FREQ", "Frequenz setzen", "Hz", MIN_FREQUENCY_HZ, MAX_FREQUENCY_HZ, True, channels=2),
        _a("FG_AMPL", "Amplitude setzen (Spitze-Spitze)", "V", 0, MAX_AMPLITUDE_V, True, channels=2),
        _a("FG_OFFS", "Offset setzen", "V", -MAX_OFFSET_V, MAX_OFFSET_V, True, channels=2),
        _a("FG_DUTY", "Tastverhältnis setzen", "%", 0, 100, True, channels=2),
        _a("FG_PHASE", "Phase setzen", "°", 0, 360, True, channels=2,
           note="gilt für Kanal 2 relativ zu Kanal 1, der Kanal wird ignoriert"),
        _a("FG_WAVE_SINE", "Wellenform Sinus", channels=2),
        _a("FG_WAVE_SQUARE", "Wellenform Rechteck", channels=2),
        _a("FG_WAVE_PULSE", "Wellenform Puls", channels=2),
        _a("FG_WAVE_TRIANGLE", "Wellenform Dreieck", channels=2),
        _a("FG_WAVE_DC", "Wellenform DC", channels=2),
        _a("FG_OUT_ON", "Ausgang EIN", channels=2),
        _a("FG_OUT_OFF", "Ausgang AUS", channels=2),
    )},
}


def effective_range(definition: RemoteAction, ratings: dict | None) -> tuple[float, float]:
    """(Minimum, Maximum) einer Aktion fuer DIESES Geraet.

    Ist der Nennwert des Geraets bekannt (`ratings` aus LiveState, GMAX), gilt er
    statt des statischen Rahmens -- nach oben UND nach unten: ein 16-V-Netzteil
    nimmt keine 20 V an, ein 30-A-Netzteil aber sehr wohl 25 A.
    """
    maximum = definition.maximum
    rated = (ratings or {}).get(definition.rating_field) if definition.rating_field else None
    if isinstance(rated, (int, float)) and not isinstance(rated, bool) and rated > definition.minimum:
        maximum = float(rated)
    return definition.minimum, maximum


def describe(kind: str, ratings: dict | None = None) -> list[dict]:
    """Aktionsliste einer Geraeteart fuer die JSON-Antwort (Feld "actions")."""
    result = []
    for action in REMOTE_ACTIONS.get(kind, {}).values():
        item: dict = {"action": action.code, "label": action.label}
        if action.needs_value:
            low, high = effective_range(action, ratings)
            item.update({"value": {"unit": action.unit, "min": low, "max": high}})
        if action.channels:
            item["channels"] = action.channels
        if action.note:
            item["note"] = action.note
        result.append(item)
    return result


def validate(kind: str, action: str, value, channel,
             ratings: dict | None = None) -> tuple[RemoteAction | None, str, dict]:
    """Prueft eine angeforderte Aktion. Liefert (Aktion, Fehlercode, Details);
    bei Erfolg (Aktion, "", {}).

    Fehlercodes: unknown_action, value_required, invalid_value,
    value_out_of_range, channel_required, invalid_channel,
    channel_out_of_range. `value`/`channel` sind die rohen JSON-Werte
    (None = nicht angegeben). `ratings`: Nennwerte des Geraets (GMAX), falls bekannt.
    """
    definition = REMOTE_ACTIONS.get(kind, {}).get(action)
    if definition is None:
        return None, "unknown_action", {"allowed": list(REMOTE_ACTIONS.get(kind, {}))}
    if definition.needs_value:
        if value is None:
            return None, "value_required", {"unit": definition.unit}
        # bool ist in Python ein int -- true/false ist hier kein Zahlenwert.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None, "invalid_value", {}
        low, high = effective_range(definition, ratings)
        if not low <= value <= high:
            return None, "value_out_of_range", {"min": low, "max": high, "unit": definition.unit}
    if definition.channels:
        if channel is None:
            return None, "channel_required", {"channels": definition.channels}
        if isinstance(channel, bool) or not isinstance(channel, int):
            return None, "invalid_channel", {}
        if not 1 <= channel <= definition.channels:
            return None, "channel_out_of_range", {"channels": definition.channels}
    return definition, "", {}


def limit_violation(definition: RemoteAction, value: float, limits: dict) -> tuple[str, float] | None:
    """Ueberschreitet der Sollwert einen AKTIVEN Sicherheits-Grenzwert dieses
    Geraets? Liefert (Feldname, Grenzwert) oder None.

    Grund: der Watchdog (safety.py) loest erst bei einer MESSUNG oberhalb des
    Grenzwerts aus. Ein Sollwert darueber wuerde also erst angelegt und dann
    abgeschaltet -- besser gar nicht erst anlegen. `limits` ist die rohe Form
    aus Settings.safety_limits fuer EIN Geraet: {feld: {"enabled", "value"}}.
    Nicht aktivierte Grenzwerte (das ist der Default) zaehlen nicht.
    """
    if not definition.limit_field:
        return None
    entry = limits.get(definition.limit_field) if isinstance(limits, dict) else None
    if not isinstance(entry, dict) or not entry.get("enabled"):
        return None
    try:
        limit = float(entry["value"])
    except (KeyError, TypeError, ValueError):
        return None
    if value > limit:
        return definition.limit_field, limit
    return None
