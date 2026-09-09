"""Vendor-unabhaengiger CAN-Treiber, wrapt python-can.

Unterstuetzt aktuell Vector- (z.B. CANcase XL, benoetigt die Vector XL
Driver Library) und PEAK-Interfaces (z.B. PCAN-USB, benoetigt PCAN-Basic) --
beides Fremd-Software, die separat installiert sein muss (siehe README.md) --
sowie SLCAN (serielles/USB-CDC-Protokoll, u.a. vom microHIL-CAN1-Port
gesprochen, siehe _slcan_serial_configs() unten). Ausserhalb dieses Moduls
ist nie von einem konkreten Hersteller die Rede, nur noch von
interface/channel/bitrate (siehe device_worker.py) -- neue Interface-Typen
lassen sich durch Ergaenzen von INTERFACE_LIST anbinden, sofern python-can
sie unterstuetzt.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import can
from serial.tools import list_ports

logger = logging.getLogger(__name__)

INTERFACE_LIST = ["vector", "pcan", "slcan"]
DEFAULT_BITRATE = 500_000
# Baudrate der seriellen/USB-Verbindung ZUM Adapter (python-can-Parameter
# `tty_baudrate`) -- zu unterscheiden von obigem DEFAULT_BITRATE, der
# Bitrate AUF dem CAN-Bus selbst. Bei USB-CDC-Adaptern wie dem microHIL-
# CAN1-Port wird dieser Wert vom virtuellen COM-Port ignoriert (analog zur
# Anmerkung in microhil/driver.py), pyserial/python-can verlangen aber
# trotzdem einen Wert -- 115200 ist der python-can-Standardwert und passt
# zufaellig auch zu microhil/driver.py::DEFAULT_BAUDRATE.
DEFAULT_SLCAN_SERIAL_BAUDRATE = 115200

# Standard-Installationsort der Vector XL Driver Library (vxlapi64.dll landet
# dort ueber den offiziellen Vector-Treiber-Installer) plus dem optionalen
# SDK-Ordner, falls zusaetzlich die "XL Driver Library" separat installiert
# wurde.
_VECTOR_DLL_CANDIDATES = (
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32",
    Path(os.environ.get("PUBLIC", r"C:\Users\Public"))
    / "Documents" / "Vector" / "XL Driver Library" / "bin",
)


def _ensure_vector_dll_findable() -> None:
    """Rein defensiv, NICHT bestaetigt als Ursache des "keine Kanaele
    gefunden"-Bugs an echter VN1610-Hardware (siehe unten) -- lediglich ein
    zusaetzliches Sicherheitsnetz fuer den Fall, dass `System32` auf einem
    bestimmten Rechner NICHT in PATH steht (waere sonst der einzige Fall, in
    dem `ctypes.util.find_library()` unter Windows die Datei uebersieht --
    es durchsucht ausschliesslich `os.environ["PATH"]`, siehe dessen
    CPython-Quelltext, ohne eigenen Fallback auf die echte Windows-DLL-
    Suchreihenfolge/System32-Automatik).

    Direkt an echter VN1610-Hardware nachgemessen (sowohl per einfachem
    `python`-Aufruf als auch aus dem gebauten LabControl_v0.9.16.exe
    heraus): `find_library("vxlapi64")` fand die DLL in
    `C:\\Windows\\System32` ZUVERLAESSIG, auch OHNE diese Funktion --
    System32 stand in beiden Faellen bereits reguleaer in PATH. Der
    eigentliche, einmal beobachtete "keine Kanaele gefunden"-Fall liess
    sich bei mehreren Wiederholungen (mit und ohne manuelles Vorladen der
    DLL) NICHT reproduzieren, alle lieferten korrekt alle VN1610-/virtuellen
    Kanaele -- deutet eher auf ein einmaliges/zeitliches Problem hin (z.B.
    die Vector-Treiber-Enumeration direkt nach Systemstart/Anstecken der
    VN1610 noch nicht fertig) als auf einen deterministischen Code-Bug.
    Diese Funktion bleibt trotzdem als billige, harmlose Absicherung stehen
    -- schadet nicht, falls PATH auf einem anderen Rechner doch einmal kein
    System32 enthaelt."""
    path_env = os.environ.get("PATH", "")
    for candidate in _VECTOR_DLL_CANDIDATES:
        if candidate.is_dir() and str(candidate) not in path_env.split(os.pathsep):
            path_env = str(candidate) + os.pathsep + path_env
    os.environ["PATH"] = path_env


_ensure_vector_dll_findable()


# Kanal-Token fuer Vector-Interfaces: "<seriennummer>:<hw_kanal>", z.B.
# "75816:0" fuer CAN 1 der VN1610 mit Seriennummer 75816. discover_configs()
# liefert Kanaele ausschliesslich in dieser Form, siehe _vector_bus_kwargs()
# fuer den Grund.
_VECTOR_CHANNEL_TOKEN = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")


def _vector_bus_kwargs(channel: str) -> dict:
    """Bindet einen Vector-Kanal an die PHYSISCHE Hardware statt an eine
    Anwendung aus der Vector Hardware Config.

    Hintergrund (an echter VN1610 nachgemessen, war die eigentliche Ursache
    des lange gesuchten "CAN-Interface"-Bugs): python-cans `VectorBus`
    defaultet `app_name` auf **"CANalyzer"**. Ohne explizites Ueberschreiben
    bedeutet `channel=0` deshalb NICHT "Kanal 0 der angeschlossenen
    Hardware", sondern "der Kanal, der in der Vector Hardware Config der
    Anwendung namens *CANalyzer* auf Position 0 zugewiesen ist"
    (`VectorBus._find_global_channel_idx` -> `xlGetApplConfig`). Damit haengt
    die Verbindung an einer fremden, vom Nutzer unabhaengig veraenderbaren
    CANalyzer-Konfiguration:

    - Ist auf dem Rechner keine Anwendung "CANalyzer" eingerichtet (z.B. weil
      nur der Vector-Treiber, aber kein CANalyzer/CANoe installiert ist),
      schlaegt JEDER Verbindungsversuch mit "xlGetApplConfig failed" fehl --
      im DeviceWorker nur als Log-Warnung sichtbar, das Interface taucht in
      der GUI schlicht nie auf.
    - Ist sie eingerichtet, aber auf ein anderes Geraet/einen anderen Kanal
      gemappt, verbindet sich LabControl klaglos mit der FALSCHEN Hardware
      (z.B. einem virtuellen Kanal): Status "verbunden", aber nie ein Frame.

    Deshalb hier immer `app_name=None`. Damit interpretiert python-can den
    Kanal entweder ueber die Seriennummer (bevorzugt, eindeutig) oder --
    ohne Seriennummer -- als globalen Kanalindex ueber alle Vector-Geraete
    hinweg.
    """
    token = str(channel).strip()
    match = _VECTOR_CHANNEL_TOKEN.match(token)
    if match:
        # Bevorzugter Weg: eindeutig ueber Seriennummer + Hardware-Kanal,
        # unabhaengig von Reihenfolge/Anzahl anderer Vector-Geraete.
        return dict(channel=int(match.group(2)), serial=int(match.group(1)), app_name=None)
    # Alt-Konfiguration aus der Zeit vor dem Token-Format (reine Zahl, z.B.
    # "0"): als globaler Kanalindex behandeln. Bleibt fuer bestehende
    # settings.json lauffaehig, ist aber nicht mehr eindeutig, sobald sich
    # die Zusammenstellung der Vector-Geraete aendert -- der Kanal sollte im
    # Settings-Tab einmal neu ausgewaehlt werden.
    return dict(channel=token, app_name=None)


def _vector_display_name(config: dict) -> str:
    """Sprechender Name eines erkannten Vector-Kanals fuer die Auswahl im
    Settings-Tab, z.B. "VN1610 Channel 1 (S/N 75816)". Ohne ihn zeigt die
    Auswahl nur nackte Kanalnummern, die sich zwischen echter Hardware und
    den immer vorhandenen virtuellen Kanaelen doppeln."""
    channel_config = config.get("vector_channel_config")
    name = getattr(channel_config, "name", "") or f"Kanal {config.get('hw_channel', '?')}"
    serial = config.get("serial")
    return f"{name} (S/N {serial})" if serial is not None else name


def _slcan_serial_configs() -> list[dict]:
    """Verfuegbare serielle Ports als SLCAN-Kandidaten.

    Anders als Vector/PCAN kennt python-can fuer SLCAN keine eigene
    Geraete-Erkennung (`can.interfaces.slcan.slcanBus` implementiert kein
    eigenes `_detect_available_configs()`, `can.detect_available_configs
    (interfaces=["slcan"])` liefert deshalb immer eine leere Liste -- nach-
    geprueft gegen python-can 4.6.1). Jeder serielle Port ist grundsaetzlich
    ein moeglicher SLCAN-Adapter (generisches Protokoll, kein fester
    VID/PID wie bei den uebrigen Treibern dieses Repos), daher hier direkt
    ueber `serial.tools.list_ports` statt ueber python-can.

    Sonderfall microHIL: dessen USB-Composite meldet zwei Ports unter
    identischer VID:PID -- Interface 0 spricht das HIL-Kommandoprotokoll
    (siehe microhil/driver.py), NICHT SLCAN, und wuerde hier ohne Filterung
    als (nutzloser, weil falsches Protokoll) Kanal-Kandidat auftauchen.
    `microhil.driver.MicroHIL.discover()` hat diese Unterscheidung (Interface
    0 vs. 2 anhand hwid/LOCATION) bereits geloest -- wird hier wiederverwendet
    statt neu erfunden, um den HIL-Port herauszufiltern und den CAN1-Port
    (falls eindeutig bestimmbar) sprechend zu benennen. Bleibt die
    Zuordnung mehrdeutig (HilError), wird NICHTS herausgefiltert -- lieber
    alle Kandidaten zeigen als einen echten SLCAN-Port faelschlich zu
    verstecken.
    """
    from microhil.driver import MicroHIL, HilError, CAN_INTERFACE_NUMBER as MICROHIL_CAN_INTERFACE
    from microhil.driver import _interface_number as _microhil_interface_number

    try:
        hil_port = MicroHIL.discover()
    except HilError:
        hil_port = None
    microhil_ports = {info.device: info for info in MicroHIL.discover_ports()}

    configs: list[dict] = []
    for info in list_ports.comports():
        if info.device == hil_port:
            continue  # microHIL HIL-Protokoll-Port, spricht kein SLCAN
        display_name = info.description or info.device
        if info.device in microhil_ports:
            iface = _microhil_interface_number(microhil_ports[info.device])
            if iface == MICROHIL_CAN_INTERFACE:
                display_name = f"microHIL CAN1 ({info.device})"
        configs.append(dict(interface="slcan", channel=info.device, display_name=display_name))
    return configs


class CanError(RuntimeError):
    """Fehler bei der Kommunikation ueber den CAN-Bus."""


class CanConnectionError(CanError):
    """Verbindung zum Interface konnte nicht (mehr) hergestellt werden."""


@dataclass
class CanFrame:
    arbitration_id: int
    data: bytes
    extended: bool
    # Sekunden seit dem Oeffnen dieses CanBus (siehe CanBus._opened), NICHT
    # der von python-can gelieferte msg.timestamp: dessen Referenzpunkt ist
    # backend-abhaengig (z.B. beim Vector-Backend Unix-Epoch statt eines
    # kleinen, fuer die Anzeige sinnvollen Werts -- an echter VN1610-Hardware
    # beobachtet, siehe control_tab.CanControlGroup.append_frame, dessen
    # "Zeit (s)"-Spalte kleine Werte erwartet, wie MockCanBus sie auch schon
    # liefert).
    timestamp: float


class CanBus:
    def __init__(
        self,
        interface: str,
        channel: str,
        bitrate: int = DEFAULT_BITRATE,
        serial_baudrate: int | None = None,
    ):
        # python-can wirft je nach Backend/Fehlerursache sehr unterschiedliche
        # Exception-Typen (fehlende Vendor-DLL: OSError/ImportError, falscher
        # Kanal: eigene *InitializationError-Klassen, ...) -- breit gefangen
        # und auf die App-eigene Exception-Hierarchie abgebildet, analog zum
        # SerialException-Fang in hcs34xx/driver.py.
        kwargs: dict = dict(interface=interface, channel=channel, bitrate=bitrate)
        if interface == "vector":
            kwargs.update(_vector_bus_kwargs(channel))
        elif interface == "slcan":
            # serial_baudrate ist fuer vector/pcan bedeutungslos (dort nie
            # gesetzt) -- nur hier verwendet, siehe DEFAULT_SLCAN_SERIAL_BAUDRATE.
            kwargs["tty_baudrate"] = serial_baudrate or DEFAULT_SLCAN_SERIAL_BAUDRATE
        try:
            self._bus = can.interface.Bus(**kwargs)
        except Exception as exc:
            raise CanConnectionError(
                f"Verbindung zu {interface}:{channel} fehlgeschlagen: {exc}"
            ) from exc
        self.interface = interface
        self.channel = channel
        self.bitrate = bitrate
        self.serial_baudrate = kwargs.get("tty_baudrate")
        self._opened = time.monotonic()

    @staticmethod
    def backend_problem(interface: str) -> str:
        """Leerer String, wenn das python-can-Backend fuer ``interface``
        ueberhaupt ladbar ist, sonst eine Begruendung.

        Existiert, weil `can.detect_available_configs()` NICHT zwischen
        "Backend geladen, aber kein Geraet angeschlossen" und "Backend gar
        nicht vorhanden" unterscheidet -- es liefert in beiden Faellen
        wortlos eine leere Liste (`_get_class_for_interface` faengt den
        ImportError als CanInterfaceNotImplementedError ab und ueberspringt
        den Interface-Typ nur mit einer Log-Zeile). Genau daran hing ein
        lange gesuchter Bug: in der von PyInstaller gebauten .exe fehlten
        saemtliche Backends, weil python-can sie ueber Modulnamen als String
        nachlaedt (can.interfaces.BACKENDS -> importlib.import_module) und
        PyInstallers statische Analyse solche Importe nicht sieht. Sichtbar
        war das nur als "Keine Kanäle gefunden" im Settings-Tab und liess
        sich aus dem Quellcode heraus prinzipiell nie reproduzieren, weil
        dort alle Backends normal importierbar sind (siehe LabControl.spec::
        CAN_HIDDENIMPORTS).
        """
        try:
            can.interface._get_class_for_interface(interface)
        except Exception as exc:
            return str(exc)
        return ""

    @staticmethod
    def discover_configs(errors: dict[str, str] | None = None) -> list[dict]:
        """Verfuegbare Kanaele je unterstuetztem Interface-Typ.

        Liefert nur Kanaele, deren Vendor-Treiber tatsaechlich installiert
        ist -- ein nicht installiertes Backend wirft beim Erkennen eine
        Exception (fehlende DLL o.ae.), die hier pro Interface-Typ
        uebersprungen wird, damit z.B. ein fehlendes PCAN-Basic nicht auch
        die Vector-Erkennung verhindert.

        ``errors`` wird, falls uebergeben, mit ``interface -> Begruendung``
        fuer jeden Interface-Typ gefuellt, der gar kein Ergebnis liefern
        konnte -- damit der Aufrufer "kein Geraet angeschlossen" von
        "Backend/Treiber fehlt" unterscheiden kann, statt beides als leere
        Liste zu sehen (siehe backend_problem).

        Jeder Eintrag traegt neben dem von python-can gelieferten Inhalt:

        - ``channel``: immer ein String, und bei Vector das eindeutige Token
          "<seriennummer>:<hw_kanal>" statt der rohen Kanalnummer. python-can
          meldet beim Erkennen den HARDWARE-Kanal (0/1 je Geraet), erwartet
          beim Verbinden ohne Seriennummer aber einen GLOBALEN Index ueber
          alle Vector-Geraete -- zwei verschiedene Nummernkreise, die sich
          nur zufaellig gleichen, solange genau ein Geraet angeschlossen ist
          (siehe _vector_bus_kwargs). Das Token ist in beiden Richtungen
          eindeutig und wird so auch in settings.json abgelegt.
        - ``display_name``: sprechender Name fuer die Auswahl im
          Settings-Tab (nicht zu verwechseln mit dem Feld ``label``, das dort
          die frei vergebene Nutzer-Bezeichnung eines Interfaces meint).
        """
        configs: list[dict] = []
        for interface in INTERFACE_LIST:
            problem = CanBus.backend_problem(interface)
            if problem:
                logger.warning("CAN-Backend '%s' nicht ladbar: %s", interface, problem)
                if errors is not None:
                    errors[interface] = problem
                continue
            try:
                if interface == "slcan":
                    # can.detect_available_configs() liefert fuer SLCAN immer
                    # [] (siehe _slcan_serial_configs()-Docstring) -- eigene
                    # Erkennung ueber serielle Ports statt python-can.
                    found = _slcan_serial_configs()
                else:
                    found = can.detect_available_configs(interfaces=[interface])
            except Exception as exc:
                logger.warning("CAN-Erkennung fuer '%s' fehlgeschlagen: %s", interface, exc)
                if errors is not None:
                    errors[interface] = str(exc)
                continue
            for config in found:
                config.setdefault("interface", interface)
                if interface == "vector":
                    config["display_name"] = _vector_display_name(config)
                    config["channel"] = f"{config.get('serial')}:{config.get('hw_channel')}"
                else:
                    config["channel"] = str(config.get("channel", ""))
                    config.setdefault("display_name", config["channel"])
                configs.append(config)
        return configs

    def close(self) -> None:
        self._bus.shutdown()

    def __enter__(self) -> "CanBus":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def send(self, arbitration_id: int, data: bytes, extended: bool = False) -> None:
        msg = can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=extended)
        try:
            self._bus.send(msg)
        except Exception as exc:
            raise CanError(f"Senden fehlgeschlagen: {exc}") from exc

    def recv(self, timeout: float = 0.0) -> CanFrame | None:
        """Nicht-blockierender Empfang (timeout=0.0) fuers Polling in
        device_worker.py -- liefert None, wenn kein Frame anliegt."""
        try:
            msg = self._bus.recv(timeout=timeout)
        except Exception as exc:
            raise CanConnectionError(f"Empfang fehlgeschlagen: {exc}") from exc
        if msg is None:
            return None
        return CanFrame(
            arbitration_id=msg.arbitration_id,
            data=bytes(msg.data),
            extended=msg.is_extended_id,
            timestamp=time.monotonic() - self._opened,
        )
