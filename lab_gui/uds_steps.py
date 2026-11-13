"""UDS-Ablaufschritte (FT-1, 0.60.1): Schritt -> UdsService-Befehl, Ergebnis -> Urteil.

Parameter stehen in TestStep.params (Format v3):
  UDS_SESSION    {"session": "Extended"}                     Name aus dem ODX oder Nummer
  UDS_SECURITY   {"level": "Application"}                    leer = einzige Stufe
  UDS_READ_DID   {"did": "EcuConditions", "element": "supply"}   Element-Wert fuer Pruefung/Variable
  UDS_WRITE_DID  {"did": "RamTestValue", "values": {"ramTestValue": 5}} oder {"hex": "00 00 00 05"}
  UDS_READ_DTC   {"mask": 255, "present": ["0x904001"], "absent": ["0xC10016"], "empty": false}
  UDS_CLEAR_DTC  {"group": "0xFFFFFF"}
  UDS_ROUTINE    {"routine": "LedSelfTest", "step": "start", "data": ""}
  UDS_RESET      {"type": 1}
  UDS_RAW        {"request": "22 F1 86"}
und bei allen {"expect": "positive"} (Standard) oder {"expect": "nrc:0x33"}.

Urteil: eine Abweichung von der Erwartung (falsche Antwort, fehlender/verbotener DTC) ist eine verletzte
Pruefung und bricht den Lauf ab (Exit 1, nicht bestanden); keine Antwort oder ein Busfehler ist ein Fehler
(Exit 2). Qt-frei.
"""
from __future__ import annotations

from dataclasses import dataclass

from i18n import tr

COMMANDS = {
    "UDS_SESSION": "session", "UDS_SECURITY": "unlock", "UDS_READ_DID": "read_did", "UDS_WRITE_DID": "write_did",
    "UDS_READ_DTC": "read_dtcs", "UDS_CLEAR_DTC": "clear_dtcs", "UDS_ROUTINE": "routine", "UDS_RESET": "reset",
    "UDS_RAW": "raw",
}
RESET_TYPES = {1: "hardReset", 2: "keyOffOnReset", 3: "softReset"}


@dataclass
class Verdict:
    status: str          # "ok" | "check_failed" | "error"
    message: str = ""
    value: float = 0.0


def _int(value, default: int = 0) -> int:
    if value in (None, ""):
        return default
    return value if isinstance(value, int) else int(str(value).strip(), 0)


def parse_expect(text: str | None) -> int | None:
    """None = positive Antwort erwartet, sonst die erwartete NRC."""
    text = (text or "positive").strip().lower()
    if text in ("positive", "positiv", "ok"):
        return None
    return _int(text.split(":", 1)[-1])


def command_for(action: str, params: dict) -> tuple[str, dict]:
    if action not in COMMANDS:
        raise ValueError(f"Unbekannte UDS-Aktion {action}")
    p = dict(params or {})
    command = COMMANDS[action]
    if action == "UDS_READ_DTC":
        return command, {"mask": _int(p.get("mask"), 0xFF)}
    if action == "UDS_CLEAR_DTC":
        return command, {"group": _int(p.get("group"), 0xFFFFFF)}
    if action == "UDS_RESET":
        return command, {"type": _int(p.get("type"), 1)}
    keep = {"UDS_SESSION": ("session",), "UDS_SECURITY": ("level",), "UDS_READ_DID": ("did",),
            "UDS_WRITE_DID": ("did", "values", "hex"), "UDS_ROUTINE": ("routine", "step", "data"),
            "UDS_RAW": ("request",)}[action]
    return command, {k: p[k] for k in keep if k in p and p[k] not in (None, "")}


def _codes(items) -> list[int]:
    return [_int(c) for c in items or [] if str(c).strip()]


