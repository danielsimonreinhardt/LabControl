"""Fehlerbox (FT-9, 0.61.0): benannte Fehler als Schaltfolgen am microHIL.

Ein Fehler ist eine Schaltfolge in Kurzschrift, z. B. "R1=1, O3=1, A1=1500":
  R<n>=0|1   Relais n (1-4) aus/ein          O<n>=0|1   Digitalausgang n (1-8) aus/ein
  A<n>=<mV>  Analogausgang n (1-2) in mV
Gesetzt wird in der angegebenen Reihenfolge, aufgehoben in umgekehrter Reihenfolge mit dem Ruhezustand
(Relais/Ausgang aus, Analogausgang 0 mV). Hoechstens ein Fehler ist aktiv; ein Wechsel hebt immer zuerst auf,
wartet `settle_ms` und setzt dann (FP-26 c: im Ablauf automatisch, an der Kachel abgelehnt). Die Kanaele eines
aktiven Fehlers sind fuer direkte Bedienung gesperrt (FP-27 a).

Die Fehler liegen in faultbox.json im gemeinsamen Ordner (FP-25 a); ein Fehler nennt optional seinen microHIL
("hil:<seriennummer>", leer = der einzige verbundene). Qt-frei.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from microhil.driver import AOUT_COUNT, AOUT_MAX_MV, OUT_COUNT, RELAY_COUNT

FILE_NAME = "faultbox.json"
FILE_FORMAT = "labcontrol-faultbox"
CHANNEL_COUNTS = {"R": RELAY_COUNT, "O": OUT_COUNT, "A": AOUT_COUNT}
DEFAULT_SETTLE_MS = 100
_TOKEN = re.compile(r"^\s*([ROAroa])\s*(\d+)\s*=\s*(\d+)\s*$")


class FaultError(ValueError):
    pass


def parse_sequence(text: str) -> list[tuple[str, int, int]]:
    """"R1=1, O3=0, A1=1500" -> [("R", 1, 1), ("O", 3, 0), ("A", 1, 1500)]."""
    result = []
    seen = set()
    for part in re.split(r"[,;]", text or ""):
        if not part.strip():
            continue
        match = _TOKEN.match(part)
        if not match:
            raise FaultError(f"'{part.strip()}': erwartet z. B. R1=1, O3=1 oder A1=1500")
        kind, channel, value = match.group(1).upper(), int(match.group(2)), int(match.group(3))
        if not 1 <= channel <= CHANNEL_COUNTS[kind]:
            raise FaultError(f"'{part.strip()}': Kanal 1-{CHANNEL_COUNTS[kind]}")
        if kind in "RO" and value not in (0, 1):
            raise FaultError(f"'{part.strip()}': 0 oder 1")
        if kind == "A" and value > AOUT_MAX_MV:
            raise FaultError(f"'{part.strip()}': höchstens {AOUT_MAX_MV} mV")
        if (kind, channel) in seen:
            raise FaultError(f"'{kind}{channel}' doppelt")
        seen.add((kind, channel))
        result.append((kind, channel, value))
    if not result:
        raise FaultError("Leere Schaltfolge")
    return result


def to_action(kind: str, channel: int, value: int) -> tuple[str, int, float]:
    """Kurzschrift -> (HIL-Aktion, Kanal, Wert) wie im Testablauf (testcase_model.HIL_ACTIONS)."""
    if kind == "R":
        return ("HIL_RELAY_ON" if value else "HIL_RELAY_OFF"), channel, 0.0
    if kind == "O":
        return ("HIL_OUT_ON" if value else "HIL_OUT_OFF"), channel, 0.0
    return "HIL_AOUT", channel, float(value)


def channel_of_action(action: str, channel: int) -> tuple[str, int] | None:
    """HIL-Aktion -> belegter Kanal ("R"/"O"/"A", n); PWM n liegt auf OUT n."""
    if action in ("HIL_RELAY_ON", "HIL_RELAY_OFF"):
        return "R", channel
    if action in ("HIL_OUT_ON", "HIL_OUT_OFF", "HIL_PWM"):
        return "O", channel
    if action == "HIL_AOUT":
        return "A", channel
    return None


@dataclass
class Fault:
    name: str
    sequence: str
    hil: str = ""
    settle_ms: int = DEFAULT_SETTLE_MS
    description: str = ""

    def steps(self) -> list[tuple[str, int, int]]:
        return parse_sequence(self.sequence)

    def set_actions(self) -> list[tuple[str, int, float]]:
        return [to_action(*step) for step in self.steps()]

    def clear_actions(self) -> list[tuple[str, int, float]]:
        return [to_action(kind, channel, 0) for kind, channel, _value in reversed(self.steps())]

    def channels(self) -> set[tuple[str, int]]:
        return {(kind, channel) for kind, channel, _value in self.steps()}

    def label(self) -> str:
        return f"{self.name}: {self.sequence}"


def validate(faults: list[Fault]) -> None:
    names = set()
    for fault in faults:
        if not fault.name.strip():
            raise FaultError("Fehler ohne Namen")
        if fault.name in names:
            raise FaultError(f"Name '{fault.name}' doppelt")
        names.add(fault.name)
        fault.steps()
        if not 0 <= int(fault.settle_ms) <= 10_000:
            raise FaultError(f"{fault.name}: Wartezeit 0-10000 ms")


def load(path: Path) -> list[Fault]:
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("faults", []) if isinstance(data, dict) else []
    faults = []
    for item in items:
        if not isinstance(item, dict):
            continue
        faults.append(Fault(name=str(item.get("name", "")).strip(), sequence=str(item.get("sequence", "")),
                            hil=str(item.get("hil", "") or ""),
                            settle_ms=int(item.get("settle_ms", DEFAULT_SETTLE_MS) or 0),
                            description=str(item.get("description", "") or "")))
    return faults


def save(path: Path, faults: list[Fault]) -> None:
    validate(faults)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps({"format": FILE_FORMAT, "version": 1, "faults": [asdict(f) for f in faults]},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
