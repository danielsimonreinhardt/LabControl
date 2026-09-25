"""Gemeinsamer Katalog aller Messgroessen: Anzeigename und Einheit je
(Geraeteart, Feld), plus die bevorzugte Feldreihenfolge je Geraeteart.

Warum ueberhaupt ein eigenes Modul: die Netzwerk-Freigabe (siehe
share_api.py) laeuft in einem eigenen Thread und darf deshalb KEIN Qt und
kein Widget-Modul der App importieren -- sie braucht aber Namen und
Einheiten. Ohne dieses Modul muesste sie dashboard.py (71 kB Widgets samt
qtawesome) oder timeline_tab.py importieren, nur um zu erfahren, dass
Spannung in Volt gemessen wird. Dieses Modul importiert ausser den
Kanalzahl-Konstanten aus microhil/driver.py nichts.

Bewusster Haltepunkt: dashboard.FIELD_DEFS und recording.FIELD_INFO bleiben
unangetastet. Beide sind anzeige- bzw. exportseitig (dashboard: nur nach
field_key ohne Geraeteart, weil ein Panel seine Art ohnehin kennt;
recording: flaches Mapping fuer CSV-Spalten/MF4-Kanalnamen). Sie hier
mitzuziehen waere ein eigener, riskanterer Umbau ohne Nutzen fuer die
Netzwerk-Freigabe -- wer das spaeter angeht, faengt bei diesem Kommentar an.
"""
from __future__ import annotations

from microhil.driver import AIN_COUNT, IN_COUNT, OUT_COUNT, PWR12_COUNT, RELAY_COUNT

# -- Plotbare Signale (Verlauf-Tab) ---------------------------------------
# field_key -> (deutscher Basis-Anzeigename, Einheit); die Einheit ist
# sprachunabhaengig und wird NICHT ueber i18n.tr uebersetzt.
#
# Diese drei Dicts und KIND_FIELDS sind woertlich aus timeline_tab.py
# hierher gezogen und muessen genau so bleiben: der Verlauf-Tab plottet
# numerische Reihen, ein nicht-numerisches Feld wie "mode" wuerde dort als
# waehlbares Signal auftauchen und beim Zeichnen scheitern.
LOAD_SIGNAL_FIELDS = {
    "voltage": ("Spannung", "V"),
    "current": ("Strom", "A"),
    "power": ("Leistung", "W"),
}
PSU_SIGNAL_FIELDS = {
    "voltage": ("Spannung", "V"),
    "current": ("Strom", "A"),
}
# microHIL-Kanaele (BUGS_GESCHLOSSEN.md #33) -- Kanalzahlen aus
# microhil/driver.py statt hart verdrahtet, damit eine spaetere Aenderung
# dort nicht hier separat nachgezogen werden muss. Digitalkanaele (IN/OUT/
# REL) sind 0/1-Werte ohne Einheit.
HIL_SIGNAL_FIELDS = {
    **{f"ain{n}": (f"Analogeingang {n}", "mV") for n in range(1, AIN_COUNT + 1)},
    **{f"pwr12_{n}_current": (f"12V-Ausgang {n} Strom", "mA") for n in range(1, PWR12_COUNT + 1)},
    **{f"in{n}": (f"Digitaleingang {n}", "") for n in range(1, IN_COUNT + 1)},
    **{f"out{n}": (f"Digitalausgang {n}", "") for n in range(1, OUT_COUNT + 1)},
    **{f"rel{n}": (f"Relais {n}", "") for n in range(1, RELAY_COUNT + 1)},
}
KIND_FIELDS = {"load": LOAD_SIGNAL_FIELDS, "psu": PSU_SIGNAL_FIELDS, "hil": HIL_SIGNAL_FIELDS}

# -- Nicht plotbare Zusatzfelder ------------------------------------------
# Zustandsfelder, die es NUR ueber die Netzwerk-Freigabe und im Dashboard
# gibt, nicht im Verlauf-Tab (siehe KIND_FIELDS oben): Textwerte bzw.
# monoton steigende Zaehler, die als Kurve nichts hergeben.
LOAD_STATE_FIELDS = {"mode": ("Modus", "")}
PSU_STATE_FIELDS = {"mode": ("Modus", "")}
CAN_STATE_FIELDS = {
    "tx_count": ("Gesendet", ""),
    "rx_count": ("Empfangen", ""),
}
PICOSCOPE_STATE_FIELDS = {"status": ("Status", "")}
# Funktionsgenerator: je Kanal Ausgang (0/1), Wellenform (Text), Frequenz,
# Amplitude (Spitze-Spitze), Offset, Tastverhaeltnis; dazu die Phase. Nicht im
# Verlauf-Tab plotbar (siehe KIND_FIELDS) -- es sind Einstellungen, keine
# Messwerte.
FG_STATE_FIELDS = {
    **{f"out{n}": (f"Ausgang {n}", "") for n in (1, 2)},
    **{f"wave{n}": (f"Wellenform {n}", "") for n in (1, 2)},
    **{f"freq{n}": (f"Frequenz {n}", "Hz") for n in (1, 2)},
    **{f"ampl{n}": (f"Amplitude {n}", "V") for n in (1, 2)},
    **{f"offs{n}": (f"Offset {n}", "V") for n in (1, 2)},
    **{f"duty{n}": (f"Tastverhältnis {n}", "%") for n in (1, 2)},
    "phase": ("Phase", "°"),
}

