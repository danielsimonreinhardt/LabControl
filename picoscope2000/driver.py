"""Treiber fuer PicoScope 2204A/2205A (Block-Erfassung ueber die ps2000-API).

Kommunikation per ctypes ueber die ps2000.dll (offizielles picosdk-Paket von
Pico Technology). Trotz "A" im Modellnamen nutzen 2204A/2205A laut Pico-
Support NICHT die neuere ps2000a-API -- die ist erst fuer spaetere Modelle
gedacht. ps2000aOpenUnit liefert bei diesen Geraeten PICO_NOT_FOUND, auch
wenn das Geraet angeschlossen ist (gegen echte Hardware verifiziert: 2204A,
Serial GP816/090 -- ps2000_open_unit() funktioniert, ps2000aOpenUnit() nicht).

Die DLL wird hier nicht von einem separaten PicoSDK-Setup bereitgestellt,
sondern liegt im Installationsordner der mitgelieferten PicoScope-7-Software
und damit NICHT auf dem PATH. picosdk sucht die DLL beim Import von
picosdk.ps2000 ausschliesslich ueber ctypes.util.find_library() (= PATH) --
ohne das Voranstellen des gefundenen Ordners in _ensure_dll_on_path() schlaegt
der Import mit CannotFindPicoSDKError fehl, obwohl die DLL vorhanden ist.

Bekannte Einschraenkung der ps2000-API: es gibt keine Geraete-Enumeration wie
bei ps2000a (enumerate_units()). ps2000_open_unit() oeffnet schlicht "das
naechste freie Geraet" -- eine gezielte Auswahl unter mehreren angeschlossenen
Scopes per Seriennummer ist damit nicht moeglich. discover() muss das Geraet
testweise oeffnen und wieder schliessen, um ueberhaupt an die Seriennummer zu
kommen.

WICHTIG: Der DLL-Import (siehe unten) ist bewusst defensiv (try/except statt
eines harten Modul-Imports) -- ist auf dem jeweiligen Rechner weder
PicoScope 7 noch ein separates PicoSDK installiert, WUERDE ein normaler
Import sonst schon beim Programmstart mit CannotFindPicoSDKError crashen und
damit die komplette LabControl-App lahmlegen, nicht nur die PicoScope-Kachel
(device_worker.py importiert dieses Modul global). usb_present() und
launch_app() funktionieren deshalb auch OHNE die ps2000.dll (reine
Windows-SetupAPI- bzw. Prozessstart-Funktionen) -- nur open_first() und
capture_block() brauchen die DLL tatsaechlich und werfen PicoScope2000Error,
falls sie fehlt.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

from picoscope2000.common import (
    CHANNEL_MAP,
    VOLTAGE_RANGE_CODES,
    Measurement,
    BlockCapture,
    PicoScope2000Error,
    UnitInfo,
)

# Installationsordner, in denen sowohl ps2000.dll als auch PicoScope.exe
# gesucht werden (gegen echte Installation verifiziert: nur PicoScope 7 T&M
# Stable vorhanden, kein separates PicoSDK-Setup -- siehe README.md).
_INSTALL_DIR_CANDIDATES = [
    r"C:\Program Files\Pico Technology\PicoScope 7 T&M Stable",
    r"C:\Program Files\Pico Technology\PicoScope 7 T&M",
    r"C:\Program Files\Pico Technology\PicoScope 6",
    r"C:\Program Files\Pico Technology\SDK\lib",
    r"C:\Program Files (x86)\Pico Technology\SDK\lib",
]

# Vom PicoScope 2204A ueber Windows-Geraetemanager verifiziert (Instanz-ID
# "USB\VID_0CE9&PID_1007\..."). Pico Technology nutzt VID 0x0CE9 fuer alle
# Modelle: sollte ein 2205A oder eine andere Variante eine abweichende PID
# haben, hier ergaenzen.
USB_VID = 0x0CE9
USB_PID = 0x1007


def _find_install_dir(filename: str) -> Path | None:
    for candidate in _INSTALL_DIR_CANDIDATES:
        path = Path(candidate) / filename
        if path.is_file():
            return Path(candidate)
    return None


def _ensure_dll_on_path() -> None:
    install_dir = _find_install_dir("ps2000.dll")
    if install_dir is None:
        return
    path_env = os.environ.get("PATH", "")
    if str(install_dir) not in path_env:
        os.environ["PATH"] = str(install_dir) + os.pathsep + path_env


_ensure_dll_on_path()

try:
    from picosdk.ps2000 import ps2000 as _ps
    from picosdk.functions import adc2mV
    _IMPORT_ERROR: str | None = None
except OSError as exc:
    # CannotFindPicoSDKError (picosdk.errors) ist eine OSError-Unterklasse --
    # tritt auf, wenn weder PicoScope 7 noch PicoSDK installiert ist.
    _ps = None
    adc2mV = None
    _IMPORT_ERROR = str(exc)

_MAX_ADC = ctypes.c_int16(32767)
_INFO_VARIANT = 3
_INFO_SERIAL = 4


def _require_dll() -> None:
    if _ps is None:
        raise PicoScope2000Error(
            "ps2000.dll nicht gefunden -- PicoScope 7 oder PicoSDK installiert? "
            f"({_IMPORT_ERROR})"
        )


# -- USB-Praesenzerkennung (Windows SetupAPI, kein ps2000.dll noetig) --------
# Ergaenzt die ps2000-API: ps2000_open_unit() belegt das Geraet exklusiv und
# schlaegt daher fehl, sobald z.B. die PicoScope-7-App es bereits offen haelt
# -- das laesst sich damit NICHT von "gar kein Geraet angeschlossen"
# unterscheiden (gegen echte Hardware verifiziert: beide Faelle liefern
# Status 0). usb_present() fragt stattdessen die physische USB-Praesenz
# direkt bei Windows ab, unabhaengig davon, wer das Geraet gerade haelt --
# damit kann device_worker.py "frei" von "belegt (andere App offen)"
# unterscheiden.

_setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

_DIGCF_PRESENT = 0x00000002
_DIGCF_ALLCLASSES = 0x00000004
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", ctypes.c_byte * 16),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(wintypes.ULONG)),
    ]


_setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
_setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
]
_setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
_setupapi.SetupDiEnumDeviceInfo.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(_SP_DEVINFO_DATA),
]
_setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
_setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(_SP_DEVINFO_DATA),
    wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]
_setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
_setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]


def usb_present(vid: int = USB_VID, pid: int = USB_PID) -> bool:
    """Prueft per Windows-SetupAPI, ob ein USB-Geraet mit dieser VID/PID
    physisch angeschlossen ist -- unabhaengig davon, ob ps2000_open_unit()
    es gerade oeffnen koennte (siehe Modul-Docstring oben)."""
    needle = f"VID_{vid:04X}&PID_{pid:04X}"
    dev_info_set = _setupapi.SetupDiGetClassDevsW(
        None, "USB", None, _DIGCF_PRESENT | _DIGCF_ALLCLASSES
    )
    if dev_info_set == _INVALID_HANDLE_VALUE:
        return False
    try:
        index = 0
        while True:
            devinfo = _SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)
            if not _setupapi.SetupDiEnumDeviceInfo(dev_info_set, index, ctypes.byref(devinfo)):
                return False
            index += 1
            buf = ctypes.create_unicode_buffer(512)
            required = wintypes.DWORD()
            ok = _setupapi.SetupDiGetDeviceInstanceIdW(
                dev_info_set, ctypes.byref(devinfo), buf, 512, ctypes.byref(required)
            )
            if ok and needle in buf.value:
                return True
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(dev_info_set)


# -- PicoScope-7-App starten --------------------------------------------------


def launch_app() -> bool:
    """Startet die PicoScope-7-App (fuer die eigentliche Bedienung des
    Oszilloskops, siehe README.md: LabControl kopiert deren Funktionsumfang
    bewusst nicht). Liefert False, falls die exe nicht gefunden wurde."""
    install_dir = _find_install_dir("PicoScope.exe")
    if install_dir is None:
        return False
    subprocess.Popen([str(install_dir / "PicoScope.exe")], cwd=str(install_dir))
    return True


def _check(status: int, what: str) -> int:
    """ps2000-Konvention: status > 0 = Erfolg (siehe picosdk-Beispiele,
    assert_pico2000_ok). Anders als bei ps2000a ist 0 hier KEIN Erfolgscode."""
    if status <= 0:
        raise PicoScope2000Error(f"{what} fehlgeschlagen (Status {status}).")
    return status


class PicoScope2000:
    def __init__(self, chandle: int):
        self._chandle = ctypes.c_int16(chandle)
        self._closed = False

    # -- Discovery / Verbindung ----------------------------------------------

    @classmethod
    def discover(cls) -> list:
        """Liefert die Seriennummer eines oeffenbaren Geraets (max. 1 Eintrag).

        Siehe Moduldoc: die ps2000-API kennt keine Enumeration ohne Oeffnen.
        """
        try:
            unit = cls.open_first()
        except PicoScope2000Error:
            return []
        try:
            return [unit.get_info().serial]
        finally:
            unit.close()

    @classmethod
    def open_first(cls) -> "PicoScope2000":
        _require_dll()
        try:
            status = _ps.ps2000_open_unit()
        except OSError as exc:
            raise PicoScope2000Error(f"ps2000_open_unit fehlgeschlagen: {exc}") from exc
        if status <= 0:
            raise PicoScope2000Error(
                f"Kein PicoScope 2000 gefunden (ps2000_open_unit lieferte Status {status})."
            )
        return cls(status)

    def close(self) -> None:
        if self._closed:
            return
        try:
            _ps.ps2000_close_unit(self._chandle)
        except OSError:
            pass
        self._closed = True

    def __enter__(self) -> "PicoScope2000":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- Abfragen --------------------------------------------------------------

    def get_info(self) -> UnitInfo:
        return UnitInfo(
            variant=self._query_info(_INFO_VARIANT),
            serial=self._query_info(_INFO_SERIAL),
        )

    def _query_info(self, info_type: int) -> str:
        buf = ctypes.create_string_buffer(256)
        try:
            status = _ps.ps2000_get_unit_info(self._chandle, buf, 256, info_type)
        except OSError as exc:
            raise PicoScope2000Error(f"ps2000_get_unit_info fehlgeschlagen: {exc}") from exc
        _check(status, "ps2000_get_unit_info")
        return buf.value.decode(errors="replace")

    # -- Erfassung ---------------------------------------------------------------

    def capture_block(
        self,
        channel: str = "A",
        voltage_range: str = "2V",
        num_samples: int = 2000,
        timebase: int = 8,
    ) -> BlockCapture:
        """Blockweise Erfassung eines Kanals, freilaufend (kein echter Trigger).

        Deaktiviert den jeweils anderen Kanal -- laut Pico-Doku sind sonst
        nicht alle Timebases erreichbar, weil nach dem Oeffnen standardmaessig
        beide Kanaele aktiv sind.
        """
        if channel not in CHANNEL_MAP:
            raise PicoScope2000Error(f"Unbekannter Kanal: {channel!r}")
        if voltage_range not in VOLTAGE_RANGE_CODES:
            raise PicoScope2000Error(f"Unbekannter Spannungsbereich: {voltage_range!r}")

        range_code = VOLTAGE_RANGE_CODES[voltage_range]
        try:
            for name, code in CHANNEL_MAP.items():
                enabled = 1 if name == channel else 0
                _check(
                    _ps.ps2000_set_channel(self._chandle, code, enabled, 1, range_code),
                    f"ps2000_set_channel({name})",
                )

            # Freilaufend: sehr kurzes Auto-Trigger-Timeout statt eines echten
            # Trigger-Levels -- fuer einfache Live-Anzeige voellig ausreichend.
            _check(
                _ps.ps2000_set_trigger(self._chandle, CHANNEL_MAP[channel], 0, 0, 0, 1),
                "ps2000_set_trigger",
            )

            time_interval = ctypes.c_int32()
            time_units = ctypes.c_int32()
            oversample = ctypes.c_int16(1)
            max_samples_return = ctypes.c_int32()
            _check(
                _ps.ps2000_get_timebase(
                    self._chandle, timebase, num_samples,
                    ctypes.byref(time_interval), ctypes.byref(time_units),
                    oversample, ctypes.byref(max_samples_return),
                ),
                "ps2000_get_timebase",
            )

            time_indisposed_ms = ctypes.c_int32()
            _check(
                _ps.ps2000_run_block(
                    self._chandle, num_samples, timebase, oversample,
                    ctypes.byref(time_indisposed_ms),
                ),
                "ps2000_run_block",
            )

            ready = ctypes.c_int16(0)
            while ready.value == 0:
                ready = ctypes.c_int16(_ps.ps2000_ready(self._chandle))

            buffer = (ctypes.c_int16 * num_samples)()
            overflow = ctypes.c_int16()
            n_values = _check(
                _ps.ps2000_get_values(
                    self._chandle, ctypes.byref(buffer), None, None, None,
                    ctypes.byref(overflow), num_samples,
                ),
                "ps2000_get_values",
            )
        except OSError as exc:
            raise PicoScope2000Error(f"Blockerfassung fehlgeschlagen: {exc}") from exc

        millivolts = adc2mV(buffer[:n_values], range_code, _MAX_ADC)
        return BlockCapture(
            channel=channel,
            voltage_range=voltage_range,
            sample_interval_ns=float(time_interval.value),
            millivolts=millivolts,
        )

    def measure(
        self,
        channel: str = "A",
        voltage_range: str = "2V",
        num_samples: int = 2000,
        timebase: int = 8,
    ) -> Measurement:
        """Blockerfassung + daraus abgeleitete Kennwerte (siehe
        picoscope2000.common.Measurement) -- fuer den Testablauf
        (testcase_model.PICO_ACTIONS): eine einzelne Aktion braucht nur EINEN
        dieser Werte, misst aber trotzdem alle gemeinsam, da eine zusaetzliche
        Erfassung durch das ~4,5s lange Verbinden/Trennen (siehe
        picoscope2000/README.md) unverhaeltnismaessig teuer waere."""
        capture = self.capture_block(
            channel=channel, voltage_range=voltage_range,
            num_samples=num_samples, timebase=timebase,
        )
        values = capture.millivolts
        vmax = max(values)
        vmin = min(values)
        vrms = (sum(v * v for v in values) / len(values)) ** 0.5
        return Measurement(
            channel=channel,
            voltage_range=voltage_range,
            vmax=vmax,
            vmin=vmin,
            vpp=vmax - vmin,
            vrms=vrms,
        )
