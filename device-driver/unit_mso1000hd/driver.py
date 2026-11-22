"""UNI-T MSO1000HD (z.B. MSO1154HD-S) -- SCPI über VISA.

Grundlage: UNI-T "MSO1000HD Series Programming Manual" V1.0 (Dezember 2024,
Software 1.00.0009). Verbindung über pyvisa:

- LAN (empfohlen): VXI-11, Ressource ``TCPIP0::<ip>::inst0::INSTR``. Läuft mit
  dem reinen Python-Backend pyvisa-py, braucht also kein NI-VISA -- auch nicht
  in der gebauten .exe.
- USB (USBTMC): nur mit installiertem VISA (NI-VISA, ``@ivi``). pyvisa-py
  könnte USB über libusb, das braucht unter Windows aber einen WinUSB-Treiber
  (Zadig) und ist hier bewusst nicht der Weg.

Der Treiber bleibt "dumm" (synchron, blockierend, ein Aufrufer-Thread) und
kennt nur Gerätebefehle. Die Übersetzung aus der herstellerneutralen
Erfassungsanfrage macht scope_api/unit_mso_adapter.py.

STAND 1.0.2: am MSO1154HD-S (Firmware V1.01.0081) über LAN geprüft. Abweichungen
vom Handbuch, die das Gerät zeigte, stehen in README.md ("Am Gerät geprüft").
"""
from __future__ import annotations

import ipaddress
import re
import time
from dataclasses import dataclass

import numpy as np

KIND = "scpiscope"
# Werkseitig: 4 Kanäle; Typen mit 2 Kanälen (MSO1xx2) gibt es laut Namensschema
# ebenfalls, siehe parse_model().
MAX_CHANNELS = 4
# :WAVeform:POINts ist die Stückgröße je Lesevorgang (Handbuch S. 192: 25 000). Am Gerät
# gehen bis 1 000 000 je Stück (darüber begrenzt es); die Gesamtlänge meldet
# :WAVeform:STOP? nach dem Setzen der Quelle (5M-Speicher: 5 000 000, als WORD in 1,2 s).
READ_CHUNK = 1_000_000
DEFAULT_TIMEOUT_S = 5.0
# 1 Mio. Punkte als ASCII ~16 MB -- großzügig.
READ_TIMEOUT_S = 60.0
# Am Gerät: direkt nach STOP liefert :WAVeform:DATA? (RAW) noch unfertige Daten
# (Füllwert, Rohwert 0x0404). Nach 0,5 s waren sie bei 25K und 250K vollständig.
DATA_SETTLE_S = 0.6
# VXI-11: das Gerät meldet maxRecvSize 1024 und beantwortet ein device_read, dessen
# requestSize kleiner als die ganze Antwort ist, mit einem Fehler (statt Teilstücken).
# pyvisa-py fragt mit min(Anzahl, maxRecvSize) an -- deshalb hier groß.
VXI11_READ_SIZE = 64 * 1024 * 1024
SIM_RESOURCE = "SIM"
SIM_DEVICE_ID = f"{KIND}:SIM"


class MSO1000HDError(RuntimeError):
    """Verbindung oder Gerät gestört (Verbindung gilt als tot)."""


class MSO1000HDValueError(MSO1000HDError):
    """Gerät hat einen Wert/Befehl abgelehnt, die Verbindung lebt noch."""


@dataclass(frozen=True)
class IdnInfo:
    vendor: str
    model: str
    serial: str
    firmware: str


@dataclass(frozen=True)
class ModelInfo:
    channels: int
    bandwidth_hz: float


@dataclass(frozen=True)
class Preamble:
    """:WAVeform:PREamble? (Handbuch S. 195). Felder in der Reihenfolge des
    Handbuchs; nicht jedes wird ausgewertet (siehe unit_mso_adapter)."""
    fmt: str
    acq_type: str
    points: int
    count: int
    xinc: float
    xorigin: float
    xref: float
    yinc: float
    yorigin: float
    yref: float


def parse_model(model: str) -> ModelInfo:
    """MSO1154HD-S -> 4 Kanäle, 150 MHz; MSO1254HD -> 4 Kanäle, 250 MHz.

    Namensschema MSO1<BB><K>: BB = Bandbreite in 10 MHz, K = Kanalzahl.
    Unbekanntes Schema: 4 Kanäle, 150 MHz (kleinstes 4-Kanal-Modell)."""
    m = re.search(r"MSO1(\d\d)(\d)", model.upper())
    if not m:
        return ModelInfo(channels=MAX_CHANNELS, bandwidth_hz=150e6)
    channels = int(m.group(2))
    if channels not in (2, 4):
        channels = MAX_CHANNELS
    return ModelInfo(channels=channels, bandwidth_hz=int(m.group(1)) * 10e6)


