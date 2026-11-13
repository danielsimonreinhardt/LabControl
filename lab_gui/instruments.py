"""Messmittel (FT-8, 0.59.1): wer hat gemessen, und war es kalibriert?

- Kennung je Geraet (Typ, Seriennummer, Firmware) meldet der DeviceWorker beim Verbinden
  (device_identity); Netzteil, Oszilloskop und Batterie-Simulator ergaenzt MainWindow aus ihren Signalen.
  Das HCS-Netzteil kennt keine Firmware-Abfrage, die Seriennummer ist die USB-Seriennummer.
- Inventarnummer, Kalibrierdatum und Faelligkeit pflegt der Nutzer in den Einstellungen; sie liegen in
  messmittel.json im gemeinsamen Ordner (FP-20 a), damit PC und Pi denselben Stand sehen.
- Laufbericht, JUnit und run-result.json listen die verwendeten Messmittel; eine abgelaufene Kalibrierung
  ist nur eine Warnung (FP-21 a, Hobby-Labor ohne Kalibrierpflicht).

Qt-frei, damit Kommandozeile und Pruefungen es ohne Fenster nutzen.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_NAME = "messmittel.json"
FILE_FORMAT = "labcontrol-messmittel"
CAL_FIELDS = ("inventory", "calibrated", "due", "note")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_idn(text: str) -> dict:
    """Typ/Seriennummer/Firmware aus einer Kennungs-Antwort, so gut es geht:
    "KORAD-SLAB KEL102 V2.20 SN:00010925", "microHIL,fw=0.1.0,SN=2065...", "UNI-T Technologies,MSO1154HD-S,SN,FW"."""
    text = (text or "").strip()
    result = {"model": "", "serial": "", "firmware": ""}
    if not text:
        return result
    fields = dict(part.split("=", 1) for part in text.split(",") if "=" in part)
    if fields:
        result["serial"] = fields.get("SN", "")
        result["firmware"] = fields.get("fw", "") or fields.get("FW", "")
        result["model"] = next((p for p in text.split(",") if "=" not in p), "").strip()
        return result
    parts = [p.strip() for p in text.split(",")]
    if len(parts) >= 4:   # SCPI *IDN?: Hersteller, Modell, Seriennummer, Firmware
        result.update(model=f"{parts[0]} {parts[1]}".strip(), serial=parts[2], firmware=parts[3])
        return result
    sn = re.search(r"SN[:=]?\s*(\S+)", text)
    fw = re.search(r"\bV(\d+(?:\.\d+)+)\b", text)
    result["serial"] = sn.group(1) if sn else ""
    result["firmware"] = f"V{fw.group(1)}" if fw else ""
    model = text
    for match in (sn, fw):
        if match:
            model = model.replace(match.group(0), "")
    result["model"] = " ".join(model.split())
    return result


def valid_date(text: str) -> bool:
    if not text:
        return True
    if not _DATE_RE.match(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


class Instruments:
    """Kennungen (aus der laufenden Sitzung) und Kalibrierdaten (Datei)."""

    def __init__(self, path_provider=None) -> None:
        self._lock = threading.Lock()
        self._identity: dict[str, dict] = {}    # device_id -> {"kind","model","serial","firmware"}
        self._calibration: dict[str, dict] = {}
        self._path_provider = path_provider     # () -> Path der messmittel.json
        self.reload()

    # -- Kennungen ---------------------------------------------------------------

    def set_identity(self, device_id: str, kind: str = "", **fields) -> None:
        with self._lock:
            entry = self._identity.setdefault(device_id, {"kind": kind, "model": "", "serial": "", "firmware": ""})
            if kind:
                entry["kind"] = kind
            for key in ("model", "serial", "firmware"):
                value = fields.get(key)
                if value:
                    entry[key] = str(value)

    def identity(self, device_id: str) -> dict:
        with self._lock:
            return dict(self._identity.get(device_id, {}))

    def known_ids(self) -> list[str]:
        with self._lock:
            return sorted(set(self._identity) | set(self._calibration))

    # -- Kalibrierdaten ------------------------------------------------------------

    def path(self) -> Path | None:
        return Path(self._path_provider()) if self._path_provider is not None else None

    def reload(self) -> None:
        path = self.path()
        data: dict = {}
        if path is not None and path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                data = raw.get("devices", {}) if isinstance(raw, dict) else {}
            except (OSError, ValueError) as exc:
                logger.warning("Messmittel-Datei %s nicht lesbar: %s", path, exc)
        clean = {}
        for device_id, entry in (data if isinstance(data, dict) else {}).items():
            if isinstance(entry, dict):
                clean[str(device_id)] = {k: str(entry.get(k, "") or "") for k in CAL_FIELDS}
        with self._lock:
            self._calibration = clean

    def calibration(self, device_id: str) -> dict:
        with self._lock:
            return dict(self._calibration.get(device_id, {k: "" for k in CAL_FIELDS}))

    def set_calibration(self, device_id: str, **fields) -> None:
        """Speichert sofort in messmittel.json. Ungueltige Daten (kein JJJJ-MM-TT) -> ValueError."""
        for key in ("calibrated", "due"):
            if key in fields and not valid_date(str(fields[key] or "").strip()):
                raise ValueError(f"{key}: Datum als JJJJ-MM-TT angeben")
        self.reload()   # Aenderungen am anderen Rechner nicht ueberschreiben
        with self._lock:
            entry = self._calibration.setdefault(device_id, {k: "" for k in CAL_FIELDS})
            for key in CAL_FIELDS:
                if key in fields:
                    entry[key] = str(fields[key] or "").strip()
            if not any(entry.values()):
                del self._calibration[device_id]
            snapshot = {d: dict(e) for d, e in self._calibration.items()}
        path = self.path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps({"format": FILE_FORMAT, "version": 1, "devices": snapshot}, indent=2,
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    # -- fuer einen Lauf -------------------------------------------------------------

    def for_run(self, device_ids, labels: dict | None = None, today: date | None = None) -> list[dict]:
        """Messmittel eines Laufs mit Kennung, Kalibrierung und Warnung ("expired"/"due_unknown")."""
        today = today or date.today()
        rows = []
        for device_id in sorted(set(device_ids)):
            identity = self.identity(device_id)
            cal = self.calibration(device_id)
            due = cal.get("due", "")
            expired = bool(due) and valid_date(due) and date.fromisoformat(due) < today
            rows.append({
                "device_id": device_id,
                "label": (labels or {}).get(device_id, device_id),
                "kind": identity.get("kind", device_id.split(":", 1)[0]),
                "model": identity.get("model", ""),
                "serial": identity.get("serial", ""),
                "firmware": identity.get("firmware", ""),
                "inventory": cal.get("inventory", ""),
                "calibrated": cal.get("calibrated", ""),
                "due": due,
                "expired": expired,
            })
        return rows


def expired_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("expired")]


def _usb_serial(device_id: str) -> str:
    """Seriennummer aus einer stabilen Geraete-ID (psu:HCS60 -> HCS60); Anschluss-IDs (COM6, usb-1-1.2)
    sind keine Seriennummer."""
    rest = device_id.split(":", 1)[1] if ":" in device_id else ""
    return "" if not rest or rest.upper().startswith("COM") or rest.startswith("usb-") or rest == "SIM" else rest


def connect_worker(worker, instruments: Instruments) -> None:
    """Kennungen aus den Signalen des DeviceWorkers sammeln (Fenster und Kommandozeile). Die Verbindungen
    laufen im Worker-Thread; Instruments ist thread-sicher."""
    worker.device_identity.connect(
        lambda device_id, kind, text: instruments.set_identity(device_id, kind, **parse_idn(text)))
    worker.psu_ratings.connect(
        lambda device_id, volts, amps: instruments.set_identity(
            device_id, "psu", model=f"HCS-34xx {volts:g} V / {amps:g} A", serial=_usb_serial(device_id)))
    worker.picoscope_state.connect(
        lambda device_id, _state, variant, serial: instruments.set_identity(
            device_id, "picoscope", model=f"PicoScope {variant}" if variant else "", serial=serial))
    worker.scpiscope_state.connect(
        lambda device_id, _state, model, serial: instruments.set_identity(
            device_id, "scpiscope", model=model, serial=serial))
    worker.battsim_state.connect(
        lambda device_id, state: instruments.set_identity(
            device_id, "battsim", model="Batterie-Simulator",
            firmware=(state or {}).get("fw_version") or ""))
