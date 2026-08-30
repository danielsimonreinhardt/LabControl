"""Datenmodell fuer Testablauf-Schritte (Testcase-Editor)."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from i18n import tr

# Interner Aktionscode -> deutscher Basis-Anzeigename (Uebersetzungsschluessel
# fuer i18n.tr, siehe action_label()), je Geraeteart ("load"/"psu").
LOAD_ACTIONS = {
    "CURR": "Konstantstrom (CC)",
    "VOLT": "Konstantspannung (CV)",
    "RES": "Konstantwiderstand (CR)",
    "POW": "Konstantleistung (CW)",
    "OUT_ON": "Ausgang EIN",
    "OUT_OFF": "Ausgang AUS",
    "ARB": "Arbiträrsignal",
}

PSU_ACTIONS = {
    "PSU_VOLT": "Spannung setzen",
    "PSU_CURR": "Strom setzen",
    "PSU_OUT_ON": "Ausgang EIN",
    "PSU_OUT_OFF": "Ausgang AUS",
    "PSU_P1": "Preset P1 abrufen",
    "PSU_P2": "Preset P2 abrufen",
    "PSU_P3": "Preset P3 abrufen",
    "PSU_ARB": "Arbiträrsignal",
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

# Geraeteart -> deutscher Basis-Anzeigename (Uebersetzungsschluessel).
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

# Einheit/Min/Max fuer das Wert-Feld je Aktionscode (Einheiten sind
# sprachunabhaengig, daher nicht ueber i18n.tr uebersetzt). Die
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

# Kontrollfluss-Schritttypen (Ablaufsteuerung) neben dem normalen
# Geraete-Aktionsschritt ("action"). "loop"/"while"/"if" eroeffnen einen
# Block, der durch ein zugehoeriges "end" geschlossen wird (siehe
# validate_structure()); "else" ist optional und nur innerhalb eines
# "if"-Blocks gueltig. "set_var"/"inc_var" setzen bzw. erhoehen eine
# Laufvariable, die in Bedingungen als cond_source=="variable" gelesen wird.
STEP_TYPE_ACTION = "action"
CONTROL_STEP_TYPES = {"loop", "while", "if", "else", "end", "set_var", "inc_var"}
BLOCK_START_TYPES = {"loop", "while", "if"}
CONDITION_STEP_TYPES = {"while", "if"}

# step_type-Basis-Anzeigenamen (Uebersetzungsschluessel), analog zu
# DEVICE_KIND_LABELS/LOAD_ACTIONS oben.
CONTROL_STEP_LABELS = {
    "loop": "Schleife",
    "while": "Solange",
    "if": "Wenn",
    "else": "Sonst",
    "end": "Ende",
    "set_var": "Variable setzen",
    "inc_var": "Variable erhöhen",
}

COND_SOURCES = ("measurement", "time", "variable")
COND_FIELDS = ("voltage", "current", "power")
COND_OPS = ("<", "<=", ">", ">=", "==", "!=")
COND_OP_LABELS = {"<": "<", "<=": "≤", ">": ">", ">=": "≥", "==": "=", "!=": "≠"}
COND_TIME_REFS = ("block", "run")
COND_FIELD_UNITS = {"voltage": "V", "current": "A", "power": "W"}
COND_FIELD_LABELS = {"voltage": "Spannung", "current": "Strom", "power": "Leistung"}

# Aktuelle Testablauf-Dateiversion (siehe save_steps/load_steps). Version 1
# war ein nacktes JSON-Array ohne Umschlag/Versionsnummer.
FILE_FORMAT_VERSION = 2


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
    # Bei "set_var"/"inc_var" ist es weiterhin die Wartezeit NACH dem Schritt;
    # bei allen anderen Kontrollfluss-Schritten (loop/while/if/else/end) wird
    # sie ignoriert.
    duration: float = 0.0
    enabled: bool = True
    # -- Arbiträrsignal-Parameter (nur relevant wenn action in ARB_ACTIONS) --
    arb_shape: str = "sine"       # "sine" | "square"
    arb_target: str = ""          # tatsaechlich gesendeter Aktionscode, z.B. "VOLT"/"PSU_CURR"
    arb_amplitude: float = 0.0    # Signal schwingt zwischen offset-amplitude und offset+amplitude
    arb_offset: float = 0.0
    arb_frequency: float = 1.0    # Hz
    arb_interval_ms: int = 200    # Abstand zwischen zwei Sollwert-Updates

    # -- Ablaufsteuerung: Schritttyp-Diskriminator ------------------------
    # "action" (Standard, s.o.) | "loop" | "while" | "if" | "else" | "end"
    # | "set_var" | "inc_var". Bei allen Nicht-"action"-Typen spielen
    # device_kind/action/arb_* keine Rolle.
    step_type: str = STEP_TYPE_ACTION
    loop_count: int = 2         # nur "loop": Anzahl Durchlaeufe
    max_iterations: int = 1000  # nur "while": Endlosschleifen-Schutz, 0 = unbegrenzt
    var_name: str = ""          # nur "set_var"/"inc_var" (Zielvariable); `value` ist der Setz-/Inkrementwert

    # -- Bedingung (nur "while"/"if", siehe CONDITION_STEP_TYPES) --------
    cond_source: str = "measurement"   # "measurement" | "time" | "variable"
    cond_device_kind: str = "load"
    cond_device_id: str = ""           # leer = automatisch (einziges verbundenes Geraet dieser Art)
    cond_field: str = "voltage"        # "voltage" | "current" | "power"
    cond_op: str = "<"
    cond_value: float = 0.0
    cond_time_ref: str = "block"       # "block" (seit Blockstart) | "run" (seit Teststart)
    cond_var: str = ""                 # Variablenname bei cond_source=="variable"


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


def is_control_step(step: TestStep) -> bool:
    return step.step_type != STEP_TYPE_ACTION


def is_block_start(step: TestStep) -> bool:
    return step.step_type in BLOCK_START_TYPES


@dataclass
class BlockMatch:
    """Verknuepfung der drei Marker-Zeilen eines Kontrollfluss-Blocks.

    Unter allen drei Indizes (start_index, ggf. else_index, end_index) im
    `matching`-Dict von validate_structure() abgelegt, damit Runner/Editor von
    jeder der drei Zeilen aus die anderen nachschlagen koennen, ohne je nach
    Aufrufstelle unterschiedliche Lookup-Richtungen zu brauchen.
    """

    start_index: int
    else_index: int | None
    end_index: int


def validate_structure(
    steps: list[TestStep],
) -> tuple[dict[int, BlockMatch], list[int], list[tuple[int, str]]]:
    """Prueft die Verschachtelung der Block-Marker (loop/while/if/else/end).

    Ein Stack-Scan ueber alle Schritte (auch deaktivierte -- die Struktur muss
    unabhaengig vom "enabled"-Flag konsistent sein, sonst koennte ein spaeteres
    Aktivieren einen unbalancierten Ablauf erzeugen). Rueckgabe:
      - matching: siehe BlockMatch, erreichbar ueber jede der drei Marker-Zeilen.
      - depths: Einrueckungstiefe je Zeile (0 = oberste Ebene) fuer die
        Editor-Darstellung; Start-/Sonst-/Ende-Zeile liegen auf der Tiefe des
        Blocks selbst, der Blockinhalt eine Ebene tiefer.
      - errors: (Zeilenindex, Fehlermeldung) fuer strukturelle Probleme --
        "end" ohne offenen Block, "else" ausserhalb/doppelt in einem "if",
        am Dateiende nicht geschlossene Bloecke.
    """
    matching: dict[int, BlockMatch] = {}
    depths = [0] * len(steps)
    errors: list[tuple[int, str]] = []
    # Je offener Block: [start_index, kind, else_index-oder-None].
    stack: list[list] = []
    depth = 0

    for i, step in enumerate(steps):
        t = step.step_type
        if t in BLOCK_START_TYPES:
            depths[i] = depth
            stack.append([i, t, None])
            depth += 1
        elif t == "else":
            if not stack or stack[-1][1] != "if":
                errors.append((i, tr("„Sonst“ ohne zugehöriges „Wenn“")))
                depths[i] = depth
                continue
            if stack[-1][2] is not None:
                errors.append((i, tr("Mehrfaches „Sonst“ im selben „Wenn“-Block")))
                depths[i] = depth
                continue
            depth -= 1
            depths[i] = depth
            stack[-1][2] = i
            depth += 1
        elif t == "end":
            if not stack:
                errors.append((i, tr("„Ende“ ohne offenen Block")))
                depths[i] = depth
                continue
            depth -= 1
            depths[i] = depth
            start_index, _kind, else_index = stack.pop()
            match = BlockMatch(start_index=start_index, else_index=else_index, end_index=i)
            matching[start_index] = match
            matching[i] = match
            if else_index is not None:
                matching[else_index] = match
        else:
            depths[i] = depth

    for start_index, _kind, _else_index in stack:
        errors.append((start_index, tr("Block nicht geschlossen (fehlendes „Ende“)")))

    return matching, depths, errors


def condition_summary(step: TestStep) -> str:
    """Anzeigetext einer Bedingung (Zeile/Statuszeile), z.B.

    "Last (automatisch): Spannung < 3.0 V", "Zeit seit Blockstart ≥ 3600 s",
    "i < 10". Kennt keine Geraete-Anzeigenamen (dafuer fehlt der Kontext) --
    der Editor zeigt bei bekannten Geraeten stattdessen deren Label an.
    """
    op = COND_OP_LABELS.get(step.cond_op, step.cond_op)
    if step.cond_source == "measurement":
        device = (
            step.cond_device_id
            if step.cond_device_id
            else tr("{kind} (automatisch)", kind=kind_label(step.cond_device_kind))
        )
        field = tr(COND_FIELD_LABELS.get(step.cond_field, step.cond_field))
        unit = COND_FIELD_UNITS.get(step.cond_field, "")
        return tr(
            "{device}: {field} {op} {value:g} {unit}",
            device=device, field=field, op=op, value=step.cond_value, unit=unit,
        ).rstrip()
    if step.cond_source == "time":
        ref = tr("seit Blockstart") if step.cond_time_ref == "block" else tr("seit Teststart")
        return tr("Zeit {ref} {op} {value:g} s", ref=ref, op=op, value=step.cond_value)
    if step.cond_source == "variable":
        return f"{step.cond_var or '?'} {op} {step.cond_value:g}"
    return ""


def save_steps(steps: list[TestStep], path: Path) -> None:
    payload = {
        "format": "labor-testcase",
        "version": FILE_FORMAT_VERSION,
        "steps": [asdict(step) for step in steps],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_steps(path: Path) -> list[TestStep]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        items = data  # Altes Dateiformat (v1): nacktes Array ohne Umschlag.
    else:
        version = int(data.get("version", 0))
        if version > FILE_FORMAT_VERSION:
            raise ValueError(
                tr(
                    "Testablauf-Datei stammt aus einer neueren Programmversion "
                    "(Format {version}) und kann nicht geladen werden.",
                    version=version,
                )
            )
        items = data["steps"]
    steps = []
    for item in items:
        if "device" in item and "device_kind" not in item:
            item = dict(item)
            legacy = item.pop("device")
            item["device_kind"] = _LEGACY_DEVICE_KIND.get(legacy, legacy)
            item.setdefault("device_id", "")
        steps.append(TestStep(**item))
    return steps


def kind_label(device_kind: str) -> str:
    return tr(DEVICE_KIND_LABELS.get(device_kind, device_kind))


def action_label(device_kind: str, action_code: str) -> str:
    base_label = DEVICE_ACTIONS[device_kind].get(action_code)
    return tr(base_label) if base_label is not None else action_code
