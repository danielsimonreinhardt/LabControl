"""Rotierendes Datei-Logging fuer die Labor-App, bevorzugt auf ein Netzlaufwerk.

Die gebaute .exe laeuft mit --windowed (siehe .github/workflows/build-exe.yml),
stdout/stderr gehen also ins Leere. Ohne eigenes Logging waere ein
Sicherheitsabbruch (siehe safety.py) waehrend eines unbeaufsichtigten
Uebernacht-Laufs im Nachhinein nicht mehr nachvollziehbar.

Netzlaufwerk (Raspberry Pi, Nutzer 10.11.2026: "Log vorrangig auf ein
Netzlaufwerk", um die SD-Karte zu schonen):

- Ist ein Netzlaufwerk eingestellt (Einstellungen > Allgemein oder
  LABCONTROL_NETWORK_DIR, die Variable gewinnt), schreibt LabControl dorthin,
  je Rechner in einen eigenen Unterordner (<netzlaufwerk>/<rechnername>/labdash.log,
  seit 0.31.0; dort liegen auch die anderen Betriebsdaten, siehe data_store),
  damit PC und Pi dasselbe Netzlaufwerk nutzen koennen.
- Ist er nicht erreichbar, schreibt LabControl in die lokale labdash.log im
  Datenordner und versucht es jede Minute erneut. Beide Wechsel stehen im Log.
- Unter Linux zaehlt ein Ordner auf der SD-Karte nicht als Ziel: weder auf
  dem Dateisystem von "/" (ein nicht eingehaengter Mountpoint ist ein leerer
  Ordner dort) noch auf einer anderen mmcblk-Partition (/boot/firmware). Eine
  USB-SSD oder ein tmpfs gehen, gedacht ist ein Netzlaufwerk (cifs/nfs).
- Geschrieben wird in einem eigenen Thread. Ein haengendes Netzlaufwerk
  blockiert so weder die Oberflaeche noch den Geraete-Worker; laeuft die
  Warteschlange voll, werden Meldungen verworfen und gezaehlt.
- Die lokale Datei wird erst angelegt, wenn wirklich etwas lokal landet.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import socket
import sys
import threading
import time
from pathlib import Path

from paths import app_dir

LOG_PATH = app_dir() / "labdash.log"
NETWORK_DIR_ENV = "LABCONTROL_NETWORK_DIR"
LOCAL_MAX_BYTES = 1_000_000
LOCAL_BACKUPS = 3
NETWORK_MAX_BYTES = 5_000_000
NETWORK_BACKUPS = 10
RETRY_INTERVAL_S = 60.0
QUEUE_SIZE = 10_000
STOP_TIMEOUT_S = 3.0

MAIN = "main"
ACCESS = "access"

_FORMATS = {
    MAIN: "%(asctime)s %(levelname)s [%(name)s] %(message)s",
    ACCESS: "%(asctime)s %(message)s",
}

logger = logging.getLogger(__name__)


def host_name() -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in socket.gethostname()) or "rechner"


def mount_source(path: str, mounts_text: str) -> str:
    """Geraet/Quelle des Dateisystems, auf dem `path` liegt (laengster passender
    Mountpoint aus /proc/self/mounts; Leerzeichen dort als \\040 kodiert)."""
    best, source = "", ""
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        mountpoint = parts[1].replace("\\040", " ")
        inside = path == mountpoint or path.startswith(mountpoint.rstrip("/") + "/")
        if inside and len(mountpoint) >= len(best):
            best, source = mountpoint, parts[0]
    return source


def network_dir_problem(directory: Path) -> str:
    """Leer, wenn `directory` als Log-Ziel taugt, sonst der Grund (Anzeige/Log). Unter Linux
    zaehlt nichts auf der SD-Karte: weder das Dateisystem von "/" (dort landet auch ein
    nicht eingehaengter Mountpoint) noch eine andere mmcblk-Partition wie /boot/firmware."""
    try:
        if not directory.is_dir():
            return "Ordner nicht erreichbar"
        if sys.platform != "win32":
            if os.stat(directory).st_dev == os.stat("/").st_dev:
                return "liegt auf der Systemplatte, Netzlaufwerk nicht eingehängt?"
            try:
                mounts = Path("/proc/self/mounts").read_text(encoding="utf-8")
            except OSError:
                mounts = ""
            if mount_source(os.path.realpath(directory), mounts).startswith("/dev/mmcblk"):
                return "liegt auf der SD-Karte"
        if not os.access(directory, os.W_OK):
            return "kein Schreibrecht"
    except OSError as exc:
        return str(exc)
    return ""


class _RaisingRotatingHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler meldet Schreibfehler sonst nur auf stderr; hier soll der
    Fehler den Wechsel auf die lokale Datei ausloesen."""

    def handleError(self, record: logging.LogRecord) -> None:   # noqa: N802 -- logging-API
        raise   # wird nur aus dem except-Block von emit() aufgerufen