def evaluate(action: str, params: dict, result) -> Verdict:
    """Ergebnis eines UdsService-Befehls (uds_client.UdsResult) gegen die Erwartung des Schritts."""
    expected_nrc = parse_expect(params.get("expect"))
    if not result.ok and result.nrc is None:
        return Verdict("error", tr("UDS: {message}", message=result.message or tr("keine Antwort")))
    if expected_nrc is not None:
        if result.nrc == expected_nrc:
            return Verdict("ok", value=float(expected_nrc))
        got = tr("positiv") if result.ok else f"NRC 0x{result.nrc:02X}"
        return Verdict("check_failed", tr("Erwartet NRC 0x{nrc:02X}, erhalten {got}", nrc=expected_nrc, got=got),
                       float(result.nrc or 0))
    if not result.ok:
        return Verdict("check_failed", tr("Erwartet positiv, erhalten {got}", got=result.describe()),
                       float(result.nrc or 0))
    if action == "UDS_READ_DID":
        return _did_value(params, result)
    if action == "UDS_READ_DTC":
        return _dtc_expectations(params, result)
    return Verdict("ok")


def _did_value(params: dict, result) -> Verdict:
    """Wert fuer Pruefung und Variable: das gewaehlte Element als Zahl (Wertetabelle: Rohwert, skaliert:
    physikalisch). Ohne Element der erste Zahlenwert der DID; eine DID ohne ODX-Eintrag liefert 0."""
    numeric = result.values.get("numeric") or {}
    element = params.get("element") or ""
    if element:
        if element not in numeric:
            return Verdict("error", tr("Element '{name}' nicht in der DID", name=element))
        value = numeric[element]
        if value is None:
            return Verdict("error", tr("Element '{name}' hat keinen Zahlenwert", name=element))
        return Verdict("ok", value=float(value))
    value = next((v for v in numeric.values() if v is not None), 0.0)
    return Verdict("ok", value=float(value))


def _dtc_expectations(params: dict, result) -> Verdict:
    present = {code for code, _status in result.values.get("dtcs", [])}
    missing = [c for c in _codes(params.get("present")) if c not in present]
    forbidden = [c for c in _codes(params.get("absent")) if c in present]
    if params.get("empty") and present:
        forbidden = sorted(present)
    if missing or forbidden:
        parts = []
        if missing:
            parts.append(tr("fehlt: {codes}", codes=", ".join(f"0x{c:06X}" for c in missing)))
        if forbidden:
            parts.append(tr("gesetzt: {codes}", codes=", ".join(f"0x{c:06X}" for c in forbidden)))
        return Verdict("check_failed", tr("Fehlerspeicher: {details}", details="; ".join(parts)), float(len(present)))
    return Verdict("ok", value=float(len(present)))


def summary(action: str, params: dict) -> str:
    """Kurztext fuer Editor-Zeile und Bericht."""
    p = params or {}
    if action == "UDS_SESSION":
        text = str(p.get("session", "?"))
    elif action == "UDS_SECURITY":
        text = str(p.get("level") or tr("Stufe automatisch"))
    elif action == "UDS_READ_DID":
        text = str(p.get("did", "?")) + (f".{p['element']}" if p.get("element") else "")
    elif action == "UDS_WRITE_DID":
        values = p.get("values") or {}
        text = f"{p.get('did', '?')} ← " + (", ".join(f"{k}={v}" for k, v in values.items()) or p.get("hex", "?"))
    elif action == "UDS_READ_DTC":
        bits = []
        if p.get("present"):
            bits.append(tr("erwartet {codes}", codes=", ".join(map(str, p["present"]))))
        if p.get("absent"):
            bits.append(tr("nicht {codes}", codes=", ".join(map(str, p["absent"]))))
        if p.get("empty"):
            bits.append(tr("leer"))
        text = "; ".join(bits) or tr("lesen")
    elif action == "UDS_CLEAR_DTC":
        text = str(p.get("group") or "0xFFFFFF")
    elif action == "UDS_ROUTINE":
        text = f"{p.get('routine', '?')} {p.get('step', 'start')}" + (f" [{p['data']}]" if p.get("data") else "")
    elif action == "UDS_RESET":
        reset = _int(p.get("type"), 1)
        text = RESET_TYPES.get(reset, f"0x{reset:02X}")
    elif action == "UDS_RAW":
        text = str(p.get("request", "?"))
    else:
        text = ""
    nrc = parse_expect(p.get("expect"))
    return text + (f" → NRC 0x{nrc:02X}" if nrc is not None else "")
