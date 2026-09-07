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
    "PSU_ARB": "Arbiträrsignal",
}

CAN_ACTIONS = {
    "CAN_SEND": "CAN-Frame senden",
}

# microHIL-Aktionen (siehe microhil/driver.py). Jede Aktion bezieht sich auf
# genau EINEN Kanal (device_worker.MicroHIL-Methoden nehmen alle einen
# Kanalindex) -- welcher, steht in TestStep.hil_channel statt im
# Aktionscode selbst (anders als z.B. eine hypothetische "OUT1_ON"), damit
# die Kanalauswahl ein normales Editor-Feld bleibt statt die Aktionsliste
# je Aktion um die jeweilige Kanalzahl aufzublaehen (8 OUT + 4 RELAY + 2
# AOUT + 8 IN + 4 AIN waeren sonst 26 Eintraege). HIL_IN_READ/HIL_AIN_READ
# lesen den aktuellen Kanalzustand direkt vom Geraet (siehe
# device_worker._dispatch_action) statt aus dem 1s-Poll-Cache -- der
# gelesene Wert kommt darum sofort und fliesst nur in eine optionale
# Pass/Fail-Pruefung ein (siehe HIL_READ_ACTIONS/testcase_runner.py).
HIL_ACTIONS = {
    "HIL_OUT_ON": "Digitalausgang EIN",
    "HIL_OUT_OFF": "Digitalausgang AUS",
    "HIL_RELAY_ON": "Relais EIN",
    "HIL_RELAY_OFF": "Relais AUS",
    "HIL_AOUT": "Analogausgang setzen",
    "HIL_IN_READ": "Digitaleingang lesen",
    "HIL_AIN_READ": "Analogeingang lesen",
}

# Aktionscode -> Kanalzahl des jeweiligen microHIL-Kanaltyps (siehe
# microhil/driver.py: OUT_COUNT/RELAY_COUNT/AOUT_COUNT/IN_COUNT/AIN_COUNT) --
# begrenzt die Kanal-Spinbox im Editor (siehe testcase_tab._build_action_row)
# auf die tatsaechlich vorhandenen Kanaele je Aktion.
HIL_CHANNEL_COUNTS = {
    "HIL_OUT_ON": 8,
    "HIL_OUT_OFF": 8,
    "HIL_RELAY_ON": 4,
    "HIL_RELAY_OFF": 4,
    "HIL_AOUT": 2,
    "HIL_IN_READ": 8,
    "HIL_AIN_READ": 4,
}

# Lese-Aktionen (siehe HIL_ACTIONS-Kommentar): liefern anders als alle
# anderen Aktionen einen Messwert zurueck, den testcase_runner.py sofort
# (ohne auf den naechsten Poll-Zyklus zu warten) gegen eine optionale
# Pass/Fail-Pruefung auswertet.
HIL_READ_ACTIONS = {"HIL_IN_READ", "HIL_AIN_READ"}

# Arbiträrsignal-Aktionscode je Geraeteart -> Liste der Aktionscodes, die als
# Zielgroesse (das tatsaechlich modulierte Sollwert-Kommando) waehlbar sind.
# Schaltaktionen (Ausgang EIN/AUS, Presets) scheiden aus, da sie keinen
# Zahlenwert entgegennehmen.
ARB_ACTIONS = {"ARB", "PSU_ARB"}

ARB_TARGETS: dict[str, list[str]] = {
    "load": ["CURR", "VOLT", "RES", "POW"],
    "psu": ["PSU_VOLT", "PSU_CURR"],
}

# Interner Signalform-Code -> deutscher Basis-Anzeigename (Uebersetzungsschluessel
# fuer i18n.tr, siehe arb_shape_label()). Zentral hier statt dupliziert in
# signal_dialog.py/testcase_tab.py, da beide dieselbe Zuordnung brauchen.
ARB_SHAPE_LABELS = {"sine": "Sinus", "square": "Rechteck", "triangle": "Dreieck", "sawtooth": "Sägezahn"}


def arb_shape_label(shape: str) -> str:
    return tr(ARB_SHAPE_LABELS.get(shape, shape))

DEVICE_ACTIONS = {
    "load": LOAD_ACTIONS,
    "psu": PSU_ACTIONS,
    "can": CAN_ACTIONS,
    "hil": HIL_ACTIONS,
}

# Geraeteart -> deutscher Basis-Anzeigename (Uebersetzungsschluessel).
DEVICE_KIND_LABELS = {
    "load": "Last",
    "psu": "Netzteil",
    "can": "CAN-Bus",
    "hil": "microHIL",
}