class _FailoverWriter:
    """Schreibt Datensaetze ins Netzlaufwerk oder, falls das nicht geht, lokal.
    Wird nur im Log-Thread benutzt."""

    def __init__(self, local_path: Path, network_name: str, fmt: str,
                 local_max_bytes: int, local_backups: int, eager: bool, subdir: str = "") -> None:
        # eager: Ziel sofort pruefen (Haupt-Log, Status in den Einstellungen). Sonst erst beim
        # ersten Datensatz -- das Zugriffsprotokoll soll ohne Zugriffe keine Datei anlegen.
        self._eager = eager
        self._local_path = local_path
        self._network_name = network_name
        self._subdir = subdir          # je Rechner ein Unterordner im Netzlaufwerk
        self._formatter = logging.Formatter(fmt)
        self._local_max_bytes = local_max_bytes
        self._local_backups = local_backups
        self._local: logging.Handler | None = None
        self._network: logging.Handler | None = None
        self._network_dir: Path | None = None
        self._next_try = 0.0
        self._fallback_since: float | None = None
        self.state = {"target": "local", "path": str(local_path), "reason": ""}

    # -- Ziel ----------------------------------------------------------------

    def set_network_dir(self, directory: Path | None) -> None:
        self._close_network()
        self._network_dir = directory
        self._next_try = 0.0
        self._fallback_since = None
        if directory is None:
            self._set_state("local", self._local_path, "")
        elif self._eager:
            self._try_network()

    def _try_network(self) -> bool:
        self._next_try = time.monotonic() + RETRY_INTERVAL_S
        problem = network_dir_problem(self._network_dir)
        if not problem:
            folder = self._network_dir / self._subdir if self._subdir else self._network_dir
            path = folder / self._network_name
            try:
                folder.mkdir(exist_ok=True)
                handler = _RaisingRotatingHandler(path, maxBytes=NETWORK_MAX_BYTES,
                                                  backupCount=NETWORK_BACKUPS, encoding="utf-8")
            except OSError as exc:
                problem = str(exc)
            else:
                handler.setFormatter(self._formatter)
                self._network = handler
                if self._fallback_since is not None:
                    self._write_note_network(
                        f"Netzlaufwerk wieder erreichbar. Meldungen seit "
                        f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self._fallback_since))} "
                        f"stehen lokal in {self._local_path}")
                self._fallback_since = None
                self._set_state("network", path, "")
                return True
        if self._fallback_since is None:
            self._fallback_since = time.time()
            self._write_local(self._note(logging.WARNING,
                                         f"Netzlaufwerk {self._network_dir} nicht nutzbar ({problem}) "
                                         f"-- schreibe lokal nach {self._local_path}, neuer Versuch jede Minute"))
        self._set_state("local", self._local_path, problem)
        return False

    def _set_state(self, target: str, path: Path, reason: str) -> None:
        self.state = {"target": target, "path": str(path), "reason": reason}

    # -- Schreiben -------------------------------------------------------------

    def write(self, record: logging.LogRecord) -> None:
        if self._network is None and self._network_dir is not None and time.monotonic() >= self._next_try:
            self._try_network()
        if self._network is not None:
            try:
                self._network.emit(record)
                return
            except Exception as exc:   # noqa: BLE001 -- jeder Schreibfehler fuehrt zum Wechsel
                self._close_network()
                self._switch_local_after_error(exc)
        self._write_local(record)

    def _switch_local_after_error(self, exc: Exception) -> None:
        self._fallback_since = time.time()
        self._next_try = time.monotonic() + RETRY_INTERVAL_S
        self._write_local(self._note(logging.WARNING,
                                     f"Schreiben ins Netzlaufwerk {self._network_dir} fehlgeschlagen ({exc}) "
                                     f"-- schreibe lokal nach {self._local_path}, neuer Versuch jede Minute"))
        self._set_state("local", self._local_path, str(exc))

    def _write_local(self, record: logging.LogRecord) -> None:
        if self._local is None:
            try:
                handler = logging.handlers.RotatingFileHandler(
                    self._local_path, maxBytes=self._local_max_bytes,
                    backupCount=self._local_backups, encoding="utf-8")
            except OSError:
                return   # schreibgeschuetzt o.ae. -- die App laeuft trotzdem
            handler.setFormatter(self._formatter)
            self._local = handler
        self._local.emit(record)

    def _write_note_network(self, text: str) -> None:
        try:
            self._network.emit(self._note(logging.INFO, text))
        except Exception:   # noqa: BLE001
            pass

    @staticmethod
    def _note(level: int, text: str) -> logging.LogRecord:
        return logging.LogRecord(__name__, level, __file__, 0, text, None, None)

    def _close_network(self) -> None:
        if self._network is not None:
            try:
                self._network.close()
            except Exception:   # noqa: BLE001
                pass
            self._network = None

    def close(self) -> None:
        self._close_network()
        if self._local is not None:
            self._local.close()
            self._local = None


