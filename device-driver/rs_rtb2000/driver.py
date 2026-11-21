"""Rohde & Schwarz RTB2000 (RTB2002, RTB2004) -- SCPI über VISA.

Grundlage: R&S RTB2000 User Manual 1333.1611.02 ─ 04, Kapitel 15 "Remote
Control Commands" (Seitenangaben unten = gedruckte Seitenzahlen), dazu das
R&S-Beispiel MATLAB_directSCPI_RTB_Example.m. Verbindung über pyvisa:

- LAN (empfohlen): VXI-11, Ressource ``TCPIP0::<ip>::inst0::INSTR`` (Handbuch
  S. 243), mit dem reinen Python-Backend pyvisa-py, also ohne NI-VISA. Geht
  auch als Socket ``TCPIP0::<ip>::5025::SOCKET`` (Port 5025, S. 156).
- USB (USB TMC): nur mit installiertem VISA (``@ivi``). Am Gerät muss dafür
  unter Setup > Interface "USB" mit Modus "USB TMC" gewählt sein, für LAN
  "Ethernet" (S. 155 f.).

Die Geräteart ist dieselbe wie beim UNI-T MSO1000HD ("scpiscope"): beide
stehen in derselben Adressliste der Einstellungen, welcher Treiber zuständig
ist, entscheidet die Antwort auf *IDN? (scope_api/scpi.py).

Der Treiber bleibt "dumm" (synchron, blockierend, ein Aufrufer-Thread) und
kennt nur Gerätebefehle. Die Übersetzung aus der herstellerneutralen
Erfassungsanfrage macht scope_api/rs_rtb_adapter.py.

STAND: am RTB2002 (Firmware 01.210) über LAN geprüft, siehe README.md. Was
dort noch nicht nachgemessen ist, ist mit ``HARDWARE-PRÜFUNG`` markiert.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import numpy as np

KIND = "scpiscope"
MAX_CHANNELS = 4
DEFAULT_TIMEOUT_S = 5.0
# CHANnel<m>:DATA? kennt keine Teilbereiche (anders als beim UNI-T): eine
# Kurve kommt in einem Block, bei 10 Mio. Punkten als REAL,32 sind das 40 MB
# -- über 100-Mbit-LAN grob 4 s, großzügig bemessen.
READ_TIMEOUT_S = 60.0
# Nach dem Datenblock folgt ein LF (am RTB2002 bestätigt, VXI-11 und Socket).
# Fehlt es doch, soll das Lesen nicht den vollen Timeout hängen.
BLOCK_END_TIMEOUT_S = 0.5
# SCPI-Socket des Geräts (S. 156). Am RTB2002 (Firmware 01.210) gemessen: eine
# Kurve mit 1,5 MB über den Socket in 1,0 s, über VXI-11 (pyvisa-py) in 6,2 s;
# VXI-11 mit mehr als 20 kB je Lesevorgang lief sogar in den Timeout.
SOCKET_PORT = 5025
SIM_RESOURCE = "SIM-RTB"
SIM_DEVICE_ID = f"{KIND}:{SIM_RESOURCE}"
# Grundbandbreite RTB2002/RTB2004 ohne Bandbreiten-Option (Datenblatt S. 12).
BASE_BANDWIDTH_HZ = 70e6
# Bandbreiten-Optionen RTB-B22x (2 Kanäle) bzw. RTB-B24x (4 Kanäle), x = 1/2/3.
OPTION_BANDWIDTH_HZ = {"1": 100e6, "2": 200e6, "3": 300e6}


class RTB2000Error(RuntimeError):
    """Verbindung oder Gerät gestört (Verbindung gilt als tot)."""


class RTB2000ValueError(RTB2000Error):
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
class Header:
    """CHANnel<m>:DATA:HEADer? (S. 341): Zeit des ersten und letzten Punkts,
    Zahl der Punkte, Werte je Abtastintervall (1, bei Hüllkurve 2)."""
    xstart: float
    xstop: float
    points: int
    values_per_sample: int

    @property
    def xinc(self) -> float:
        """Abstand zweier Punkte. Laut Beispiel S. 244 ist XStop die Zeit des
        LETZTEN Punkts: t_n = XStart + n * XINC, n = 0 .. points-1."""
        if self.points < 2:
            return 0.0
        return (self.xstop - self.xstart) / (self.points - 1)


def is_rtb_idn(vendor: str, model: str) -> bool:
    """*IDN? (S. 248): "Rohde&Schwarz,RTB2004,1333.1005k04/900012,01.203"."""
    return "ROHDE" in vendor.upper() and model.upper().startswith("RTB2")


def parse_model(model: str, options: str = "") -> ModelInfo:
    """RTB2002 -> 2 Kanäle, RTB2004 -> 4 Kanäle; letzte Ziffer = Kanalzahl.

    Bandbreite: 70 MHz, mit Option RTB-B22x/B24x 100/200/300 MHz. *OPT?
    liefert die Optionen "an festen Positionen, 0 für nicht installiert"
    (S. 248) -- wie die Einträge genau heißen ("B222" oder "RTB-B222"), ist
    nicht beschrieben, daher wird nur nach dem Muster B2<2|4><1-3> gesucht
    (HARDWARE-PRÜFUNG)."""
    m = re.search(r"RTB2\d\d(\d)", model.upper())
    channels = int(m.group(1)) if m else MAX_CHANNELS
    if channels not in (2, 4):
        channels = MAX_CHANNELS
    bandwidth = BASE_BANDWIDTH_HZ
    for opt in re.findall(r"B2[24]([123])\b", options.upper()):
        bandwidth = max(bandwidth, OPTION_BANDWIDTH_HZ[opt])
    return ModelInfo(channels=channels, bandwidth_hz=bandwidth)


def socket_resource(resource: str) -> str | None:
    """VXI-11-Adresse ``TCPIP<n>::<host>::inst0::INSTR`` -> SCPI-Socket
    desselben Hosts; andere Adressen (Socket, USB) -> None."""
    parts = resource.split("::")
    if (len(parts) == 4 and parts[0].upper().startswith("TCPIP") and parts[2].lower().startswith("inst")
            and parts[3].upper() == "INSTR"):
        return f"TCPIP0::{parts[1]}::{SOCKET_PORT}::SOCKET"
    return None


# -- Transport --------------------------------------------------------------------


class VisaTransport:
    """Dünne Hülle um eine pyvisa-Ressource: Zeilen schreiben/lesen, Bytes
    genau lesen. Jeden pyvisa-/OS-Fehler in RTB2000Error übersetzen."""

    def __init__(self, resource: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        try:
            import pyvisa
        except ImportError as exc:  # pragma: no cover -- requirements.txt
            raise RTB2000Error("pyvisa ist nicht installiert (requirements.txt).") from exc
        # LAN (VXI-11 wie Socket) immer über pyvisa-py, USB braucht ein
        # installiertes VISA ("@ivi").
        backend = "@py" if resource.upper().startswith("TCPIP") else "@ivi"
        try:
            self._rm = pyvisa.ResourceManager(backend)
            self._res = self._rm.open_resource(resource)
            self._res.timeout = int(timeout_s * 1000)
            self._res.read_termination = "\n"
            self._res.write_termination = "\n"
        except Exception as exc:  # noqa: BLE001 -- pyvisa.Error, OSError, VisaIOError-Unterklassen des Backends
            self.close()
            raise RTB2000Error(f"{resource} lässt sich nicht öffnen: {exc}") from exc

    def set_timeout(self, seconds: float) -> None:
        self._res.timeout = int(seconds * 1000)

    def write(self, command: str) -> None:
        try:
            self._res.write(command)
        except Exception as exc:  # noqa: BLE001
            raise RTB2000Error(f"Senden von {command!r} gescheitert: {exc}") from exc

    def read_line(self) -> str:
        try:
            return self._res.read().strip()
        except Exception as exc:  # noqa: BLE001
            raise RTB2000Error(f"Lesen gescheitert: {exc}") from exc

    def read_exact(self, count: int) -> bytes:
        try:
            return bytes(self._res.read_bytes(count))
        except Exception as exc:  # noqa: BLE001
            raise RTB2000Error(f"Lesen von {count} Bytes gescheitert: {exc}") from exc

    def close(self) -> None:
        for obj in (getattr(self, "_res", None), getattr(self, "_rm", None)):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:  # noqa: BLE001 -- Aufräumen darf nie scheitern
                pass


# -- Gerät ------------------------------------------------------------------------


class RTB2000:
    """Ein Oszilloskop der Reihe RTB2000. ``transport`` liefert write/read_line/
    read_exact/set_timeout/close -- echt (VisaTransport) oder simuliert
    (mock.SimulatedRTB2000)."""

    def __init__(self, transport) -> None:
        self._t = transport

    @classmethod
    def open(cls, resource: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> "RTB2000":
        if resource == SIM_RESOURCE:
            from rs_rtb2000.mock import SimulatedRTB2000
            return cls(SimulatedRTB2000())
        # Eingetragen ist meist die bloße IP (= VXI-11, wie beim UNI-T). Für
        # die Daten ist der Socket viel schneller (siehe SOCKET_PORT); geht er
        # nicht auf (am Gerät abgeschaltet, Firewall), bleibt es bei VXI-11.
        fast = socket_resource(resource)
        if fast is not None:
            try:
                return cls(VisaTransport(fast, timeout_s))
            except RTB2000Error:
                pass
        return cls(VisaTransport(resource, timeout_s))

    def close(self) -> None:
        self._t.close()

    def __enter__(self) -> "RTB2000":
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
            raise RTB2000ValueError(f"{command}: unerwartete Antwort {text!r}") from exc

    def query_block(self, command: str) -> bytes:
        """Definite Length Block ``#<n><Länge><Daten>`` (S. 339): die Ziffer
        nach ``#`` nennt die Zahl der Längenziffern."""
        self._t.write(command)
        head = self._t.read_exact(2)
        if head[:1] != b"#" or not head[1:2].isdigit():
            rest = head + self._read_rest_of_line()
            raise RTB2000ValueError(f"{command}: kein Datenblock, Antwort {rest[:80]!r}")
        digits = int(head[1:2])
        if digits == 0:
            # "#0" = Indefinite Length Block, bis zum Zeilenende.
            return self._read_rest_of_line()
        length = int(self._t.read_exact(digits))
        data = self._t.read_exact(length) if length else b""
        self._discard_block_end()
        return data

    def _discard_block_end(self) -> None:
        self._t.set_timeout(BLOCK_END_TIMEOUT_S)
        try:
            self._t.read_exact(1)
        except RTB2000Error:
            pass
        finally:
            self._t.set_timeout(DEFAULT_TIMEOUT_S)

    def _read_rest_of_line(self) -> bytes:
        try:
            return self._t.read_line().encode()
        except RTB2000Error:
            return b""

    # -- Identität / Fehler --------------------------------------------------------

    def idn(self) -> IdnInfo:
        text = self.query("*IDN?")
        parts = [p.strip() for p in text.split(",")]
        parts += [""] * (4 - len(parts))
        if not is_rtb_idn(parts[0], parts[1]):
            raise RTB2000Error(f"Kein R&S RTB2000: *IDN? -> {text!r}")
        return IdnInfo(vendor=parts[0], model=parts[1], serial=parts[2], firmware=parts[3])

    def options(self) -> str:
        """*OPT? (S. 248), Rohtext."""
        return self.query("*OPT?")

    def pop_error(self) -> tuple[int, str]:
        """SYSTem:ERRor[:NEXT]? (S. 365) -> (Code, Text), 0 = kein Fehler."""
        text = self.query("SYSTem:ERRor?")
        head, _, message = text.partition(",")
        try:
            code = int(float(head))
        except ValueError:
            return (0, "") if not text or "no error" in text.lower() else (-1, text)
        return code, message.strip().strip('"')

    def clear_errors(self) -> None:
        # *CLS (S. 247) setzt die Statusregister zurück und leert laut SCPI
        # auch die Fehlerwarteschlange.
        self.write("*CLS")

    # -- Einstellungen ------------------------------------------------------------

    def set_channel(self, n: int, display: bool, coupling: str = "DC", probe: float = 1.0,
                    scale_v: float | None = None, offset_v: float | None = None) -> None:
        self.write(f"CHANnel{n}:STATe {'ON' if display else 'OFF'}")
        if not display:
            return
        # Kopplung heißt hier DCLimit/ACLimit (1 MOhm, S. 254), nicht DC/AC.
        self.write(f"CHANnel{n}:COUPling {'ACLimit' if coupling.upper() == 'AC' else 'DCLimit'}")
        self.write(f"CHANnel{n}:BANDwidth FULL")
        self.write(f"CHANnel{n}:POLarity NORMal")
        self.write(f"PROBe{n}:SETup:ATTenuation:UNIT V")
        # Tastkopf vor der Skala setzen: CHANnel:SCALe gilt laut S. 253 "without
        # probe attenuation" für 1 mV..10 V/div -- angenommen ist, dass sich
        # die Skala danach auf die Tastkopfspitze bezieht (HARDWARE-PRÜFUNG,
        # bisher nur mit 1:1 am Gerät geprüft).
        self.write(f"PROBe{n}:SETup:ATTenuation:MANual {probe:g}")
        if scale_v is not None:
            self.write(f"CHANnel{n}:SCALe {scale_v:.6g}")
        if offset_v is not None:
            self.write(f"CHANnel{n}:OFFSet {offset_v:.6g}")

    def channel_scale(self, n: int) -> float:
        return self.query_float(f"CHANnel{n}:SCALe?")

    def channel_range(self, n: int) -> float:
        """CHANnel<m>:RANGe? (S. 253): Spannung über ALLE vertikalen Teilungen
        -- am RTB2002 sind das 10 (2 V/div -> 20 V), nicht 8, wie die
        Grenzen auf S. 253 nahelegen."""
        return self.query_float(f"CHANnel{n}:RANGe?")

    def channel_offset(self, n: int) -> float:
        return self.query_float(f"CHANnel{n}:OFFSet?")

    def set_timebase(self, scale_s: float, position_s: float) -> None:
        self.write("TIMebase:ROLL:ENABle OFF")
        self.write("TIMebase:ZOOM:STATe OFF")
        # Referenzpunkt in der Bildschirmmitte (S. 259): TIMebase:POSition ist
        # dann der Abstand Trigger -> Bildschirmmitte.
        self.write("TIMebase:REFerence 50")
        self.write(f"TIMebase:SCALe {scale_s:.6g}")
        self.write(f"TIMebase:POSition {position_s:.6g}")

    def timebase_scale(self) -> float:
        return self.query_float("TIMebase:SCALe?")

    def timebase_position(self) -> float:
        return self.query_float("TIMebase:POSition?")

    def timebase_divisions(self) -> int:
        """TIMebase:DIVisions? (S. 259): 12. TIMebase:ACQTime spricht von "10
        divisions" (S. 259) -- am RTB2002 gemessen gilt 12 (500 µs/div ->
        6 ms Fenster laut Kopf der Daten)."""
        return int(round(self.query_float("TIMebase:DIVisions?")))

    def set_acquisition(self, points: int) -> None:
        self.write("ACQuire:TYPE REFresh")
        self.write("CHANnel:TYPE SAMPle")          # gilt für alle Kanäle (S. 261)
        self.write("CHANnel:ARIThmetics OFF")
        self.write("ACQuire:NSINgle:COUNt 1")
        # Setzt ACQuire:POINts:AUTomatic OFF; das Gerät nimmt die nächste
        # der Stufen 10k..20M (S. 261).
        self.write(f"ACQuire:POINts {int(points)}")

    def acquisition_points(self) -> int:
        return int(self.query_float("ACQuire:POINts?"))

    def sample_rate(self) -> float:
        return self.query_float("ACQuire:SRATe?")

    def set_edge_trigger(self, source_n: int, level_v: float, rising: bool, normal: bool = True) -> None:
        # NORMal: nur echte Trigger. Auto-Trigger macht der Adapter selbst
        # (*TRG nach timeout_s), damit die Wartezeit der Anfrage gilt und
        # nicht die interne des Geräts.
        self.write(f"TRIGger:A:MODE {'NORMal' if normal else 'AUTO'}")
        self.write("TRIGger:A:TYPE EDGE")
        self.write(f"TRIGger:A:SOURce CH{source_n}")
        self.write(f"TRIGger:A:EDGE:SLOPe {'POSitive' if rising else 'NEGative'}")
        self.write("TRIGger:A:EDGE:COUPling DC")
        # Filter im Triggerpfad ausdrücklich aus: am RTB2002 war HFReject (5-kHz-
        # Tiefpass, S. 269) vom Bedienen noch an -- der Trigger kam dadurch
        # 2,0 µs nach der Flanke, unabhängig von der Zeitbasis.
        self.write("TRIGger:A:EDGE:FILTer:HFReject OFF")
        self.write("TRIGger:A:EDGE:FILTer:NREJect OFF")
        self.write("TRIGger:A:HOLDoff:MODE OFF")
        self.write(f"TRIGger:A:LEVel{source_n}:VALue {level_v:.6g}")

    def sync(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        """*OPC? (S. 248): wartet, bis alle vorigen Befehle ausgeführt sind.
        Nötig vor SINGle -- am RTB2002 brauchen die Einstellungen 0,2..0,5 s;
        ohne diesen Punkt ging SINGle gelegentlich verloren (Zustand blieb
        STOP) und CHANnel:DATA? antwortete danach gar nicht mehr."""
        self._t.set_timeout(timeout_s)
        try:
            answer = self.query("*OPC?")
        finally:
            self._t.set_timeout(DEFAULT_TIMEOUT_S)
        if answer.strip() != "1":
            raise RTB2000ValueError(f"*OPC?: unerwartete Antwort {answer!r}")

    # -- Ablauf -------------------------------------------------------------------

    def single(self) -> None:
        self.write("SINGle")

    def run(self) -> None:
        self.write("RUN")

    def stop(self) -> None:
        self.write("STOP")

    def force_trigger(self) -> None:
        self.write("*TRG")

    def acquisition_state(self) -> str:
        """ACQuire:STATe? (S. 251): RUN / STOP / COMPlete / BREak -- das
        RTB2002 antwortet in Kurzform (nach einer Einzelerfassung "COMP"),
        siehe is_complete()."""
        return self.query("ACQuire:STATe?").strip().upper()

    # -- Daten --------------------------------------------------------------------

    def header(self, n: int) -> Header:
        text = self.query(f"CHANnel{n}:DATA:HEADer?")
        fields = [f.strip() for f in text.split(",") if f.strip()]
        if len(fields) < 3:
            raise RTB2000ValueError(f"Kopf der Daten unvollständig: {text[:120]!r}")
        try:
            return Header(xstart=float(fields[0]), xstop=float(fields[1]), points=int(float(fields[2])),
                          values_per_sample=int(float(fields[3])) if len(fields) > 3 else 1)
        except ValueError as exc:
            raise RTB2000ValueError(f"Kopf der Daten nicht lesbar: {text[:120]!r}") from exc

    def read_waveform_volts(self, n: int) -> tuple[np.ndarray, Header]:
        """Kurve eines Kanals in Volt, Gerät muss gestoppt sein.

        Format REAL,32 (S. 339): 4-Byte-Gleitkomma je Punkt, schon in Volt --
        keine Umrechnung aus ADC-Werten, und viermal kompakter als ASCII.
        Bereich DMAXimum (S. 341): alle Punkte der Erfassung im angezeigten
        Zeitbereich (DEFault liefert bei hoher Kurvenrate weniger, MAXimum
        auch Punkte außerhalb des Bildschirms)."""
        self.write("FORMat:DATA REAL,32")
        self.write("FORMat:BORDer LSBFirst")
        self.write("CHANnel:DATA:POINts DMAXimum")
        head = self.header(n)
        if head.points <= 0:
            raise RTB2000ValueError(f"Kanal {n}: Gerät meldet keine Punkte.")
        self._t.set_timeout(READ_TIMEOUT_S)
        try:
            data = self.query_block(f"CHANnel{n}:DATA?")
        finally:
            self._t.set_timeout(DEFAULT_TIMEOUT_S)
        if len(data) % 4:
            raise RTB2000ValueError(f"Kanal {n}: Datenblock mit {len(data)} Bytes ist kein REAL,32.")
        volts = np.frombuffer(data, dtype="<f4").astype(float)
        if volts.size == 0:
            raise RTB2000ValueError(f"Kanal {n}: leerer Datenblock.")
        return volts, head

    # -- Bequemlichkeit für CLI/Tests ----------------------------------------------

    def wait_finished(self, timeout_s: float, poll_s: float = 0.02) -> str | None:
        deadline = time.monotonic() + timeout_s
        while True:
            state = self.acquisition_state()
            if is_complete(state):
                return state
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll_s)


def is_complete(state: str) -> bool:
    """Einzelerfassung fertig: COMPlete (Kurz- oder Langform).

    Am RTB2002 gemessen: STOP setzt den Zustand auf "STOP", nach SINGle kommt
    zuerst noch "STOP", dann "COMP". STOP heißt also "noch nicht gestartet"
    und zählt nicht als fertig."""
    return state.upper().startswith("COMP")


def probe(resource: str, timeout_s: float = 2.0) -> IdnInfo:
    """Öffnen, *IDN?, schließen."""
    scope = RTB2000.open(resource, timeout_s)
    try:
        return scope.idn()
    finally:
        scope.close()
