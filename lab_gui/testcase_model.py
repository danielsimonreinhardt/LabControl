"""Datenmodell fuer Testablauf-Schritte (Testcase-Editor)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

# Anzeigename -> interner Aktionscode, je Geraet
LOAD_ACTIONS = {
    "Konstantstrom (CC)": "CURR",
    "Konstantspannung (CV)": "VOLT",
    "Konstantwiderstand (CR)": "RES",
    "Konstantleistung (CW)": "POW",
    "Ausgang EIN": "OUT_ON",
    "Ausgang AUS": "OUT_OFF",
}

PSU_ACTIONS = {
    "Spannung setzen": "PSU_VOLT",
    "Strom setzen": "PSU_CURR",
    "Ausgang EIN": "PSU_OUT_ON",
    "Ausgang AUS": "PSU_OUT_OFF",
    "Preset P1 abrufen": "PSU_P1",
    "Preset P2 abrufen": "PSU_P2",
    "Preset P3 abrufen": "PSU_P3",
}

DEVICE_ACTIONS = {
    "Last": LOAD_ACTIONS,
    "Netzteil": PSU_ACTIONS,
}

# Aktionen, die keinen Zahlenwert benoetigen (Wert-Feld wird deaktiviert).
# PSU_OUT_ON braucht den Wert als Spannung (Strom wird automatisch auf
# mindestens 0.1A angehoben, siehe DeviceWorker._dispatch_action).
VALUELESS_ACTIONS = {"OUT_ON", "OUT_OFF", "PSU_OUT_OFF", "PSU_P1", "PSU_P2", "PSU_P3"}

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
}


@dataclass
class TestStep:
    device: str = "Last"
    action: str = "CURR"
    value: float = 0.0
    duration: float = 0.0
    enabled: bool = True


def save_steps(steps: list[TestStep], path: Path) -> None:
    data = [asdict(step) for step in steps]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_steps(path: Path) -> list[TestStep]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [TestStep(**item) for item in data]


def action_label(device: str, action_code: str) -> str:
    for label, code in DEVICE_ACTIONS[device].items():
        if code == action_code:
            return label
    return action_code
