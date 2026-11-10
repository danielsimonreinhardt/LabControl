"""Prueft die Datenablage auf dem Netzlaufwerk (lab_gui/data_store.py, 0.31.0, PI-12 b / PI-13 a):

- ohne Netzlaufwerk: alles lokal wie bisher, nichts wird verschoben oder geloescht
- erreichbar: fertige Dateien (aelter als MIN_AGE) wandern nach <netz>/<rechner>/<art>, lokal geloescht,
  Rueckmeldung mit neuem Pfad; frische Dateien bleiben bis zur naechsten Runde
- Namensgleichheit: gleiche Groesse = schon kopiert (nur lokal loeschen), sonst neuer Name "-1"
- nicht erreichbar: Dateien bleiben lokal, Grund im Status; ueber der Grenze werden die aeltesten
  geloescht und das steht im Log; danach wieder erreichbar -> nachgeschoben
- Schreibfehler beim Kopieren: lokal bleibt, Status "local" mit Grund
- Ordner fuer Dialoge: Netzlaufwerk (Rechner-Unterordner bzw. gemeinsam), sonst lokal
- Thread: Umstellen des Netzlaufwerks loest sofort eine Runde aus
- Erfassungsspeicher zieht den CSV-Pfad nach; Einstellungen-Reiter zeigt den Zustand

Unter Linux dient /dev/shm als "Netzlaufwerk". Aufruf:  python tools/check_data_store.py
"""
from __future__ import annotations

import logging
import os
import pathlib
import shutil
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LABCONTROL_DATA_DIR"] = tempfile.mkdtemp(prefix="lc-data-")
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

import data_store as ds  # noqa: E402

FAILURES: list[str] = []
LOG: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


class Grab(logging.Handler):
    def emit(self, record):
        LOG.append(record.getMessage())


def network_base() -> pathlib.Path:
    if sys.platform == "win32":
        return pathlib.Path(tempfile.mkdtemp(prefix="lc-net-"))
    return pathlib.Path(tempfile.mkdtemp(prefix="lc-net-", dir="/dev/shm"))


def make(store: ds.DataStore, kind: str, name: str, size: int = 1000, age_s: float = 120.0) -> pathlib.Path:
    folder = store.local_dir(kind)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))
    return path


def new_store(**kw) -> ds.DataStore:
    return ds.DataStore(pathlib.Path(tempfile.mkdtemp(prefix="lc-local-")), host="pi-test", **kw)


def check_without_network() -> None:
    print("Ohne Netzlaufwerk")
    s = new_store(limit_bytes=1500)
    s._limit_on_sd = False
    a = make(s, "captures", "c1.csv")
    b = make(s, "captures", "c2.csv")
    s.sync_once()
    check("nichts verschoben oder geloescht (auch ueber der Grenze)", a.exists() and b.exists())
    check("Status: aus", s.status()["target"] == "off")
    check("Dialog-Ordner lokal", s.target_dir("testcases") == s.local_dir("testcases") and s.local_dir("testcases").is_dir())


def check_move() -> None:
    print("Netzlaufwerk erreichbar")
    s = new_store()
    net = network_base()
    moved: list[tuple] = []
    s.on_moved(lambda old, new: moved.append((old, new)))
    old = make(s, "captures", "c-1.csv", 2000)
    rep = make(s, "reports", "report_a.html", 500)
    young = make(s, "captures", "c-2.csv", age_s=1)
    part = make(s, "recordings", "halb.csv.part")
    s.set_network_dir(str(net))
    s.sync_once()
    target = net / "pi-test" / "captures" / "c-1.csv"
    check("Erfassung nach <netz>/<rechner>/captures", target.is_file() and target.stat().st_size == 2000)
    check("Bericht nach <netz>/<rechner>/reports", (net / "pi-test" / "reports" / "report_a.html").is_file())
    check("lokal geloescht", not old.exists() and not rep.exists())
    check("frische Datei bleibt bis zur naechsten Runde", young.exists())
    check(".part-Dateien werden nicht angefasst", part.exists())
    check("Rueckmeldung mit altem und neuem Pfad", (old, target) in moved, str(moved))
    st = s.status()
    check("Status: Netzlaufwerk, nur die frische Datei wartet (.part zaehlt nicht)",
          st["target"] == "network" and st["pending_files"] == 1 and st["pending_bytes"] == 1000
          and st["path"] == str(net / "pi-test"), str(st))
    check("keine .part-Reste im Netzlaufwerk", not list((net / "pi-test").rglob("*.part")))

    print("Namensgleichheit")
    same = make(s, "captures", "c-1.csv", 2000)          # schon kopiert, Loeschen hatte nicht geklappt
    other = make(s, "reports", "report_a.html", 800)    # anderer Inhalt
    s.sync_once()
    check("gleiche Groesse: nur lokal geloescht, keine Kopie", not same.exists()
          and not (net / "pi-test" / "captures" / "c-1-1.csv").exists())
    check("andere Groesse: neuer Name -1", not other.exists()
          and (net / "pi-test" / "reports" / "report_a-1.html").stat().st_size == 800)

    print("Ordner fuer Dialoge")
    check("Testablaeufe gemeinsam: <netz>/testcases", s.target_dir("testcases") == net / "testcases")
    check("Bausteine gemeinsam: <netz>/blocks", s.target_dir("blocks") == net / "blocks")
    check("Aufzeichnungen je Rechner", s.target_dir("recordings") == net / "pi-test" / "recordings")