# Geraetearten, die U/I/P-Messwerte liefern (siehe testcase_runner.py:
# on_load_measurement/on_psu_measurement) -- fuer die Bedingungs-Geraeteauswahl
# (cond_source == "measurement", siehe condition_dialog.py) relevant. CAN-Bus
# hat keine solchen Messwerte, daher hier bewusst ausgeschlossen statt wie bei
# DEVICE_KIND_LABELS/DEVICE_ACTIONS jede Geraeteart zu listen.
MEASUREMENT_DEVICE_KINDS = ("load", "psu")

# Alte Testablauf-Dateien speichern die Geraeteart noch unter dem Feldnamen
# "device" mit den frueheren deutschen Anzeigenamen als Wert.
_LEGACY_DEVICE_KIND = {"Last": "load", "Netzteil": "psu"}

# Aktionen, die keinen Zahlenwert benoetigen (Wert-Feld wird deaktiviert).
# PSU_OUT_ON ist eine reine Schaltaktion (siehe BUGS.md #17): sie laesst einen
# zuvor per PSU_VOLT gesetzten Spannungs-Sollwert unangetastet und hebt nur
# den Strom auf mindestens 0.1A an, damit ueberhaupt Ausgangsleistung
# fliesst (siehe DeviceWorker._dispatch_action) -- analog zu PSU_OUT_OFF, das
# nur den Strom auf 0A setzt, ohne die Spannung anzufassen.
# Arbiträrsignal-Aktionen brauchen ebenfalls keinen Wert im normalen Feld --
# ihre Parameter (Signalform, Amplitude, ...) kommen aus dem Definieren-Dialog
# (siehe signal_dialog.py) und liegen in den arb_*-Feldern von TestStep.
# CAN_SEND traegt seine Nutzdaten ebenfalls in eigenen Feldern (can_*, siehe
# TestStep) statt im normalen Wert-Feld -- eine CAN-ID + Datenbytes passen
# nicht in einen einzelnen float.
VALUELESS_ACTIONS = {
    "OUT_ON", "OUT_OFF", "PSU_OUT_ON", "PSU_OUT_OFF",
    "ARB", "PSU_ARB",
    "CAN_SEND",
    "HIL_OUT_ON", "HIL_OUT_OFF", "HIL_RELAY_ON", "HIL_RELAY_OFF",
    "HIL_IN_READ", "HIL_AIN_READ",
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
    "PSU_OUT_ON": ("", 0, 0),
    "PSU_OUT_OFF": ("", 0, 0),
    "ARB": ("", 0, 0),
    "PSU_ARB": ("", 0, 0),
    "CAN_SEND": ("", 0, 0),
    "HIL_OUT_ON": ("", 0, 0),
    "HIL_OUT_OFF": ("", 0, 0),
    "HIL_RELAY_ON": ("", 0, 0),
    "HIL_RELAY_OFF": ("", 0, 0),
    # 0-3300mV: AOUT_MAX_MV in microhil/driver.py (DAC-Referenzspannung).
    "HIL_AOUT": ("mV", 0, 3300),
    "HIL_IN_READ": ("", 0, 0),
    "HIL_AIN_READ": ("", 0, 0),
}

# Kontrollfluss-Schritttypen (Ablaufsteuerung) neben dem normalen
# Geraete-Aktionsschritt ("action"). "loop"/"while"/"if" eroeffnen einen
# Block, der durch ein zugehoeriges "end" geschlossen wird (siehe
# validate_structure()); "else" ist optional und nur innerhalb eines
# "if"-Blocks gueltig. "set_var"/"inc_var" setzen bzw. erhoehen eine
# Laufvariable, die in Bedingungen als cond_source=="variable" gelesen wird.
# "wait" wartet nur die in duration angegebene Zeitspanne, ohne Geraeteaktion.
STEP_TYPE_ACTION = "action"
CONTROL_STEP_TYPES = {"loop", "while", "if", "else", "end", "set_var", "inc_var", "wait"}
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
    "wait": "Warten",
}

COND_SOURCES = ("measurement", "time", "variable")
COND_FIELDS = ("voltage", "current", "power")
COND_OPS = ("<", "<=", ">", ">=", "==", "!=")
COND_OP_LABELS = {"<": "<", "<=": "≤", ">": ">", ">=": "≥", "==": "=", "!=": "≠"}
COND_TIME_REFS = ("block", "run")
COND_FIELD_UNITS = {"voltage": "V", "current": "A", "power": "W", "hil_ain": "mV", "hil_in": ""}
COND_FIELD_LABELS = {"voltage": "Spannung", "current": "Strom", "power": "Leistung"}

