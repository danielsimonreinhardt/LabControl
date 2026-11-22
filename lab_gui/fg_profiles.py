"""Funktionsgeneratoren (Geraeteart "fg"): welcher Treiber gehoert zu einem Geraet (1.0.2).

Bis 1.0.1 gab es nur den JDS2915 (jds66xx). Seit 1.0.2 kommt der eingebaute Generator der UNI-T-
Oszilloskope mit "-S" dazu (unit_mso_awg). Beide sind dieselbe Geraeteart mit denselben Aktionen
(FG_*), denselben Signalen (device_worker.fg_state) und derselben Kachel; was sie unterscheidet
(Kanalzahl, Kurvenformen, Grenzen, Phase, Modulation, Lastwiderstand), sagt das Treibermodul. Dieses
Modul ordnet Geraet, Zustand und Geraete-ID dem Treibermodul zu.

Qt-frei (wie field_catalog.py): LiveState und die Netzwerk-Freigabe benutzen es ebenfalls.
"""
from __future__ import annotations

from jds66xx import driver as jds_driver
from unit_mso_awg import driver as awg_driver
from verbund_ids import split as split_device_id

MODULES = {jds_driver.PROFILE: jds_driver, awg_driver.PROFILE: awg_driver}
DEFAULT_MODULE = jds_driver

# FG_WAVE_*-Aktionscode (testcase_model.FG_ACTIONS) -> Kurvenform-Code. Die Codes gelten fuer beide
# Treiber (unit_mso_awg uebernimmt die des JDS2915; Dreieck = RAMP mit Symmetrie 50 %).
FG_WAVE_ACTIONS = {
    "FG_WAVE_SINE": jds_driver.WAVE_SINE,
    "FG_WAVE_SQUARE": jds_driver.WAVE_SQUARE,
    "FG_WAVE_PULSE": jds_driver.WAVE_PULSE,
    "FG_WAVE_TRIANGLE": jds_driver.WAVE_TRIANGLE,
    "FG_WAVE_DC": jds_driver.WAVE_DC,
}

# Modulation und Lastwiderstand (PI-88 b, nur Generatoren mit HAS_MODULATION/HAS_LOAD): Aktionscode ->
# (Parameter, fester Wert; None = der Wert der Aktion). Parameternamen wie unit_mso_awg.driver.PARAM_FIELDS.
FG_PARAM_ACTIONS: dict[str, tuple[str, object]] = {
    **{f"FG_MODE_{mode}": ("mode", mode) for mode in awg_driver.MODES},
    **{f"FG_MOD_WAVE_{wave}": ("mod_wave", wave) for wave in awg_driver.MOD_WAVES},
    "FG_MOD_FREQ": ("mod_freq", None),
    "FG_AM_DEPTH": ("am_depth", None),
    "FG_FM_DEV": ("fm_dev", None),
    "FG_FSK_RATE": ("fsk_rate", None),
    "FG_FSK_FREQ": ("fsk_freq", None),
    "FG_SWEEP_LIN": ("sweep_type", "LIN"),
    "FG_SWEEP_LOG": ("sweep_type", "LOG"),
    "FG_SWEEP_START": ("sweep_start", None),
    "FG_SWEEP_STOP": ("sweep_stop", None),
    "FG_SWEEP_TIME": ("sweep_time", None),
    "FG_LOAD": ("load", None),
}


# Rahmen fuer Wertfelder in Ablauf-Editor und Fernsteuerung (remote_actions, testcase_model): der weiteste
# beider Generatoren -- die genaue Grenze prueft der Treiber des jeweiligen Geraets.
MIN_FREQUENCY_HZ = min(jds_driver.MIN_FREQUENCY_HZ, awg_driver.MIN_FREQUENCY_HZ)
MAX_FREQUENCY_HZ = max(jds_driver.MAX_FREQUENCY_HZ, awg_driver.MAX_FREQUENCY_HZ)
MAX_AMPLITUDE_V = max(jds_driver.MAX_AMPLITUDE_V, awg_driver.MAX_AMPLITUDE_V)
MAX_OFFSET_V = max(jds_driver.MAX_OFFSET_V, awg_driver.MAX_OFFSET_V)
# Wertebereich je Modulations-/Lastaktion mit Wert: Code -> (Einheit, Minimum, Maximum)
FG_PARAM_VALUE_RANGE: dict[str, tuple[str, float, float]] = {
    code: (awg_driver.NUMERIC_PARAMS[field][1], awg_driver.NUMERIC_PARAMS[field][2],
           awg_driver.NUMERIC_PARAMS[field][3])
    for code, (field, fixed) in FG_PARAM_ACTIONS.items() if fixed is None
}
# Deutsche Basisnamen der Modulations-/Lastaktionen (testcase_model, remote_actions)
FG_PARAM_ACTION_LABELS: dict[str, str] = {
    **{f"FG_MODE_{mode}": f"Modulation {awg_driver.MODE_NAMES[mode]}" for mode in awg_driver.MODES},
    **{f"FG_MOD_WAVE_{wave}": f"Modulationsform {awg_driver.MOD_WAVE_NAMES[wave]}" for wave in awg_driver.MOD_WAVES},
    "FG_MOD_FREQ": "Modulationsfrequenz setzen",
    "FG_AM_DEPTH": "AM-Tiefe setzen",
    "FG_FM_DEV": "FM-Hub setzen",
    "FG_FSK_RATE": "Umtastrate setzen (ASK/FSK)",
    "FG_FSK_FREQ": "FSK-Sprungfrequenz setzen",
    "FG_SWEEP_LIN": "Wobbeln linear",
    "FG_SWEEP_LOG": "Wobbeln logarithmisch",
    "FG_SWEEP_START": "Wobbel-Startfrequenz setzen",
    "FG_SWEEP_STOP": "Wobbel-Stoppfrequenz setzen",
    "FG_SWEEP_TIME": "Wobbelzeit setzen",
    "FG_LOAD": "Lastwiderstand setzen",
}


def module_for(profile: str | None):
    return MODULES.get(profile or "", DEFAULT_MODULE)


def module_of_state(state: dict | None):
    """Treibermodul zu einem Zustand (device_worker.fg_state). Ohne "profile" der JDS2915 (so meldet er
    sich seit jeher)."""
    return module_for((state or {}).get("profile"))


def module_of_device(gen):
    """Treibermodul zu einem Treiberobjekt (auch Simulator). Ohne PROFILE der JDS2915."""
    return module_for(getattr(gen, "PROFILE", None))


def module_for_device_id(device_id: str):
    """Treibermodul aus der Geraete-ID (auch "@rechner/..."), fuer Pruefungen ohne Geraet (Ablauf-Editor)."""
    _host, local_id = split_device_id(device_id or "")
    return awg_driver if awg_driver.is_device_id(local_id) else DEFAULT_MODULE


def wave_action_for(code: int) -> str | None:
    return next((name for name, value in FG_WAVE_ACTIONS.items() if value == code), None)
