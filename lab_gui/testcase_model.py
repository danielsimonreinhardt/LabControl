"""Datenmodell fuer Testablauf-Schritte (Testcase-Editor)."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

# Anzeigename -> interner Aktionscode, je Geraeteart ("load"/"psu")
LOAD_ACTIONS = {
    "Konstantstrom (CC)": "CURR",
    "Konstantspannung (CV)": "VOLT",
    "Konstantwiderstand (CR)": "RES",
    "Konstantleistung (CW)": "POW",
    "Ausgang EIN": "OUT_ON",
    "Ausgang AUS": "OUT_OFF",
    "Arbiträrsignal": "ARB",
}

PSU_ACTIONS = {
    "Spannung setzen": "PSU_VOLT",
    "Strom setzen": "PSU_CURR",
    "Ausgang EIN": "PSU_OUT_ON",
    "Ausgang AUS": "PSU_OUT_OFF",
    "Preset P1 abrufen": "PSU_P1",
    "Preset P2 abrufen": "PSU_P2",
    "Preset P3 abrufen": "PSU_P3",
    "Arbiträrsignal": "PSU_ARB",
}

# Arbiträrsignal-Aktionscode je Geraeteart -> Liste der Aktionscodes, die als
# Zielgroesse (das tatsaechlich modulierte Sollwert-Kommando) waehlbar sind.
# Schaltaktionen (Ausgang EIN/AUS, Presets) scheiden aus, da sie keinen
# Zahlenwert entgegennehmen.
ARB_ACTIONS = {"ARB", "PSU_ARB"}

ARB_TARGETS: dict[str, list[str]] = {
    "load": ["CURR", "VOLT", "RES", "POW"],
    "psu": ["PSU_VOLT", "PSU_CURR"],
}

DEVICE_ACTIONS = {
    "load": LOAD_ACTIONS,
    "psu": PSU_ACTIONS,
}

DEVICE_KIND_LABELS = {
    "load": "Last",
    "psu": "Netzteil",
}

# Alte Testablauf-Dateien speichern die Geraeteart noch unter dem Feldnamen
# "device" mit den frueheren deutschen Anzeigenamen als Wert.
_LEGACY_DEVICE_KIND = {"Last": "load", "Netzteil": "psu"}

# Aktionen, die keinen Zahlenwert benoetigen (Wert-Feld wird deaktiviert).
# PSU_OUT_ON braucht den Wert als Spannung (Strom wird automatisch auf
# mindestens 0.1A angehoben, siehe DeviceWorker._dispatch_action).
# Arbiträrsignal-Aktionen brauchen ebenfalls keinen Wert im normalen Feld --
# ihre Parameter (Signalform, Amplitude, ...) kommen aus dem Definieren-Dialog
# (siehe signal_dialog.py) und liegen in den arb_*-Feldern von TestStep.
VALUELESS_ACTIONS = {
    "OUT_ON", "OUT_OFF", "PSU_OUT_OFF", "PSU_P1", "PSU_P2", "PSU_P3",
    "ARB", "PSU_ARB",
}

# Anzeigename-Einheit/Min/Max fuer das Wert-Feld je Aktionscode. Die
# Netzteil-Spannungsgrenzen (1-60V) sind keine willkuerliche GUI-Beschraenkung,
# sondern spiegeln eine echte Geraete-Eigenschaft: Werte unter 1V werden vom
# HCS-34xx kommentarlos ignoriert (siehe hcs34xx/driver.py: MIN_VOLTAGE).
ACTION_VALUE_RANGE: dict[str, tuple[str, float, float]] = {
    "CURR": ("A", 0, 40),
    "VOLT": ("V", 0, 150),
    "RES": ("Ohm", 0, 7500),
    "POW": ("W", 0, 300),
    "SHORT": ("", 0, 0),
    "OUT_ON": ("", 0, 0),
    "OUT_OFF": ("", 0, 0),
    "PSU_VOLT": ("V", 1, 60),
    "PSU_CURR": ("A", 0, 10),
    "PSU_OUT_ON": ("V", 1, 60),
    "PSU_OUT_OFF": ("", 0, 0),
    "PSU_P1": ("", 0, 0),
    "PSU_P2": ("", 0, 0),
    "PSU_P3": ("", 0, 0),
    "ARB": ("", 0, 0),
    "PSU_ARB": ("", 0, 0),
}


@dataclass
class TestStep:
    device_kind: str = "load"
    # Ziel-Geraeteinstanz (device_id aus device_worker.py). Leer = "die einzige
    # aktuell verbundene Instanz dieser Art" -- so bleiben alte, mit nur einem
    # Geraet je Art erstellte Testablaeufe ohne Anpassung lauffaehig.
    device_id: str = ""
    action: str = "CURR"
    value: float = 0.0
    # Dauer (s): bei normalen Aktionen die Wartezeit NACH dem (sofortigen)
    # Setzen des Sollwerts, bevor der naechste Schritt beginnt. Bei einem
    # Arbiträrsignal-Schritt (action in ARB_ACTIONS) ist es stattdessen die
    # Laufzeit des Signals selbst -- der Schritt ist also selbst "die Aktion".
    duration: float = 0.0
    enabled: bool = True
    # -- Arbiträrsignal-Parameter (nur relevant wenn action in ARB_ACTIONS) --
    arb_shape: str = "sine"       # "sine" | "square"
    arb_target: str = ""          # tatsaechlich gesendeter Aktionscode, z.B. "VOLT"/"PSU_CURR"
    arb_amplitude: float = 0.0    # Signal schwingt zwischen offset-amplitude und offset+amplitude
    arb_offset: float = 0.0
    arb_frequency: float = 1.0    # Hz
    arb_interval_ms: int = 200    # Abstand zwischen zwei Sollwert-Updates


def arb_value(step: "TestStep", t: float) -> float:
    """Momentanwert des Arbiträrsignals von `step` zum Zeitpunkt t (Sekunden)."""
    phase = 2.0 * math.pi * step.arb_frequency * t
    raw = math.sin(phase) if step.arb_shape != "square" else (1.0 if math.sin(phase) >= 0 else -1.0)
    value = step.arb_offset + step.arb_amplitude * raw
    unit, lo, hi = ACTION_VALUE_RANGE.get(step.arb_target, ("", value, value))
    if lo < hi:
        value = min(max(value, lo), hi)
    return value


def is_arb_action(action_code: str) -> bool:
    return action_code in ARB_ACTIONS


def save_steps(steps: list[TestStep], path: Path) -> None:
    data = [asdict(step) for step in steps]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_steps(path: Path) -> list[TestStep]:
    data = json.loads(path.read_text(encoding="utf-8"))
    steps = []
    for item in data:
        if "device" in item and "device_kind" not in item:
            item = dict(item)
            legacy = item.pop("device")
            item["device_kind"] = _LEGACY_DEVICE_KIND.get(legacy, legacy)
            item.setdefault("device_id", "")
        steps.append(TestStep(**item))
    return steps


def action_label(device_kind: str, action_code: str) -> str:
    for label, code in DEVICE_ACTIONS[device_kind].items():
        if code == action_code:
            return label
    return action_code
