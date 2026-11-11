"""SEGGER J-Link ueber den J-Link Commander (JLink.exe / JLinkExe), Verbund V3 (PI-31 a).

Bewusst der Commander als eigener Prozess je Bedienung statt einer dauerhaft geoeffneten DLL
(pylink o. ae.): LabControl haelt den J-Link nur fuer die Dauer EINES Schritts (Flashen, Reset,
Anhalten, Starten). Dazwischen bleibt er frei fuer den Debugger in der IDE.

Nichts hier fragt den J-Link im Hintergrund ab -- jede Abfrage oeffnet die Sonde, und bei der
EDU-Lizenz erscheint einmal je 24 h ein Bestaetigungsdialog, der den Commander bis zum Wegklicken
anhaelt. Deshalb hat jeder Aufruf ein Zeitlimit; laeuft es ab, nennt die Meldung den Dialog als
wahrscheinlichen Grund.

Qt-frei. Die Auswertung der Commander-Ausgabe (parse_*) ist von der Prozessausfuehrung getrennt
und laesst sich mit aufgezeichneten Ausgaben pruefen.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

INTERFACES = ("SWD", "JTAG")
DEFAULT_SPEED_KHZ = 4000
TIMEOUT_S = 30.0
FLASH_TIMEOUT_S = 180.0
FIRMWARE_SUFFIXES = (".hex", ".bin", ".srec", ".s19", ".mot", ".elf", ".axf", ".out")

EDU_HINT = ("J-Link antwortet nicht -- wartet vermutlich der Bestätigungsdialog der EDU-Lizenz "
            "(einmal je 24 h) auf einen Klick")


class JLinkError(Exception):
    pass


@dataclass
class ProbeInfo:
    serial: str = ""
    product: str = ""
    firmware: str = ""
    vtref: float | None = None      # Zielspannung in V
    core: str = ""                  # z. B. "Cortex-M4" (nur nach Verbindung zum Ziel)
    connected: bool = False         # Zielverbindung hergestellt


@dataclass
class Result:
    ok: bool
    message: str
    output: str = ""
    info: ProbeInfo = field(default_factory=ProbeInfo)


def find_commander(explicit: str = "") -> str:
    """Pfad zum J-Link Commander oder "" (nicht installiert)."""
    if explicit and os.path.isfile(explicit):
        return explicit
    for name in ("JLink.exe", "JLinkExe"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        candidates = []
        for root in roots:
            candidates += glob.glob(os.path.join(root, "SEGGER", "JLink*", "JLink.exe"))
        # Neueste Version zuerst (Ordner "JLink_V794j" sortiert hinter "JLink_V780")
        for path in sorted(candidates, reverse=True):
            return path
    else:
        for path in ("/opt/SEGGER/JLink/JLinkExe", "/usr/bin/JLinkExe", "/usr/local/bin/JLinkExe"):
            if os.path.isfile(path):
                return path
    return ""


# -- Ausgabe auswerten -----------------------------------------------------------

_EMU_RE = re.compile(r"J-Link\[\d+\]:.*?Serial number:\s*(\d+)(?:.*?ProductName:\s*([^,\r\n]+))?", re.I)
_VTREF_RE = re.compile(r"VTref\s*=\s*([\d.]+)\s*V", re.I)
_SERIAL_RE = re.compile(r"^S/N:\s*(\d+)", re.I | re.M)
_FIRMWARE_RE = re.compile(r"^Firmware:\s*(.+)$", re.I | re.M)
_CORE_RE = re.compile(r"(Cortex-[AMR]\d+\+?|ARM\d+\w*)\s+identified", re.I)
_ERROR_RE = re.compile(
    r"(\*{4,}\s*Error|^Error[: ]|Cannot connect|Could not|Failed to|Connecting to target failed|"
    r"Unknown command|Unknown device|No J-Link found|Target connection not established|"
    r"Download failed|Verify failed|Could not open file|File not found)", re.I | re.M)


def parse_emulators(output: str) -> list[tuple[str, str]]:
    """ShowEmuList -> [(Seriennummer, Produktname)]."""
    return [(m.group(1), (m.group(2) or "").strip()) for m in _EMU_RE.finditer(output)]


def parse_info(output: str) -> ProbeInfo:
    info = ProbeInfo()
    if m := _SERIAL_RE.search(output):
        info.serial = m.group(1)
    if m := _FIRMWARE_RE.search(output):
        info.firmware = m.group(1).strip()
    vtrefs = _VTREF_RE.findall(output)
    if vtrefs:
        info.vtref = float(vtrefs[-1])
    if m := _CORE_RE.search(output):
        info.core = m.group(1)
        info.connected = True
    return info


def first_error(output: str) -> str:
    """Erste Fehlerzeile der Commander-Ausgabe ("" = keine)."""
    for line in output.splitlines():
        if _ERROR_RE.search(line.strip()):
            return line.strip().strip("*").strip()
    return ""


# -- Commander ausfuehren --------------------------------------------------------

class JLink:
    """Ein J-Link mit fester Zielkonfiguration (Baustein, Schnittstelle, Takt, optional Seriennummer)."""

    def __init__(self, commander: str, device: str, interface: str = "SWD",
                 speed_khz: int = DEFAULT_SPEED_KHZ, serial: str = "") -> None:
        if interface not in INTERFACES:
            raise ValueError(f"Schnittstelle {interface!r} nicht unterstützt")
        self.commander = commander
        self.device = device.strip()
        self.interface = interface
        self.speed_khz = int(speed_khz)
        self.serial = serial.strip()

    def _args(self, script: str, with_target: bool) -> list[str]:
        args = [self.commander, "-NoGui", "1", "-ExitOnError", "1"]
        if self.serial:
            args += ["-USB", self.serial]
        if with_target:
            args += ["-Device", self.device, "-If", self.interface, "-Speed", str(self.speed_khz)]
        return args + ["-CommanderScript", script]

    def run(self, commands: list[str], with_target: bool = True, timeout: float = TIMEOUT_S) -> Result:
        """Fuehrt ein Commander-Skript aus. ok = Rueckgabewert 0 und keine Fehlerzeile."""
        if not self.commander:
            return Result(False, "J-Link Commander nicht gefunden (SEGGER J-Link Software installieren)")
        if with_target and not self.device:
            return Result(False, "Kein Zielbaustein eingestellt (Einstellungen → Geräte → J-Link)")
        handle, script = tempfile.mkstemp(prefix="labcontrol-jlink-", suffix=".jlink")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write("\n".join([*commands, "exit"]) + "\n")
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                proc = subprocess.Popen(self._args(script, with_target), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                        encoding="utf-8", errors="replace", creationflags=flags)
            except OSError as exc:
                return Result(False, f"J-Link Commander nicht startbar: {exc}")
            try:
                output, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Abbrechen, aber nicht ewig auf die Ausgabe warten: haelt ein Unterprozess die Leitung
                # offen, wuerde communicate() sonst bis zu dessen Ende blockieren.
                proc.kill()
                try:
                    output, _ = proc.communicate(timeout=2.0)
                except subprocess.TimeoutExpired:
                    output = ""
                return Result(False, f"{EDU_HINT} (Zeitlimit {timeout:.0f} s)", output or "")
        finally:
            try:
                os.remove(script)
            except OSError:
                pass
        error = first_error(output)
        info = parse_info(output)
        if proc.returncode != 0 or error:
            return Result(False, error or f"J-Link Commander beendet mit Code {proc.returncode}", output, info)
        return Result(True, "", output, info)

    # -- Bedienungen ---------------------------------------------------------------

    def probe_info(self) -> Result:
        """Nur die Sonde (Seriennummer, Firmware, Zielspannung) -- verbindet sich NICHT mit dem Ziel."""
        return self.run([], with_target=False)

    def check_target(self) -> Result:
        """Mit dem Ziel verbinden und wieder trennen (Kern erkannt?). Der Kern laeuft dabei weiter."""
        result = self.run(["connect"])
        if result.ok and not result.info.connected:
            return Result(False, "Ziel nicht erkannt", result.output, result.info)
        return result

    def flash(self, path: str) -> Result:
        if not os.path.isfile(path):
            return Result(False, f"Datei nicht gefunden: {path}")
        if not path.lower().endswith(FIRMWARE_SUFFIXES):
            return Result(False, f"Unbekanntes Dateiformat ({os.path.basename(path)}), erwartet "
                                 f"{', '.join(FIRMWARE_SUFFIXES)}")
        # .bin braucht eine Zieladresse -- die kennt nur der Nutzer; HEX/SREC/ELF tragen sie selbst.
        if path.lower().endswith(".bin"):
            return Result(False, ".bin braucht eine Startadresse -- bitte HEX, SREC oder ELF verwenden")
        return self.run(["connect", "r", "h", f'loadfile "{path}"', "r", "g"], timeout=FLASH_TIMEOUT_S)

    def reset(self) -> Result:
        return self.run(["connect", "r", "g"])

    def halt(self) -> Result:
        return self.run(["connect", "h"])

    def go(self) -> Result:
        return self.run(["connect", "g"])


def list_emulators(commander: str) -> list[tuple[str, str]]:
    """Angeschlossene J-Links (Seriennummer, Produkt). Oeffnet keine Sonde."""
    if not commander:
        return []
    probe = JLink(commander, "")
    result = probe.run(["ShowEmuList"], with_target=False, timeout=10.0)
    return parse_emulators(result.output)
