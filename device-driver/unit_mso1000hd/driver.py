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

STAND: nur gegen den Mock (mock.py) geprüft, das Gerät war beim Schreiben noch
nicht da. Jede Stelle, an der das Handbuch mehrdeutig ist, ist mit
``HARDWARE-PRÜFUNG`` markiert und in README.md gesammelt.
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
# Handbuch S. 192: aus dem internen Speicher höchstens 25 000 Punkte je Lesevorgang.
READ_CHUNK = 25_000
DEFAULT_TIMEOUT_S = 5.0
# Ein Lesevorgang mit 25 000 Punkten als ASCII sind ~350 kB -- großzügig.
READ_TIMEOUT_S = 20.0
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
            self._res = self._rm.open_resource(resource)
            self._res.timeout = int(timeout_s * 1000)
            self._res.read_termination = "\n"
            self._res.write_termination = "\n"
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

    def read_exact(self, count: int) -> bytes:
        try:
            return bytes(self._res.read_bytes(count))
        except Exception as exc:  # noqa: BLE001
            raise MSO1000HDError(f"Lesen von {count} Bytes gescheitert: {exc}") from exc

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
    read_exact/set_timeout/close -- echt (VisaTransport) oder simuliert
    (mock.SimulatedInstrument)."""

    def __init__(self, transport) -> None:
        self._t = transport

    @classmethod
    def open(cls, resource: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> "MSO1000HD":
        if resource == SIM_RESOURCE:
            from unit_mso1000hd.mock import SimulatedInstrument
            return cls(SimulatedInstrument())
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
        """Datenblock ``#9XXXXXXXXX<Daten>\\n`` (Handbuch S. 5). Die Ziffer nach
        ``#`` nennt die Länge der Längenangabe -- nicht fest 9 angenommen."""
        self._t.write(command)
        head = self._t.read_exact(2)
        if head[:1] != b"#" or not head[1:2].isdigit():
            rest = head + self._read_rest_of_line()
            raise MSO1000HDValueError(f"{command}: kein Datenblock, Antwort {rest[:80]!r}")
        digits = int(head[1:2])
        length = int(self._t.read_exact(digits)) if digits else 0
        data = self._t.read_exact(length) if length else b""
        # Abschließendes \n (falls vorhanden) verwerfen.
        self._t.read_exact(1)
        return data

    def _read_rest_of_line(self) -> bytes:
        try:
            return self._t.read_line().encode()
        except MSO1000HDError:
            return b""

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
        raw = self.query_block(":WAVeform:PREamble?").decode("ascii", "replace")
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

    def read_waveform_volts(self, n: int, progress=None) -> tuple[np.ndarray, Preamble]:
        """Rohdaten eines Kanals aus dem internen Speicher in Volt.

        Format ASCII (Handbuch S. 189: "actual voltage value of each waveform
        point"): langsamer als WORD, aber ohne die im Handbuch widersprüchliche
        Umrechnung aus AD-Werten (YORigin mal in Counts, mal in V; YREF 128 in
        einem Beispiel für einen 12-Bit-Wandler). Das Gerät muss gestoppt sein.
        Gelesen wird in Stücken von READ_CHUNK Punkten über STARt/STOP.
        """
        self.write(":WAVeform:MODE RAW")
        self.write(":WAVeform:FORMat ASCII")
        self.write(f":WAVeform:SOURce CHANnel{n}")
        pre = self.preamble()
        total = int(self.query_float(":WAVeform:POINts?")) or pre.points
        if total <= 0:
            raise MSO1000HDValueError(f"Kanal {n}: Gerät meldet keine Punkte im Speicher.")
        chunks: list[np.ndarray] = []
        self._t.set_timeout(READ_TIMEOUT_S)
        try:
            start = 1
            while start <= total:
                stop = min(total, start + READ_CHUNK - 1)
                self.write(f":WAVeform:STARt {start}")
                self.write(f":WAVeform:STOP {stop}")
                data = self.query_block(":WAVeform:DATA?")
                values = _parse_ascii_values(data)
                if values.size == 0:
                    raise MSO1000HDValueError(f"Kanal {n}: leerer Datenblock bei {start}..{stop}.")
                chunks.append(values)
                start += values.size
                if progress is not None:
                    progress(start - 1, total)
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