class _DroppingQueueHandler(logging.handlers.QueueHandler):
    """Blockiert nie: ist die Warteschlange voll (Netzlaufwerk haengt), wird verworfen und gezaehlt."""

    def __init__(self, pipeline: "_LogPipeline", key: str) -> None:
        super().__init__(pipeline.queue)
        self._pipeline = pipeline
        self._key = key

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait((self._key, record))
        except queue.Full:
            self._pipeline.dropped += 1


class _LogPipeline:
    def __init__(self) -> None:
        self.queue: queue.Queue = queue.Queue(QUEUE_SIZE)
        self.dropped = 0
        self._reported_dropped = 0
        self._lock = threading.Lock()
        host = host_name()
        self._writers = {
            MAIN: _FailoverWriter(LOG_PATH, "labdash.log", _FORMATS[MAIN],
                                  LOCAL_MAX_BYTES, LOCAL_BACKUPS, eager=True, subdir=host),
            ACCESS: _FailoverWriter(app_dir() / "share_access.log", "share_access.log",
                                    _FORMATS[ACCESS], 200_000, 1, eager=False, subdir=host),
        }
        self._thread = threading.Thread(target=self._run, name="labcontrol-log", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self.queue.get()
            if item is None:
                return
            key, payload = item
            if key == "__dir__":
                for writer in self._writers.values():
                    writer.set_network_dir(payload)
                continue
            if self.dropped != self._reported_dropped:
                lost = self.dropped - self._reported_dropped
                self._reported_dropped = self.dropped
                self._writers[MAIN].write(_FailoverWriter._note(
                    logging.WARNING, f"{lost} Log-Meldung(en) verworfen (Warteschlange voll)"))
            self._writers[key].write(payload)

    def handler(self, key: str) -> logging.Handler:
        return _DroppingQueueHandler(self, key)

    def set_network_dir(self, directory: Path | None) -> None:
        try:
            self.queue.put_nowait(("__dir__", directory))
        except queue.Full:
            pass

    def status(self) -> dict:
        return dict(self._writers[MAIN].state)

    def stop(self, timeout: float) -> bool:
        try:
            self.queue.put(None, timeout=timeout)
        except queue.Full:
            return False
        self._thread.join(timeout)
        if self._thread.is_alive():
            return False
        for writer in self._writers.values():
            writer.close()
        return True


_pipeline: _LogPipeline | None = None


def effective_network_dir(configured: str) -> str:
    """LABCONTROL_NETWORK_DIR gewinnt gegen die Einstellung."""
    return os.environ.get(NETWORK_DIR_ENV) or (configured or "")


def setup_logging(network_dir: str = "") -> None:
    global _pipeline
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if _pipeline is None:
        _pipeline = _LogPipeline()
        root.addHandler(_pipeline.handler(MAIN))
    set_network_dir(network_dir)


def set_network_dir(configured: str) -> None:
    """Log-Ziel aendern (leer = nur lokal). Kehrt sofort zurueck; geprueft wird im Log-Thread."""
    if _pipeline is None:
        return
    directory = effective_network_dir(configured)
    _pipeline.set_network_dir(Path(directory).expanduser() if directory else None)


def access_log_handler() -> logging.Handler | None:
    """Handler fuer das Zugriffsprotokoll der Netzwerk-Freigabe (eigene Datei, gleiches Ziel)."""
    return _pipeline.handler(ACCESS) if _pipeline is not None else None


def status() -> dict:
    """{"target": "network"|"local", "path": ..., "reason": ...} des Haupt-Logs."""
    if _pipeline is None:
        return {"target": "local", "path": str(LOG_PATH), "reason": ""}
    return _pipeline.status()


def shutdown(timeout: float = STOP_TIMEOUT_S) -> bool:
    """Restliche Meldungen schreiben und den Log-Thread beenden. False, wenn das
    Netzlaufwerk haengt -- der Thread ist ein Daemon und haelt das Beenden nicht auf."""
    global _pipeline
    if _pipeline is None:
        return True
    ok = _pipeline.stop(timeout)
    if ok:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, _DroppingQueueHandler):
                root.removeHandler(handler)
        _pipeline = None
    return ok
