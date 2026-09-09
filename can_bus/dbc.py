"""Optionale DBC-Signal-Decodierung fuer empfangene CAN-Frames.

Ergaenzt (ersetzt NICHT) die Rohdaten-Anzeige in control_tab.CanControlGroup:
pro konfiguriertem CAN-Interface laesst sich im Einstellungen-Tab optional
eine DBC-Datei hinterlegen (settings.py::can_configs, Schluessel
"dbc_path"). Ist eine gesetzt, decodiert dieses Modul jeden empfangenen
CanFrame zusaetzlich zu den weiterhin unveraendert angezeigten Rohdaten
(Hex-ID + Hex-Bytes) in benannte, skalierte Signale.

Bewusst ein eigenstaendiges Modul (nicht Teil von driver.py) -- Decodierung
ist unabhaengig vom Interface-Typ (vector/pcan/..., siehe INTERFACE_LIST)
und arbeitet ausschliesslich auf bereits empfangenen CanFrame-Objekten,
siehe FEATURES.md Punkt 3. Basiert auf `cantools` (liest DBC-Dateien, siehe
requirements.txt) -- das im python-can-Oekosystem uebliche Werkzeug dafuer.

Architektur-Hinweis fuer einen spaeteren Ausbau (FEATURES.md: While/If-
Testablauf-Bausteine sollen decodierte CAN-Signale direkt als
Bedingungsquelle anbieten -- hier bewusst NICHT umgesetzt, siehe dort):
decode_frame() liefert eine reine, Qt-freie Datenstruktur (DecodedFrame/
DecodedSignal) zurueck, kein GUI-gebundenes Format. device_worker.
DeviceWorker reicht sie unveraendert per eigenem Signal (can_signals_decoded)
an die GUI weiter, sodass ein spaeterer zusaetzlicher Abnehmer (z.B.
testcase_runner fuer Solange/Wenn-Bedingungen) sich dort einklinken kann,
ohne dieses Modul aendern zu muessen.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cantools
from cantools.database import Database
from cantools.database.errors import DecodeError

from can_bus.driver import CanFrame

logger = logging.getLogger(__name__)


class DbcError(RuntimeError):
    """DBC-Datei konnte nicht geladen werden (fehlende/kaputte Datei,
    Parse-Fehler o.ae.)."""


@dataclass
class DecodedSignal:
    name: str
    value: object  # int | float | str -- str bei DBC-Value-Tables (VAL_-Enum-Choices)
    unit: str | None


@dataclass
class DecodedFrame:
    message_name: str
    signals: list[DecodedSignal]


def load_dbc(path: str | Path) -> Database:
    """Laedt+parst eine DBC-Datei.

    Wirft die app-eigene DbcError statt der rohen cantools-Exception --
    cantools wirft je nach Fehlerursache uneinheitliche Typen (z.B. OSError
    bei fehlender Datei, cantools-eigene ParseError bei kaputtem Inhalt),
    analog zur breit gefangenen CanConnectionError in can_bus/driver.py.
    """
    try:
        return cantools.database.load_file(str(path))
    except Exception as exc:
        raise DbcError(f"DBC-Datei '{path}' konnte nicht geladen werden: {exc}") from exc


def decode_frame(db: Database, frame: CanFrame) -> DecodedFrame | None:
    """Decodiert EINEN empfangenen CanFrame anhand einer bereits geladenen
    DBC-Datenbank.

    Liefert None, wenn die DBC-Datei keine Message-Definition fuer die
    arbitration_id des Frames enthaelt, oder wenn die Nutzdaten trotz
    passender Message-Definition nicht decodierbar sind (z.B. zu wenige
    Datenbytes) -- beides KEIN Fehlerfall im Sinne einer Exception: eine
    DBC-Datei deckt ueblicherweise nur einen Teil des tatsaechlichen
    Bus-Traffics ab, unbekannte IDs sind der Normalfall. Aufrufer zeigen in
    diesem Fall weiterhin nur die Rohdaten (Hex-ID + Hex-Bytes), wie ohne
    hinterlegte DBC-Datei auch.
    """
    try:
        message = db.get_message_by_frame_id(frame.arbitration_id, force_extended_id=frame.extended)
    except KeyError:
        return None
    try:
        # allow_truncated=True: ein DLC-Mismatch (z.B. Testframe kuerzer als
        # die DBC-Definition erwartet) soll nicht die gesamte Anzeige
        # verhindern, betroffene Signale fehlen dann einfach im Ergebnis.
        decoded = message.decode(frame.data, decode_choices=True, scaling=True, allow_truncated=True)
    except DecodeError as exc:
        logger.debug(
            "CAN-Frame 0x%X konnte trotz passender DBC-Message '%s' nicht decodiert werden: %s",
            frame.arbitration_id, message.name, exc,
        )
        return None
    signals: list[DecodedSignal] = []
    for name, value in decoded.items():
        try:
            unit = message.get_signal_by_name(name).unit
        except KeyError:
            unit = None
        signals.append(DecodedSignal(name=name, value=value, unit=unit))
    return DecodedFrame(message_name=message.name, signals=signals)
