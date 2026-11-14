"""XCP-Ablaufschritte (0.64.0, FT-10): Schritt -> XcpService-Befehl, Ergebnis -> Wert.

Parameter in TestStep.params (Format v3):
  XCP_READ   {"name": "DemoSwc.MotorNtc_mV.MotorNtc_mV", "index": 0}   Wert fuer Pruefung (xcp_value)/Variable
  XCP_WRITE  {"name": "DemoSwc.CalPrm.FilterAlpha", "values": [0.5]}  physikalisch, in den A2L-Grenzen
Schreiben nur aus GUI und Ablauf (FP-29 b), nicht ueber REST/MCP. Qt-frei.
"""
from __future__ import annotations

from i18n import tr


def _values(raw) -> list[float]:
    if isinstance(raw, (int, float)):
        return [float(raw)]
    if isinstance(raw, str):
        return [float(v.replace(",", ".")) for v in raw.replace(";", " ").split() if v.strip()]
    return [float(v) for v in raw or []]


def command_for(action: str, params: dict) -> tuple[str, dict]:
    p = dict(params or {})
    name = str(p.get("name", "")).strip()
    if not name:
        raise ValueError(tr("Kein A2L-Objekt angegeben"))
    if action == "XCP_READ":
        return "read", {"name": name, "index": int(p.get("index", 0) or 0)}
    if action == "XCP_WRITE":
        values = _values(p.get("values", p.get("value")))
        if not values:
            raise ValueError(tr("Kein Wert zum Schreiben"))
        return "write", {"name": name, "values": values}
    raise ValueError(f"Unbekannte XCP-Aktion {action}")


def summary(action: str, params: dict) -> str:
    p = dict(params or {})
    name = str(p.get("name", "")).strip()
    if not name:
        return ""
    if action == "XCP_WRITE":
        try:
            values = _values(p.get("values", p.get("value")))
        except ValueError:
            values = []
        return f"{name} = " + " ".join(f"{v:g}" for v in values) if values else name
    index = int(p.get("index", 0) or 0)
    return f"{name}[{index}]" if index else name
