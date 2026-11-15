"""Firmware ueber CAN flashen (PI-54, 0.65.0): canflash aus neXtBSW als Unterprozess.

canflash bleibt die eine Wahrheit fuer Bootloader-Protokoll, Bildpruefung und Fingerprint; LabControl
startet es nur. Weil LabControl den CAN-Kanal des Prueflings offen haelt, gibt der DeviceWorker ihn fuer
die Dauer frei (hold_can) und oeffnet ihn danach wieder; UDS und XCP verbinden sich dann neu.

Ablauf einer Anfrage:
  start() -> hold_requested(can, True) -> Worker schliesst den Kanal -> can_hold_changed(can, True)
  -> canflash laeuft (Fortschritt aus den Zeilen "( 40 %)") -> hold_requested(can, False)
  -> Worker oeffnet wieder -> can_hold_changed(can, False) -> finished(...)

Am simulierten Bus (can:mock:SIM) ohne eingestelltes neXtBSW wird nur nachgebildet (Datei wird gelesen,
Fortschritt in zehn Schritten), damit Ablaeufe ohne Hardware laufen.
"""
from __future__ import annotations

import hashlib
import logging
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from i18n import tr

logger = logging.getLogger(__name__)

TIMEOUT_S = 600.0            # canflash selbst bricht bei fehlender Antwort viel frueher ab
HOLD_CONFIRM_S = 5.0         # Worker bestaetigt das Freigeben/Wiederoeffnen des Kanals
SIM_STEP_MS = 100
FIRMWARE_SUFFIXES = (".hex", ".ihex", ".srec", ".s19", ".mot")
_PERCENT = re.compile(r"\(\s*(\d{1,3})\s*%\)")


def canflash_script(nextbsw_dir: str) -> Path:
    return Path(nextbsw_dir).expanduser() / "tools" / "canflash" / "canflash.py"


def nextbsw_python(nextbsw_dir: str) -> Path:
    base = Path(nextbsw_dir).expanduser() / ".venv"
    return base / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def vector_channel_index(serial: int, hw_channel: int) -> int:
    """canflash nimmt fuer Vector den globalen Kanalindex (ohne app_name); LabControl merkt sich Seriennummer
    und Hardware-Kanal (can_bus/driver.py). Ohne passenden Eintrag bleibt der Hardware-Kanal."""
    try:
        from can.interfaces.vector import get_channel_configs
        for config in get_channel_configs():
            if config.serial_number == serial and config.hw_channel == hw_channel:
                return int(config.channel_index)
    except Exception as exc:  # noqa: BLE001 -- ohne Vector-Treiber gibt es nichts nachzuschlagen
        logger.debug("Vector-Kanalliste nicht lesbar: %s", exc)
    return hw_channel


def bus_arguments(can_device: str, channel_index=vector_channel_index) -> list[str]:
    """CAN-Interface des Prueflings ("can:vector:59177:0") -> --interface/--channel fuer canflash."""
    parts = can_device.split(":", 2)
    if len(parts) != 3 or parts[0] != "can":
        raise ValueError(tr("CAN-Interface des Prüflings nicht eingestellt"))
    interface, channel = parts[1], parts[2]
    if interface == "vector":
        match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", channel)
        index = channel_index(int(match.group(1)), int(match.group(2))) if match else int(channel)
        return ["--interface", "vector", "--channel", str(index)]
    if channel.strip().isdigit():
        return ["--interface", interface, "--channel", channel.strip()]
    raise ValueError(tr("Flashen über {interface} wird von canflash nicht unterstützt", interface=interface))