# check_field-Codes fuer HIL_AIN_READ/HIL_IN_READ (siehe HIL_READ_ACTIONS) --
# eigene Einheit/Symbol statt Spannung/Strom/Leistung, da eine Lese-Aktion
# nur EINEN Wert liefert (kein Auswahlfeld noetig, siehe testcase_tab.py:
# open_check_dialog()). Bewusst NICHT Teil von COND_FIELDS/COND_FIELD_LABELS
# (die sind fuer die while/if-Bedingungsauswahl aller Geraete, die HIL-Codes
# sollen dort nicht als Option auftauchen).
HIL_CHECK_FIELD_LABELS = {"hil_ain": "Analogwert", "hil_in": "Zustand (0/1)"}

# HIL-Lese-Aktionscode -> zugehoeriger check_field-Code (siehe
# HIL_CHECK_FIELD_LABELS/COND_FIELD_UNITS oben) -- fuer testcase_tab.py:
# open_check_dialog(), das aus dem gerade gewaehlten Aktionscode den
# passenden field_choices-Eintrag fuer CheckDialog bauen muss.
HIL_READ_CHECK_FIELD = {"HIL_AIN_READ": "hil_ain", "HIL_IN_READ": "hil_in"}

# Kurzsymbole fuer die kompakte Pruefungs-Zusammenfassung in der
# Testcase-Tabelle (siehe check_summary()) -- sprachunabhaengig, daher nicht
# uebersetzt. Die Messgroessen selbst sind dieselben wie bei Bedingungen
# (COND_FIELDS); die Leistung wird beim Netzteil aus U*I berechnet (siehe
# testcase_runner.on_psu_measurement).
CHECK_FIELD_SYMBOLS = {"voltage": "U", "current": "I", "power": "P", "hil_ain": "AIN", "hil_in": "IN"}

# Aktuelle Testablauf-Dateiversion (siehe save_steps/load_steps). Version 1
# war ein nacktes JSON-Array ohne Umschlag/Versionsnummer.
FILE_FORMAT_VERSION = 2

# Aktuelle Baustein-Dateiversion (siehe save_block/load_block). Eigener
# Zaehler statt FILE_FORMAT_VERSION, da Testablauf- und Baustein-Dateien
# unabhaengig voneinander weiterentwickelt werden koennen -- ein Baustein
# ist bewusst nur ein benannter Ausschnitt (steps + name), kein vollstaendiger
# Testablauf.
BLOCK_FILE_FORMAT_VERSION = 1


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
    # bei "wait" ist es die gesamte Wartezeit des Schritts selbst (es gibt
    # keine Aktion daneben); bei allen anderen Kontrollfluss-Schritten
    # (loop/while/if/else/end) wird sie ignoriert.
    duration: float = 0.0
    enabled: bool = True
    # -- Arbiträrsignal-Parameter (nur relevant wenn action in ARB_ACTIONS) --
    arb_shape: str = "sine"       # "sine" | "square" | "triangle" | "sawtooth"
    arb_target: str = ""          # tatsaechlich gesendeter Aktionscode, z.B. "VOLT"/"PSU_CURR"
    arb_amplitude: float = 0.0    # Signal schwingt zwischen offset-amplitude und offset+amplitude
    arb_offset: float = 0.0
    arb_frequency: float = 1.0    # Hz
    arb_interval_ms: int = 200    # Abstand zwischen zwei Sollwert-Updates
    arb_duty: float = 0.5         # nur "square": Tastgrad (Anteil High-Phase), 0..1

    # -- CAN-Frame-Parameter (nur relevant wenn action == "CAN_SEND") -----
    # Ein periodisches Senden (z.B. alle 100ms fuer die Dauer des Schritts)
    # braucht keine eigenen Felder -- laesst sich mit den vorhandenen
    # Kontrollfluss-Schritten (loop + wait um einen CAN_SEND-Schritt) bauen,
    # genau wie jede andere wiederholte Aktion auch.
    can_id: int = 0            # Arbitration-ID (11-bit Standard oder 29-bit Extended)
    can_data: str = ""         # Hex-String, z.B. "01 A2 FF" (max. 8 Bytes, klassisches CAN)
    can_extended: bool = False  # 29-bit Extended-ID statt 11-bit Standard-ID

    # -- microHIL-Parameter (nur relevant wenn device_kind == "hil") -----
    # 1-basierter Kanalindex, gueltiger Bereich je Aktion siehe
    # HIL_CHANNEL_COUNTS. Bei HIL_AOUT ist `value` (mV) der Sollwert; bei
    # HIL_IN_READ/HIL_AIN_READ hat `value` keine Bedeutung (siehe
    # HIL_READ_ACTIONS).
    hil_channel: int = 1
    # Nur relevant bei device_kind=="hil" und action in HIL_READ_ACTIONS:
    # Variablenname, in den der gelesene Wert zusaetzlich zur (optionalen)
    # Pass/Fail-Pruefung geschrieben wird (siehe testcase_runner.
    # on_action_completed). Leer = nicht speichern. Damit laesst sich eine
    # microHIL-Lesung als Bedingung in einem spaeteren while/if verwenden
    # (dort cond_source="variable" waehlen).
    store_var: str = ""

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

    # -- Pass/Fail-Pruefung (optional, nur Aktionsschritte) ---------------
    # Nach Ablauf der Wartezeit (bzw. nach dem Signalende bei ARB-Schritten)
    # wird die erste danach eintreffende Messung des Schritt-Geraets gegen
    # [check_min, check_max] geprueft (siehe testcase_runner._finish_step).
    check_enabled: bool = False
    check_field: str = "voltage"  # "voltage" | "current" | "power" (siehe COND_FIELDS)
    check_min: float = 0.0
    check_max: float = 0.0
    check_abort: bool = False     # True = Lauf bei Verletzung abbrechen (wie Geraetefehler)


