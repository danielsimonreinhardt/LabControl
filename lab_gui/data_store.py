"""Datenablage auf dem Netzlaufwerk (Entscheidungen PI-12 b und PI-13 a, 10.11.2026).

Alle Nutzerdaten, die LabControl im Betrieb erzeugt, sollen aufs Netzlaufwerk, damit man
sie spaeter auswerten kann:

    <Netzlaufwerk>/<rechner>/labdash.log, share_access.log   (app_logging)
    <Netzlaufwerk>/<rechner>/captures/     Oszilloskop-Erfassungen (CSV)
    <Netzlaufwerk>/<rechner>/reports/      Ablauf-Berichte (HTML; PDF per Dialog)
    <Netzlaufwerk>/<rechner>/recordings/   Verlaufs-Aufzeichnungen (CSV/MF4 per Dialog)
    <Netzlaufwerk>/testcases/, blocks/     Testablaeufe und Bausteine, gemeinsam fuer Pi und PC

Ablauf (nie blockierend): Erfassungen und Berichte entstehen zuerst im lokalen Datenordner
(wie bisher). Ein eigener Thread schiebt sie aufs Netzlaufwerk, sobald es erreichbar ist,
und loescht danach die lokale Datei. Ein haengendes Netzlaufwerk haelt so weder Oberflaeche
noch Geraete-Worker auf. Ist das Netzlaufwerk eingestellt (oder liegt der Datenordner auf
einer SD-Karte), bleibt lokal hoechstens LOCAL_LIMIT_BYTES liegen; darueber werden die
aeltesten wartenden Dateien geloescht und das steht im Log.

Speichern-Dialoge (PDF, Aufzeichnung) und die Ordner fuer Testablaeufe/Bausteine zeigen aufs
Netzlaufwerk, wenn es bei der letzten Pruefung erreichbar war, sonst lokal.
Ohne eingestelltes Netzlaufwerk aendert sich nichts gegenueber frueher.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from app_logging import host_name, mount_source, network_dir_problem
from paths import app_dir

logger = logging.getLogger(__name__)

OUTBOX_KINDS = ("captures", "reports", "recordings")   # je Rechner, werden nachgeschoben
SHARED_KINDS = ("testcases", "blocks")                  # gemeinsam, nur Ordner fuer Dialoge
LOCAL_LIMIT_BYTES = 200 * 1024 * 1024
SYNC_INTERVAL_S = 60.0
MIN_AGE_S = 30.0          # erst nachschieben, wenn die Datei fertig geschrieben (und geoeffnet) ist
STOP_TIMEOUT_S = 3.0

MovedCallback = Callable[[Path, Path], None]


def on_sd_card(path: Path) -> bool:
    """Liegt `path` (Linux) auf einer SD-Karte (mmcblk)?"""
    if sys.platform == "win32":
        return False
    try:
        mounts = Path("/proc/self/mounts").read_text(encoding="utf-8")
    except OSError:
        return False
    return mount_source(os.path.realpath(path), mounts).startswith("/dev/mmcblk")


class DataStore:
    def __init__(self, local_root: Path, host: str | None = None, interval_s: float = SYNC_INTERVAL_S,
                 min_age_s: float = MIN_AGE_S, limit_bytes: int = LOCAL_LIMIT_BYTES) -> None:
        self.local_root = Path(local_root)
        self.host = host or host_name()
        self._interval = interval_s
        self._min_age = min_age_s
        self._limit = limit_bytes
        self._base: Path | None = None
        self._reachable = False
        self._reason = ""
        self._pending = (0, 0)
        self._last_sync: float | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._callbacks: list[MovedCallback] = []
        self._limit_on_sd = on_sd_card(self.local_root)
        self._thread: threading.Thread | None = None

    # -- Konfiguration -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="labcontrol-data", daemon=True)
            self._thread.start()

    def set_network_dir(self, directory: str) -> None:
        """Netzlaufwerk aendern (leer = nur lokal). Kehrt sofort zurueck; geprueft wird im Thread."""
        with self._lock:
            self._base = Path(directory).expanduser() if directory else None
            self._reachable = False
            self._reason = ""
        self.sync_now()

    def sync_now(self) -> None:
        self._wake.set()

    def on_moved(self, callback: MovedCallback) -> None:
        """callback(lokaler_pfad, neuer_pfad) aus dem Datenablage-Thread."""
        self._callbacks.append(callback)

    # -- Ordner ------------------------------------------------------------------

    def local_dir(self, kind: str) -> Path:
        return self.local_root / kind

    def _network_dir(self, kind: str) -> Path | None:
        with self._lock:
            if self._base is None or not self._reachable:
                return None
            base = self._base
        return base / kind if kind in SHARED_KINDS else base / self.host / kind

    def target_dir(self, kind: str) -> Path:
        """Ordner fuer Dialoge: Netzlaufwerk, wenn bei der letzten Pruefung erreichbar, sonst lokal.
        Legt ihn an; scheitert das, gilt der lokale Ordner."""
        net = self._network_dir(kind)
        if net is not None:
            try:
                net.mkdir(parents=True, exist_ok=True)
                return net
            except OSError as exc:
                logger.warning("Netzlaufwerk-Ordner %s nicht nutzbar (%s), nehme lokal", net, exc)
        local = self.local_dir(kind)
        local.mkdir(parents=True, exist_ok=True)
        return local

    # -- Zustand -----------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            files, size = self._pending
            if self._base is None:
                target = "off"
            else:
                target = "network" if self._reachable else "local"
            return {"target": target, "path": str(self._base / self.host) if self._base else "",
                    "reason": self._reason, "pending_files": files, "pending_bytes": size,
                    "last_sync": self._last_sync}

    # -- Thread ------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            self._wake.wait(self._interval)
            self._wake.clear()
            if self._stop:
                return
            try:
                self.sync_once()
            except Exception:   # noqa: BLE001 -- der Thread darf nie sterben
                logger.exception("Datenablage: unerwarteter Fehler")

    def sync_once(self) -> None:
        """Eine Runde: erreichbar? dann nachschieben; danach lokale Grenze pruefen."""
        with self._lock:
            base = self._base
        if base is not None:
            problem = network_dir_problem(base)
            moved = 0
            if not problem:
                try:
                    moved = self._move_all(base)
                except OSError as exc:
                    problem = f"Schreiben fehlgeschlagen: {exc}"
            self._set_reachable(base, problem)
            if moved:
                logger.info("Datenablage: %d Datei(en) aufs Netzlaufwerk %s verschoben", moved, base / self.host)
        if base is not None or self._limit_on_sd:
            self._enforce_limit()
        self._pending = self._count_pending()
        self._last_sync = time.time()

    def _set_reachable(self, base: Path, problem: str) -> None:
        with self._lock:
            if self._base != base:      # waehrenddessen umgestellt
                return
            was = (self._reachable, self._reason)
            self._reachable = not problem
            self._reason = problem
        if was != (self._reachable, self._reason):
            if problem:
                logger.warning("Datenablage: Netzlaufwerk %s nicht nutzbar (%s) -- Dateien bleiben lokal "
                               "in %s und werden nachgeschoben", base, problem, self.local_root)
            else:
                logger.info("Datenablage: Netzlaufwerk %s erreichbar", base / self.host)

    def _local_files(self) -> list[Path]:
        files = []
        for kind in OUTBOX_KINDS:
            folder = self.local_dir(kind)
            if folder.is_dir():
                files.extend(p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".part"))
        return sorted(files, key=_mtime)

    def _move_all(self, base: Path) -> int:
        moved = 0
        now = time.time()
        for src in self._local_files():
            if self._stop:
                break
            if now - _mtime(src) < self._min_age:
                continue
            dst_dir = base / self.host / src.parent.name
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / src.name
            size = src.stat().st_size
            if dst.exists() and dst.stat().st_size == size:
                pass   # schon kopiert, nur das lokale Loeschen hatte nicht geklappt
            else:
                if dst.exists():
                    dst = _free_name(dst)
                part = dst.with_name(dst.name + ".part")
                shutil.copy2(src, part)
                if part.stat().st_size != size:
                    part.unlink(missing_ok=True)
                    raise OSError(f"{src.name}: Groesse nach dem Kopieren weicht ab")
                os.replace(part, dst)
            try:
                src.unlink()
            except OSError as exc:   # z. B. unter Windows gerade geoeffnet -- naechste Runde
                logger.info("Datenablage: %s kopiert, lokal noch nicht loeschbar (%s)", src.name, exc)
                continue
            moved += 1
            for callback in self._callbacks:
                try:
                    callback(src, dst)
                except Exception:   # noqa: BLE001
                    logger.exception("Datenablage: Rueckmeldung fuer %s fehlgeschlagen", src.name)
        return moved

    def _count_pending(self) -> tuple[int, int]:
        files = self._local_files()
        return len(files), sum(_size(p) for p in files)

    def _enforce_limit(self) -> None:
        files = self._local_files()
        total = sum(_size(p) for p in files)
        dropped = 0
        for path in files:              # aelteste zuerst
            if total <= self._limit:
                break
            size = _size(path)
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            dropped += 1
        if dropped:
            logger.warning("Datenablage: lokal mehr als %d MB -- %d aelteste noch nicht verschobene Datei(en) "
                           "geloescht", self._limit // (1024 * 1024), dropped)

    def stop(self, timeout: float = STOP_TIMEOUT_S) -> None:
        self._stop = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _free_name(path: Path) -> Path:
    for i in range(1, 1000):
        candidate = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}-{int(time.time())}{path.suffix}")


# -- Modulweite Instanz (wie app_logging) ------------------------------------------

_store = DataStore(app_dir())


def store() -> DataStore:
    return _store


def setup(network_dir: str) -> DataStore:
    _store.set_network_dir(network_dir)
    _store.start()
    return _store


def target_dir(kind: str) -> Path:
    return _store.target_dir(kind)