def build_command(nextbsw_dir: str, can_device: str, file_path: str, wait_s: float = 0.0, ecu: str = "",
                  channel_index=vector_channel_index) -> tuple[str, list[str]]:
    """(Programm, Argumente) fuer QProcess."""
    args = [str(canflash_script(nextbsw_dir)), "flash", str(file_path), *bus_arguments(can_device, channel_index),
            "--progress", "steps"]
    if wait_s > 0:
        args += ["--wait", f"{wait_s:g}"]
    if ecu:
        args += ["--ecu", ecu]
    return str(nextbsw_python(nextbsw_dir)), args


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CanFlashService(QObject):
    # an DeviceWorker.hold_can (queued): CAN-Kanal freigeben (True) / wieder oeffnen (False)
    hold_requested = Signal(str, bool)
    # Anfrage, Prozent (-1 = unbekannt), Ausgabezeile
    progress = Signal(int, int, str)
    # Anfrage, ok, Meldung
    finished = Signal(int, bool, str)
    # {"busy", "percent", "file", "message", "ok"} fuer Kachel/Status
    state_changed = Signal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config: dict = {}
        self._simulation = False
        self._requests = 0
        self._job: dict | None = None
        self._process: QProcess | None = None
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._on_hold_timeout)
        self._run_timer = QTimer(self)
        self._run_timer.setSingleShot(True)
        self._run_timer.timeout.connect(self._on_run_timeout)
        self._sim_timer = QTimer(self)
        self._sim_timer.timeout.connect(self._sim_tick)
        self._state: dict = {"busy": False, "percent": -1, "file": "", "message": "", "ok": None}

    # -- Konfiguration -------------------------------------------------------------------

    def configure(self, dut_config: dict, simulation: bool) -> None:
        self._config = dict(dut_config or {})
        self._simulation = simulation

    def can_device(self) -> str:
        device = self._config.get("can_device") or ""
        return device or ("can:mock:SIM" if self._simulation else "")

    def _simulated(self) -> bool:
        return self.can_device().startswith("can:mock:") and not self._config.get("nextbsw_dir")

    def problem(self) -> str:
        """Leer = Flashen moeglich; sonst der Grund (Vorpruefung eines Laufs, Kachel)."""
        if self._simulated():
            return ""
        nextbsw = self._config.get("nextbsw_dir") or ""
        if not nextbsw:
            return tr("neXtBSW-Ordner für canflash fehlt (Einstellungen → Prüfling)")
        if not canflash_script(nextbsw).is_file():
            return tr("canflash nicht gefunden: {path}", path=str(canflash_script(nextbsw)))
        if not nextbsw_python(nextbsw).is_file():
            return tr("Python des neXtBSW-Ordners fehlt: {path}", path=str(nextbsw_python(nextbsw)))
        if not self.can_device():
            return tr("CAN-Interface des Prüflings nicht eingestellt")
        try:
            bus_arguments(self.can_device(), lambda _s, ch: ch)
        except ValueError as exc:
            return str(exc)
        return ""

    def busy(self) -> bool:
        return self._job is not None

    def state(self) -> dict:
        return dict(self._state)

    # -- Bedienung -----------------------------------------------------------------------

    def start(self, file_path: str, wait_s: float = 0.0, ecu: str = "", source: str = "") -> int:
        """Flashen beginnen; das Ergebnis kommt ueber `finished` mit der zurueckgegebenen Nummer (immer
        nach der Rueckkehr, auch bei sofort erkannten Fehlern)."""
        self._requests += 1
        request = self._requests
        error = ""
        if self._job is not None:
            error = tr("Es wird schon geflasht")
        elif not file_path:
            error = tr("Keine Firmware-Datei gewählt")
        elif not Path(file_path).is_file():
            error = tr("Firmware-Datei nicht gefunden: {path}", path=file_path)
        else:
            error = self.problem()
        if error:
            QTimer.singleShot(0, lambda: self.finished.emit(request, False, error))
            return request
        try:
            digest = file_sha256(file_path)
        except OSError as exc:
            QTimer.singleShot(0, lambda: self.finished.emit(request, False, str(exc)))
            return request
        ecu = ecu or self._config.get("flash_ecu", "")     # Vorgabe aus Einstellungen -> Pruefling
        self._job = {"request": request, "file": file_path, "wait": max(0.0, float(wait_s or 0.0)), "ecu": ecu,
                     "can": self.can_device(), "started": time.monotonic(), "lines": [], "held": False,
                     "result": None, "percent": -1}
        logger.info("Flashen über CAN (%s): %s, SHA-256 %s, Interface %s", source or "?", file_path, digest,
                    self.can_device())
        self._set_state(busy=True, percent=0, file=Path(file_path).name, message=tr("Kanal wird freigegeben …"),
                        ok=None)
        if self._simulated():
            self._sim_timer.start(SIM_STEP_MS)
            return request
        self._hold_timer.start(round(HOLD_CONFIRM_S * 1000))
        self.hold_requested.emit(self._job["can"], True)
        return request

    def cancel(self) -> None:
        """Laufenden Vorgang abbrechen (ALLE AUS, Stopp). canflash wird beendet; der Bootloader bleibt dann
        im Zweifel aktiv, bis neu geflasht wird."""
        if self._job is None:
            return
        if self._sim_timer.isActive():
            self._sim_timer.stop()
            self._complete(False, tr("Flashen abgebrochen"))
            return
        if self._process is not None:
            self._job["result"] = (False, tr("Flashen abgebrochen"))
            self._process.kill()
        elif not self._job["held"]:
            self._hold_timer.stop()
            self._complete(False, tr("Flashen abgebrochen"))

    # -- Worker-Rueckmeldung -------------------------------------------------------------------

    def on_can_hold_changed(self, device_id: str, held: bool) -> None:
        job = self._job
        if job is None or device_id != job["can"]:
            return
        self._hold_timer.stop()
        if held and not job["held"]:
            job["held"] = True
            self._launch()
        elif not held and job["result"] is not None:
            self._complete(*job["result"])

    def _on_hold_timeout(self) -> None:
        job = self._job
        if job is None:
            return
        if job["result"] is None:
            # Freigeben nicht bestaetigt: nichts gestartet, Kanal sicherheitshalber wieder anfordern
            self.hold_requested.emit(job["can"], False)
            self._complete(False, tr("CAN-Kanal {can} wurde nicht freigegeben", can=job["can"]))
        else:
            self._complete(*job["result"])    # Wiederoeffnen nicht bestaetigt: der Worker versucht es weiter

    # -- canflash-Prozess ---------------------------------------------------------------------

    def _launch(self) -> None:
        job = self._job
        nextbsw = self._config.get("nextbsw_dir", "")
        try:
            program, args = build_command(nextbsw, job["can"], job["file"], job["wait"], job["ecu"])
        except ValueError as exc:
            self._release(False, str(exc))
            return
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setWorkingDirectory(str(Path(nextbsw).expanduser()))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        process.setProcessEnvironment(env)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._on_process_finished)
        process.errorOccurred.connect(self._on_process_error)
        self._process = process
        self._buffer = ""
        logger.info("canflash: %s %s", program, " ".join(args))
        self._set_state(message=tr("canflash läuft …"))
        self._run_timer.start(round(TIMEOUT_S * 1000))
        process.start(program, args)

    def _read_output(self) -> None:
        if self._process is None or self._job is None:
            return
        self._buffer += bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._buffer = self._buffer.replace("\r\n", "\n").replace("\r", "\n")
        *lines, self._buffer = self._buffer.split("\n")
        for line in (raw.rstrip() for raw in lines):
            if line:
                self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        job = self._job
        job["lines"].append(line)
        del job["lines"][:-40]
        match = _PERCENT.search(line)
        if match:
            job["percent"] = min(100, int(match.group(1)))
            self._set_state(percent=job["percent"], message=tr("Download {p} %", p=job["percent"]))
        logger.info("canflash: %s", line)
        self.progress.emit(job["request"], job["percent"], line)

    def _on_process_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart and self._job is not None:
            self._run_timer.stop()
            process, self._process = self._process, None
            if process is not None:
                process.deleteLater()
            self._release(False, tr("canflash ließ sich nicht starten"))

    def _on_process_finished(self, exit_code: int, _status) -> None:
        if self._process is None or self._job is None:
            return
        self._read_output()
        if self._buffer.strip():
            self._handle_line(self._buffer.strip())
            self._buffer = ""
        self._run_timer.stop()
        process, self._process = self._process, None
        process.deleteLater()
        job = self._job
        elapsed = time.monotonic() - job["started"]
        if job["result"] is not None:          # abgebrochen
            self._release(*job["result"])
            return
        if exit_code == 0:
            self._release(True, tr("{file} geflasht ({s:.0f} s)", file=Path(job["file"]).name, s=elapsed))
            return
        detail = next((line for line in reversed(job["lines"]) if not _PERCENT.search(line)), "")
        self._release(False, tr("canflash Exit {code}: {detail}", code=exit_code, detail=detail or "–"))

    def _on_run_timeout(self) -> None:
        if self._process is not None and self._job is not None:
            self._job["result"] = (False, tr("canflash nach {s:.0f} s abgebrochen", s=TIMEOUT_S))
            self._process.kill()

    def _release(self, ok: bool, message: str) -> None:
        """canflash ist fertig: Ergebnis merken, Kanal wieder oeffnen lassen, auf die Bestaetigung warten."""
        job = self._job
        job["result"] = (ok, message)
        self._set_state(message=tr("Kanal wird wieder geöffnet …"))
        self._hold_timer.start(round(HOLD_CONFIRM_S * 1000))
        self.hold_requested.emit(job["can"], False)

    # -- Nachbildung --------------------------------------------------------------------------

    def _sim_tick(self) -> None:
        job = self._job
        if job is None:
            self._sim_timer.stop()
            return
        job["percent"] = max(0, job["percent"]) + 10
        self._handle_line(f"  {job['percent'] * 1024:7d}/102400 bytes ({job['percent']:3d} %)")
        if job["percent"] >= 100:
            self._sim_timer.stop()
            self._complete(True, tr("{file} geflasht (Simulation)", file=Path(job["file"]).name))

    # -- Abschluss ---------------------------------------------------------------------------

    def _complete(self, ok: bool, message: str) -> None:
        job, self._job = self._job, None
        self._hold_timer.stop()
        self._run_timer.stop()
        if job is None:
            return
        (logger.info if ok else logger.warning)("Flashen über CAN: %s", message)
        self._set_state(busy=False, percent=100 if ok else job["percent"], message=message, ok=ok)
        self.finished.emit(job["request"], ok, message)

    def _set_state(self, **changes) -> None:
        self._state.update(changes)
        self.state_changed.emit(dict(self._state))