# -- Adressen und Geräte-IDs -----------------------------------------------------


def normalize_resource(address: str) -> str:
    """Nutzereingabe -> VISA-Ressource. Eine bloße IP-Adresse oder ein Hostname
    wird zu ``TCPIP0::<host>::inst0::INSTR`` (VXI-11, wie im Handbuch S. 324),
    eine vollständige VISA-Adresse bleibt, wie sie ist."""
    text = address.strip()
    if not text:
        raise ValueError("Leere Adresse")
    if text.upper() == SIM_RESOURCE:
        return SIM_RESOURCE
    if "::" in text:
        return text
    return f"TCPIP0::{text}::inst0::INSTR"


def device_id_for(resource: str) -> str:
    """Stabile Geräte-ID aus der Ressource. LAN: die Adresse (darum im Netz eine
    feste IP oder DHCP-Reservierung vergeben), USB: die Seriennummer aus der
    VISA-Adresse."""
    if resource == SIM_RESOURCE:
        return SIM_DEVICE_ID
    parts = resource.split("::")
    head = parts[0].upper()
    if head.startswith("TCPIP") and len(parts) >= 2:
        return f"{KIND}:{parts[1]}"
    if head.startswith("USB") and len(parts) >= 4:
        return f"{KIND}:{parts[3]}"
    return f"{KIND}:{resource.replace('::', '_')}"


def is_valid_address(address: str) -> bool:
    text = address.strip()
    if not text:
        return False
    if "::" in text or text.upper() == SIM_RESOURCE:
        return True
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", text))


# Geräte-ID -> VISA-Ressource. Füllt der DeviceWorker aus den Einstellungen,
# liest der ScopeService (anderer Thread) beim Öffnen. Einfache dict-Zugriffe,
# unter dem GIL atomar.
_RESOURCES: dict[str, str] = {SIM_DEVICE_ID: SIM_RESOURCE}


def register_resource(device_id: str, resource: str) -> None:
    _RESOURCES[device_id] = resource


def resource_for(device_id: str) -> str | None:
    return _RESOURCES.get(device_id)


# -- Transport --------------------------------------------------------------------