# -- Flacher Gesamtkatalog fuer die Netzwerk-Freigabe ---------------------
# (kind, field) -> (deutscher Basis-Anzeigename, Einheit). Anders als
# dashboard.FIELD_DEFS zusaetzlich nach Geraeteart geschluesselt: "voltage"
# heisst bei Last und Netzteil zwar gleich, aber ein Feldname ist nur
# zusammen mit der Geraeteart eindeutig (vgl. "mode", das bei der Last aus
# LOAD_MODE_SHORT kommt und beim Netzteil aus dem CC/CV-Flag).
FIELD_DEFS: dict[tuple[str, str], tuple[str, str]] = {
    **{("load", f): v for f, v in {**LOAD_SIGNAL_FIELDS, **LOAD_STATE_FIELDS}.items()},
    **{("psu", f): v for f, v in {**PSU_SIGNAL_FIELDS, **PSU_STATE_FIELDS}.items()},
    **{("can", f): v for f, v in CAN_STATE_FIELDS.items()},
    **{("hil", f): v for f, v in HIL_SIGNAL_FIELDS.items()},
    **{("picoscope", f): v for f, v in PICOSCOPE_STATE_FIELDS.items()},
    **{("fg", f): v for f, v in FG_STATE_FIELDS.items()},
}

# Anzeigereihenfolge je Geraeteart -- bestimmt, in welcher Reihenfolge die
# Felder in der JSON-Antwort und auf dem ESP32-Display stehen. Bewusst
# dieselbe Reihenfolge wie die Dashboard-Kacheln (dashboard.LOAD_FIELD_KEYS
# etc.), damit ein externes Display und die Kachel daneben gleich aussehen.
KIND_FIELD_ORDER: dict[str, list[str]] = {
    "load": ["voltage", "current", "power", "mode"],
    "psu": ["voltage", "current", "mode"],
    "can": ["tx_count", "rx_count"],
    "hil": list(HIL_SIGNAL_FIELDS),
    "picoscope": ["status"],
    "fg": [f"{k}{n}" for n in (1, 2) for k in ("out", "wave", "freq", "ampl", "offs", "duty")] + ["phase"],
}

# Last-Funktionscode -> kompakte Anzeige. get_function() liefert auf echter
# Hardware bereits die Kurzform (CC/CV/CR/CW, siehe korad_kel102/README.md
# "Bekannte Eigenheiten"), MockKoradKEL102 dagegen den SET-Code aus
# korad_kel102.driver.FUNCTIONS (CURR/VOLT/RES/POW) -- beide Formate werden
# hier auf dieselbe Anzeige gemappt.
LOAD_MODE_SHORT: dict[str, str] = {
    "CURR": "CC", "CC": "CC",
    "VOLT": "CV", "CV": "CV",
    "RES": "CR", "CR": "CR",
    "POW": "CW", "CW": "CW",
    "SHORT": "SHORT",
}


def field_info(kind: str, field: str) -> tuple[str, str]:
    """(deutscher Basisname, Einheit) fuer EIN Feld, mit Fallback auf den
    rohen Feldnamen ohne Einheit.

    Fallback statt KeyError, weil die Netzwerk-Freigabe den Katalog aus dem
    Server-Thread liest: ein unbekanntes Feld (z.B. nach einem Treiber-
    Update, das ein neues Signal emittiert, bevor es hier eingetragen ist)
    soll eine etwas magere Antwort liefern statt die Anfrage abzubrechen.
    """
    return FIELD_DEFS.get((kind, field), (field, ""))


def field_order(kind: str, present) -> list[str]:
    """Feldreihenfolge einer Kachel: Katalogreihenfolge zuerst, danach alles,
    was das Geraet sonst noch gemeldet hat.

    Der Anhang ist Absicht: meldet ein Treiber nach einem Update ein Feld,
    das hier noch nicht eingetragen ist, taucht es hinten auf statt still zu
    verschwinden (gleiches Motiv wie der Fallback in field_info).
    """
    ordered = [f for f in KIND_FIELD_ORDER.get(kind, []) if f in present]
    extra = [f for f in present if f not in ordered]
    return ordered + sorted(extra)