def check_unreachable_and_limit() -> None:
    print("Netzlaufwerk nicht erreichbar, lokale Grenze")
    s = new_store(limit_bytes=2500)
    files = [make(s, "captures", f"c{i}.csv", 1000, age_s=300 - i) for i in range(4)]   # c0 aeltester
    missing = pathlib.Path(tempfile.mkdtemp(prefix="lc-weg-"))
    missing.rmdir()   # eindeutiger Name, der (noch) nicht existiert
    s.set_network_dir(str(missing))
    LOG.clear()
    s.sync_once()
    st = s.status()
    check("Status: lokal mit Grund", st["target"] == "local" and st["reason"] == "Ordner nicht erreichbar", str(st))
    check("Grenze: die beiden aeltesten geloescht", not files[0].exists() and not files[1].exists()
          and files[2].exists() and files[3].exists())
    check("Loeschen steht im Log", any("aelteste" in m and "2" in m for m in LOG), str(LOG))
    check("Wechsel steht im Log", any("nicht nutzbar" in m for m in LOG), str(LOG))
    check("Dialog-Ordner lokal", s.target_dir("recordings") == s.local_dir("recordings"))
    check("wartende Dateien gezaehlt", st["pending_files"] == 2 and st["pending_bytes"] == 2000, str(st))

    missing.mkdir()
    if sys.platform != "win32":
        shutil.rmtree(missing)
        missing = network_base()
        s.set_network_dir(str(missing))
    s.sync_once()
    check("wieder erreichbar: nachgeschoben", not files[2].exists() and (missing / "pi-test" / "captures" / "c3.csv").is_file())
    check("Wechsel zurueck im Log", any(m.startswith("Datenablage: Netzlaufwerk") and m.endswith(" erreichbar")
                                        for m in LOG), str(LOG))


def check_copy_error() -> None:
    print("Schreibfehler beim Kopieren")
    s = new_store()
    net = network_base()
    f = make(s, "captures", "c.csv")
    s.set_network_dir(str(net))
    real = shutil.copy2

    def failing(*_a, **_k):
        raise OSError("Netzwerkname nicht mehr verfuegbar")

    ds.shutil.copy2 = failing
    try:
        s.sync_once()
    finally:
        ds.shutil.copy2 = real
    st = s.status()
    check("lokal bleibt, Status lokal mit Grund", f.exists() and st["target"] == "local"
          and "Netzwerkname" in st["reason"], str(st))
    s.sync_once()
    check("naechste Runde: verschoben", not f.exists() and (net / "pi-test" / "captures" / "c.csv").is_file())


def check_thread() -> None:
    print("Thread")
    s = new_store(interval_s=3600)
    net = network_base()
    f = make(s, "reports", "r.html")
    s.start()
    s.set_network_dir(str(net))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and f.exists():
        time.sleep(0.05)
    check("Umstellen loest sofort eine Runde aus", not f.exists())
    started = time.monotonic()
    s.stop()
    check("stop kehrt schnell zurueck", time.monotonic() - started < 2)


def check_capture_store_and_tab() -> None:
    print("Erfassungsspeicher und Einstellungen-Reiter")
    from scope_api.store import CaptureStore

    class C:   # nur das, was relocate braucht
        csv_path = "/lokal/captures/c-1.csv"

    store = CaptureStore(None)
    c = C()
    store._captures["c-1"] = c
    check("CSV-Pfad zieht nach", store.relocate("/lokal/captures/c-1.csv", "/netz/pi/captures/c-1.csv") == 1
          and c.csv_path == "/netz/pi/captures/c-1.csv")

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from settings_tab import SettingsTab
    tab = SettingsTab()
    tab.set_data_status({"target": "network", "path": "/mnt/labor/pi", "reason": "", "pending_files": 0,
                         "pending_bytes": 0, "last_sync": 1.0})
    text = tab._data_status_label.text()
    check("Netzlaufwerk: Pfad angezeigt", "/mnt/labor/pi" in text and "warten" not in text, text)
    tab.set_data_status({"target": "local", "path": "/mnt/labor/pi", "reason": "Ordner nicht erreichbar",
                         "pending_files": 3, "pending_bytes": 3 * 1_048_576, "last_sync": 2.0})
    text = tab._data_status_label.text()
    check("lokal: Grund und wartende Dateien", "Ordner nicht erreichbar" in text and "3 Datei(en), 3.0 MB" in text, text)
    tab.set_data_status({"target": "off", "path": "", "reason": "", "pending_files": 0, "pending_bytes": 0})
    check("ohne Netzlaufwerk: keine Zeile", tab._data_status_label.text() == "")
    app.processEvents()


def main() -> int:
    ds.logger.addHandler(Grab())
    ds.logger.setLevel(logging.INFO)
    check_without_network()
    check_move()
    check_unreachable_and_limit()
    check_copy_error()
    check_thread()
    check_capture_store_and_tab()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