def arb_value(step: "TestStep", t: float) -> float:
    """Momentanwert des Arbiträrsignals von `step` zum Zeitpunkt t (Sekunden)."""
    period = 1.0 / max(step.arb_frequency, 1e-9)
    # Phasenanteil innerhalb der aktuellen Periode, 0..1 -- Grundlage fuer
    # square/triangle/sawtooth, die alle stueckweise-lineare bzw. stufige
    # Kurven ueber genau diesen Anteil beschreiben.
    phase_frac = (t % period) / period
    if step.arb_shape == "square":
        raw = 1.0 if phase_frac < step.arb_duty else -1.0
    elif step.arb_shape == "triangle":
        # 0 -> 1 (erste Haelfte), 1 -> 0 (zweite Haelfte) linear, auf -1..1 skaliert.
        raw = (4.0 * phase_frac - 1.0) if phase_frac < 0.5 else (3.0 - 4.0 * phase_frac)
    elif step.arb_shape == "sawtooth":
        # Linearer Anstieg -1..1 ueber die Periode, danach Sprung zurueck auf -1.
        raw = 2.0 * phase_frac - 1.0
    else:
        raw = math.sin(2.0 * math.pi * phase_frac)
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


def check_summary(step: TestStep) -> str:
    """Anzeigetext einer Pass/Fail-Pruefung, z.B. "U: 11.8–12.2 V".

    Leer, wenn keine Pruefung aktiv ist. Bewusst sprachunabhaengig
    (Kurzsymbol + Einheit statt uebersetzter Messgroessen-Namen), damit die
    schmale Tabellenspalte nicht ueberlaeuft.
    """
    if not step.check_enabled:
        return ""
    symbol = CHECK_FIELD_SYMBOLS.get(step.check_field, step.check_field)
    unit = COND_FIELD_UNITS.get(step.check_field, "")
    return f"{symbol}: {step.check_min:g}–{step.check_max:g} {unit}".rstrip()


def save_steps(steps: list[TestStep], path: Path) -> None:
    payload = {
        "format": "labor-testcase",
        "version": FILE_FORMAT_VERSION,
        "steps": [asdict(step) for step in steps],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _steps_from_items(items: list[dict]) -> list[TestStep]:
    steps = []
    for item in items:
        if "device" in item and "device_kind" not in item:
            item = dict(item)
            legacy = item.pop("device")
            item["device_kind"] = _LEGACY_DEVICE_KIND.get(legacy, legacy)
            item.setdefault("device_id", "")
        steps.append(TestStep(**item))
    return steps


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
    return _steps_from_items(items)


def save_block(steps: list[TestStep], name: str, path: Path) -> None:
    """Speichert einen (typischerweise per SaveBlockDialog ausgewaehlten)
    Ausschnitt von Schritten als benannten, wiederverwendbaren Baustein --
    eigenes Dateiformat statt save_steps(), da ein Baustein zusaetzlich einen
    Namen traegt und kein vollstaendiger, eigenstaendig lauffaehiger
    Testablauf sein muss (z.B. ein einzelner Block-Rumpf ohne Start/Ende)."""
    payload = {
        "format": "labor-testcase-block",
        "version": BLOCK_FILE_FORMAT_VERSION,
        "name": name,
        "steps": [asdict(step) for step in steps],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_block(path: Path) -> tuple[str, list[TestStep]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = int(data.get("version", 0))
    if version > BLOCK_FILE_FORMAT_VERSION:
        raise ValueError(
            tr(
                "Baustein-Datei stammt aus einer neueren Programmversion "
                "(Format {version}) und kann nicht geladen werden.",
                version=version,
            )
        )
    name = str(data.get("name") or path.stem)
    return name, _steps_from_items(data.get("steps", []))


def kind_label(device_kind: str) -> str:
    return tr(DEVICE_KIND_LABELS.get(device_kind, device_kind))


def action_label(device_kind: str, action_code: str) -> str:
    base_label = DEVICE_ACTIONS[device_kind].get(action_code)
    return tr(base_label) if base_label is not None else action_code