class VisaTransport:
    """Dünne Hülle um eine pyvisa-Ressource: Zeilen schreiben/lesen, Bytes
    genau lesen. Jeden pyvisa-/OS-Fehler in MSO1000HDError übersetzen."""

    def __init__(self, resource: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        try:
            import pyvisa
        except ImportError as exc:  # pragma: no cover -- requirements.txt
            raise MSO1000HDError("pyvisa ist nicht installiert (requirements.txt).") from exc
        self._errors: tuple = (pyvisa.Error, OSError, ValueError)
        # LAN immer über pyvisa-py (kein NI-VISA nötig). USB braucht ein
        # installiertes VISA ("@ivi").
        backend = "@py" if resource.upper().startswith("TCPIP") else "@ivi"
        try:
            self._rm = pyvisa.ResourceManager(backend)
            # open_timeout: ohne begrenzt pyvisa-py den Verbindungsaufbau nicht (am UNI-T hing ein
            # Versuch auf Port 5025, den das Gerät unbeantwortet lässt, 10 s -- Erkennung, 1.0.2).
            self._res = self._rm.open_resource(resource, open_timeout=int(timeout_s * 1000))
            self._res.timeout = int(timeout_s * 1000)
            self._res.read_termination = "\n"
            self._res.write_termination = "\n"
            self._res.chunk_size = VXI11_READ_SIZE
            if backend == "@py":
                _widen_vxi11_reads(self._res)
        except self._errors as exc:
            raise MSO1000HDError(f"{resource} lässt sich nicht öffnen: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 -- z.B. VisaIOError-Unterklassen aus dem Backend
            raise MSO1000HDError(f"{resource} lässt sich nicht öffnen: {exc}") from exc

    def set_timeout(self, seconds: float) -> None:
        self._res.timeout = int(seconds * 1000)

    def write(self, command: str) -> None:
        try:
            self._res.write(command)
        except Exception as exc:  # noqa: BLE001
            raise MSO1000HDError(f"Senden von {command!r} gescheitert: {exc}") from exc

    def read_line(self) -> str:
        try:
            return self._res.read().strip()
        except Exception as exc:  # noqa: BLE001
            raise MSO1000HDError(f"Lesen gescheitert: {exc}") from exc

    def read_raw(self) -> bytes:
        """Eine ganze Antwort (bis END), ohne Abschlusszeichen-Suche. Das Gerät
        liefert eine Antwort nur am Stück (siehe VXI11_READ_SIZE)."""
        try:
            return bytes(self._res.read_raw())
        except Exception as exc:  # noqa: BLE001
            raise MSO1000HDError(f"Lesen gescheitert: {exc}") from exc

    def close(self) -> None:
        for obj in (getattr(self, "_res", None), getattr(self, "_rm", None)):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:  # noqa: BLE001 -- Aufräumen darf nie scheitern
                pass


# -- Gerät ------------------------------------------------------------------------


class MSO1000HD:
    """Ein Oszilloskop der Reihe MSO1000HD. ``transport`` liefert write/read_line/
    read_raw/set_timeout/close -- echt (VisaTransport) oder simuliert
    (mock.SimulatedInstrument)."""

    def __init__(self, transport, data_settle_s: float = DATA_SETTLE_S) -> None:
        self._t = transport
        self._data_settle_s = data_settle_s

    @classmethod
    def open(cls, resource: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> "MSO1000HD":
        if resource == SIM_RESOURCE:
            from unit_mso1000hd.mock import SimulatedInstrument
            return cls(SimulatedInstrument(), data_settle_s=0.0)
        return cls(VisaTransport(resource, timeout_s))

    def close(self) -> None:
        self._t.close()

    def __enter__(self) -> "MSO1000HD":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- Grundbausteine -------------------------------------------------------

    def write(self, command: str) -> None:
        self._t.write(command)

    def query(self, command: str) -> str:
        self._t.write(command)
        return self._t.read_line()

    def query_float(self, command: str) -> float:
        text = self.query(command)
        try:
            return float(text.split(",")[0])
        except ValueError as exc:
            raise MSO1000HDValueError(f"{command}: unerwartete Antwort {text!r}") from exc

    def query_block(self, command: str) -> bytes:
        """Datenblock ``#9XXXXXXXXX<Daten>`` (Handbuch S. 5). Die Ziffer nach ``#``
        nennt die Länge der Längenangabe -- nicht fest 9 angenommen. Am Gerät folgt
        hinter dem Block noch ``,\\r\\n`` (ASCII) bzw. ``\\r\\n``; das wird verworfen."""
        self._t.write(command)
        raw = self._t.read_raw()
        data = _block_payload(raw)
        if data is None:
            raise MSO1000HDValueError(f"{command}: kein Datenblock, Antwort {raw[:80]!r}")
        return data

    # -- Identität / Fehler --------------------------------------------------------

    def idn(self) -> IdnInfo:
        """*IDN? (S. 6): Hersteller, Modell, Seriennummer, Software -- durch
        Komma getrennt (das Handbuch sagt "dot", das Beispiel zeigt Kommas)."""
        text = self.query("*IDN?")
        parts = [p.strip() for p in text.split(",")]
        if len(parts) < 2 or "UNI" not in parts[0].upper():
            raise MSO1000HDError(f"Kein UNI-T-Oszilloskop: *IDN? -> {text!r}")
        parts += [""] * (4 - len(parts))
        return IdnInfo(vendor=parts[0], model=parts[1], serial=parts[2], firmware=parts[3])

    def pop_error(self) -> tuple[int, str]:
        """:SYSTem:ERRor? -> (Code, Text). Code 0 = kein Fehler.
        HARDWARE-PRÜFUNG: Das Handbuch zeigt nur "-113" als Beispiel."""
        text = self.query(":SYSTem:ERRor?")
        head, _, message = text.partition(",")
        try:
            code = int(float(head))
        except ValueError:
            return (0, "") if not text or "no error" in text.lower() else (-1, text)
        return code, message.strip().strip('"')

    def clear_errors(self) -> None:
        self.write(":SYSTem:ERRor")

    # -- Einstellungen ------------------------------------------------------------

    def set_channel(self, n: int, display: bool, coupling: str = "DC", probe: float = 1.0,
                    scale_v: float | None = None, offset_v: float | None = None) -> None:
        self.write(f":CHANnel{n}:DISPlay {'ON' if display else 'OFF'}")
        if not display:
            return
        self.write(f":CHANnel{n}:COUPling {coupling}")
        self.write(f":CHANnel{n}:BWLimit FULL")
        self.write(f":CHANnel{n}:INVert OFF")
        self.write(f":CHANnel{n}:UNITs VOLTs")
        self.write(f":CHANnel{n}:PROBe {probe:g}X")
        if scale_v is not None:
            self.write(f":CHANnel{n}:SCALe {scale_v:.6g}")
        if offset_v is not None:
            self.write(f":CHANnel{n}:OFFSet {offset_v:.6g}")

    def channel_scale(self, n: int) -> float:
        return self.query_float(f":CHANnel{n}:SCALe?")

    def channel_offset(self, n: int) -> float:
        return self.query_float(f":CHANnel{n}:OFFSet?")

    def channel_probe(self, n: int) -> float:
        return self.query_float(f":CHANnel{n}:PROBe?")

    def set_timebase(self, scale_s: float, offset_s: float) -> None:
        self.write(":TIMebase:TYPe YT")
        self.write(":TIMebase:ROLL OFF")
        self.write(":TIMebase:EXTend:ENABle OFF")
        self.write(f":TIMebase:SCALe {scale_s:.6g}")
        self.write(f":TIMebase:OFFSet {offset_s:.6g}")

    def timebase_scale(self) -> float:
        return self.query_float(":TIMebase:SCALe?")

    def timebase_offset(self) -> float:
        return self.query_float(":TIMebase:OFFSet?")

    def set_acquisition(self, depth: str) -> None:
        self.write(":ACQuire:TYPe NORMal")
        self.write(f":ACQuire:MEMory:DEPTh {depth}")

    def set_edge_trigger(self, source_n: int, level_v: float, rising: bool, sweep: str) -> None:
        self.write(":TRIGger:MODe EDGe")
        self.write(":TRIGger:COUPling DC")
        self.write(f":TRIGger:EDGe:SOURce CHANnel{source_n}")
        self.write(f":TRIGger:EDGe:POLarity {'POSitive' if rising else 'NEGative'}")
        self.write(f":TRIGger:EDGe:LEVel {level_v:.6g}")
        self.write(f":TRIGger:SWEep {sweep}")

    # -- Ablauf -------------------------------------------------------------------

    def run(self) -> None:
        self.write(":RUN")

    def stop(self) -> None:
        self.write(":STOP")

    def force_trigger(self) -> None:
        self.write(":TRIGger:FORCe")

    def trigger_status(self) -> str:
        """STOP/ARMED/READY/TRIGED/AUTO/SCAN/RESET/REPLAY/WAIT (S. 68)."""
        return self.query(":TRIGger:STATus?").strip().upper()

    # -- Daten --------------------------------------------------------------------

    def preamble(self) -> Preamble:
        # Am Gerät eine Textzeile, laut Handbuch ein Datenblock -- beides annehmen.
        self._t.write(":WAVeform:PREamble?")
        answer = self._t.read_raw()
        block = _block_payload(answer)
        raw = (block if block is not None else answer).decode("ascii", "replace").strip()
        fields = [f.strip() for f in raw.replace("\n", ",").split(",") if f.strip()]
        if len(fields) < 10:
            raise MSO1000HDValueError(f"Preamble unvollständig: {raw[:120]!r}")
        try:
            return Preamble(
                fmt=fields[0], acq_type=fields[1], points=int(float(fields[2])), count=int(float(fields[3])),
                xinc=float(fields[4]), xorigin=float(fields[5]), xref=float(fields[6]),
                yinc=float(fields[7]), yorigin=float(fields[8]), yref=float(fields[9]),
            )
        except ValueError as exc:
            raise MSO1000HDValueError(f"Preamble nicht lesbar: {raw[:120]!r}") from exc

    def wait_data_ready(self) -> None:
        """Nach STOP warten, bis :WAVeform:DATA? vollständige Rohdaten liefert
        (am Gerät sonst Füllwerte, siehe DATA_SETTLE_S)."""
        if self._data_settle_s > 0:
            time.sleep(self._data_settle_s)

    def read_waveform_volts(self, n: int, progress=None, fmt: str = "WORD") -> tuple[np.ndarray, Preamble]:
        """Rohdaten eines Kanals aus dem internen Speicher in Volt.

        Format WORD (2 Byte je Punkt, little endian): am Gerät 250 000 Punkte in
        0,05 s statt 1,6 s als ASCII, und Punkt für Punkt gleich den ASCII-Werten
        (geprüft bei 0,2/0,5/1 V/div, Offset 0/-1,5/-3 V). Umrechnung -- NICHT wie im
        Handbuch (S. 193, "+ yorigin" in Volt), YORigin ist in Zählwerten:
            V = (Zählwert - YREFerence - YORigin) * YINCrement
        fmt="ASCII" bleibt als Rückfall (Volt direkt vom Gerät). Das Gerät muss
        gestoppt sein (und wait_data_ready() gelaufen).

        Am Gerät geprüft: im Modus RAW liefert ein DATA? höchstens :WAVeform:POINts
        Punkte (ohne Setzen nur 1), die Gesamtlänge steht in :WAVeform:STOP?; STARt
        zählt ab 0, STOP ist exklusiv; nach jedem DATA? rückt das Gerät STARt weiter --
        deshalb STARt/STOP vor jedem Stück neu.
        """
        fmt = fmt.upper()
        if fmt not in ("WORD", "ASCII"):
            raise ValueError(f"Format {fmt!r}: WORD oder ASCII")
        self.write(":WAVeform:MODE RAW")
        self.write(f":WAVeform:FORMat {fmt}")
        self.write(f":WAVeform:SOURce CHANnel{n}")
        total = int(self.query_float(":WAVeform:STOP?"))   # SOURce setzt STOP auf die Speicherlänge
        self.write(f":WAVeform:POINts {READ_CHUNK}")
        pre = self.preamble()
        total = total or pre.points
        if total <= 1:
            raise MSO1000HDValueError(f"Kanal {n}: Gerät meldet keine Punkte im Speicher.")
        chunks: list[np.ndarray] = []
        self._t.set_timeout(READ_TIMEOUT_S)
        try:
            start = 0
            while start < total:
                stop = min(total, start + READ_CHUNK)
                self.write(f":WAVeform:STARt {start}")
                self.write(f":WAVeform:STOP {stop}")
                data = self.query_block(":WAVeform:DATA?")
                values = _parse_word_values(data, pre) if fmt == "WORD" else _parse_ascii_values(data)
                if values.size == 0:
                    raise MSO1000HDValueError(f"Kanal {n}: leerer Datenblock bei {start}..{stop}.")
                chunks.append(values)
                start += values.size
                if progress is not None:
                    progress(start, total)
        finally:
            self._t.set_timeout(DEFAULT_TIMEOUT_S)
        return np.concatenate(chunks)[:total], pre

    # -- Bequemlichkeit für CLI/Tests ----------------------------------------------

    def wait_status(self, wanted: set[str], timeout_s: float, poll_s: float = 0.02) -> str | None:
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.trigger_status()
            if status in wanted:
                return status
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll_s)


def _block_payload(raw: bytes) -> bytes | None:
    """Nutzdaten eines ``#<n><Länge><Daten>``-Blocks, None wenn keiner."""
    if raw[:1] != b"#" or not raw[1:2].isdigit():
        return None
    digits = int(raw[1:2])
    try:
        length = int(raw[2:2 + digits]) if digits else 0
    except ValueError:
        return None
    data = raw[2 + digits:2 + digits + length]
    if len(data) < length:
        raise MSO1000HDError(f"Datenblock unvollständig: {len(data)} von {length} Bytes.")
    return data


def _widen_vxi11_reads(resource) -> None:
    """pyvisa-py: Anfragegröße je device_read über die vom Gerät gemeldete
    maxRecvSize (1024) hinaus anheben (siehe VXI11_READ_SIZE). Schreibzugriffe
    bleiben klein (einzelne Befehle), das Gerät nimmt sie an."""
    try:
        session = resource.visalib.sessions[resource.session]
        if hasattr(session, "max_recv_size"):
            session.max_recv_size = VXI11_READ_SIZE
    except Exception:  # noqa: BLE001 -- anderes Backend: nichts zu tun
        pass


def _parse_word_values(data: bytes, pre: Preamble) -> np.ndarray:
    """WORD-Block (int16, little endian) in Volt, siehe read_waveform_volts."""
    if len(data) % 2:
        raise MSO1000HDValueError(f"WORD-Block mit ungerader Länge ({len(data)} Bytes).")
    counts = np.frombuffer(data, dtype="<i2").astype(float)
    return (counts - pre.yref - pre.yorigin) * pre.yinc


def _parse_ascii_values(data: bytes) -> np.ndarray:
    text = data.decode("ascii", "replace").replace("\n", ",").replace(";", ",")
    try:
        return np.array([float(v) for v in text.split(",") if v.strip()], dtype=float)
    except ValueError as exc:
        raise MSO1000HDValueError(f"Wellenformdaten nicht lesbar: {text[:80]!r}") from exc


def probe(resource: str, timeout_s: float = 2.0) -> IdnInfo:
    """Öffnen, *IDN?, schließen -- für die Erkennung im DeviceWorker."""
    scope = MSO1000HD.open(resource, timeout_s)
    try:
        return scope.idn()
    finally:
        scope.close()
