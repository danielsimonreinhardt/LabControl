"""Prueft das Logging mit Netzlaufwerk (app_logging, 10.11.2026):

- ohne Log-Ordner: lokal; mit erreichbarem Ordner: dorthin (je Rechner eine Datei), lokal entsteht nichts
- Ordner nicht erreichbar / auf der Systemplatte (Linux: nicht eingehaengter Mountpoint): lokal + Grund
- Schreibfehler unterwegs: Wechsel auf lokal mit Hinweis, nach der Wartezeit zurueck mit Hinweis
- haengendes Netzlaufwerk: Aufrufer blockiert nicht, volle Warteschlange verwirft und meldet das,
  shutdown() kehrt nach der Frist zurueck
- Zugriffsprotokoll der Freigabe: eigene Datei, ohne Zugriffe keine Datei
- LABCONTROL_NETWORK_DIR gewinnt; Einstellungen-Reiter zeigt Ziel und meldet Aenderungen

Unter Linux dient /dev/shm (tmpfs, eigenes Dateisystem) als "Netzlaufwerk". Aufruf:  python tools/check_app_logging.py
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = pathlib.Path(tempfile.mkdtemp(prefix="lc-data-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)   # vor dem Import von paths/app_logging
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

import app_logging as al  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def network_base() -> pathlib.Path:
    if sys.platform == "win32":
        return pathlib.Path(tempfile.mkdtemp(prefix="lc-net-"))
    return pathlib.Path(tempfile.mkdtemp(prefix="lc-net-", dir="/dev/shm"))


def rec(text: str, name: str = "test") -> logging.LogRecord:
    return logging.LogRecord(name, logging.INFO, __file__, 0, text, None, None)


def writer(local: pathlib.Path) -> al._FailoverWriter:
    return al._FailoverWriter(local, "labdash-test.log", al._FORMATS[al.MAIN], 100_000, 1, eager=True)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_writer() -> None:
    print("Ziel und Ausweichen")
    local = DATA / "w1.log"
    w = writer(local)
    w.write(rec("ohne Ordner"))
    check("ohne Log-Ordner: lokal", "ohne Ordner" in read(local) and w.state["target"] == "local")

    net = network_base()
    local2 = DATA / "w2.log"
    w = writer(local2)
    w.set_network_dir(net)
    w.write(rec("ins Netz"))
    check("erreichbarer Ordner: Netzlaufwerk", "ins Netz" in read(net / "labdash-test.log")
          and w.state == {"target": "network", "path": str(net / "labdash-test.log"), "reason": ""}, str(w.state))
    check("lokal entsteht keine Datei", not local2.exists())

    print("Schreibfehler unterwegs")
    real_emit = w._network.emit

    def failing(_record):
        raise OSError("Netzwerkpfad nicht gefunden")

    w._network.emit = failing
    w.write(rec("waehrend Ausfall"))
    text = read(local2)
    check("Wechsel auf lokal mit Hinweis", "fehlgeschlagen (Netzwerkpfad nicht gefunden)" in text
          and "waehrend Ausfall" in text and w.state["target"] == "local", text[-200:])
    w.write(rec("noch im Ausfall"))
    check("vor Ablauf der Wartezeit kein neuer Versuch", w.state["target"] == "local" and "noch im Ausfall" in read(local2))
    del real_emit
    w._next_try = 0.0   # Wartezeit abgelaufen
    w.write(rec("wieder da"))
    net_text = read(net / "labdash-test.log")
    check("zurueck aufs Netzlaufwerk mit Hinweis auf lokale Meldungen",
          "wieder da" in net_text and "wieder erreichbar" in net_text and str(local2) in net_text, net_text[-300:])

    print("Ordner nicht nutzbar")
    local3 = DATA / "w3.log"
    w = writer(local3)
    w.set_network_dir(DATA / "gibt-es-nicht")
    w.write(rec("x"))
    check("fehlender Ordner: lokal mit Grund", w.state["target"] == "local" and w.state["reason"] == "Ordner nicht erreichbar"
          and "nicht nutzbar (Ordner nicht erreichbar)" in read(local3), str(w.state))
    saved = sys.platform
    try:
        sys.platform = "linux"
        # /tmp ist auf neueren Systemen tmpfs -- ein Ordner im Home liegt sicher auf "/"
        problem = al.network_dir_problem(pathlib.Path(tempfile.gettempdir()) if saved == "win32"
                                         else pathlib.Path(tempfile.mkdtemp(dir=pathlib.Path.home())))
    finally:
        sys.platform = saved
    check("Linux: Ordner auf der Systemplatte zaehlt nicht als Netzlaufwerk", "Systemplatte" in problem, problem)
    mounts = ("/dev/mmcblk0p2 / ext4 rw,noatime 0 0\n"
              "/dev/mmcblk0p1 /boot/firmware vfat rw 0 0\n"
              "//nas/labor /mnt/labor cifs rw 0 0\n"
              "/dev/sda1 /mnt/usb\\040ssd ext4 rw 0 0\n")
    check("Mount-Quelle: /boot/firmware ist die SD-Karte",
          al.mount_source("/boot/firmware/logs", mounts) == "/dev/mmcblk0p1")
    check("Mount-Quelle: /mnt/labor ist die Freigabe", al.mount_source("/mnt/labor/logs", mounts) == "//nas/labor")
    check("Mount-Quelle: Leerzeichen im Mountpoint", al.mount_source("/mnt/usb ssd/x", mounts) == "/dev/sda1")
    check("Mount-Quelle: /mnt/laborx gehoert nicht zu /mnt/labor",
          al.mount_source("/mnt/laborx", mounts) == "/dev/mmcblk0p2")


def check_pipeline() -> None:
    print("Log-Thread, Zugriffsprotokoll, Haenger")
    net = network_base()
    al.setup_logging(str(net))
    host = al.host_name()
    log = logging.getLogger("check")
    log.info("ueber die Warteschlange")
    deadline = time.monotonic() + 5
    main_file = net / host / "labdash.log"
    while time.monotonic() < deadline and "ueber die Warteschlange" not in read(main_file):
        time.sleep(0.05)
    check("Haupt-Log im Rechner-Unterordner des Netzlaufwerks", "ueber die Warteschlange" in read(main_file))
    check("Status: Netzlaufwerk", al.status()["target"] == "network", str(al.status()))
    check("ohne Zugriffe keine Zugriffsprotokoll-Datei",
          not (net / host / "share_access.log").exists() and not (DATA / "share_access.log").exists())
    access = logging.getLogger("check.access")
    access.propagate = False
    access.addHandler(al.access_log_handler())
    access.info("127.0.0.1 GET /api/v1/status")
    time.sleep(0.5)
    check("Zugriffsprotokoll eigene Datei im Netzlaufwerk",
          "GET /api/v1/status" in read(net / host / "share_access.log")
          and "GET /api/v1/status" not in read(main_file))

    # Haenger: jeder Schreibvorgang dauert 0,5 s
    pipeline = al._pipeline
    w = pipeline._writers[al.MAIN]
    real_write = w.write

    def slow(record):
        time.sleep(0.5)
        real_write(record)

    w.write = slow
    started = time.monotonic()
    for i in range(al.QUEUE_SIZE + 500):
        log.info("Flut %d", i)
    elapsed = time.monotonic() - started
    check("Aufrufer blockiert nicht (Netzlaufwerk haengt)", elapsed < 3.0, f"{elapsed:.2f} s")
    # 500 ueber der Kapazitaet; der Log-Thread hat inzwischen einige wenige entnommen
    check("volle Warteschlange verwirft und zaehlt", pipeline.dropped >= 450, str(pipeline.dropped))
    started = time.monotonic()
    ok = al.shutdown(timeout=1.0)
    check("shutdown kehrt nach der Frist zurueck", not ok and time.monotonic() - started < 3.0)
    w.write = real_write
    # Rest abarbeiten lassen: ohne Bremse ist die Warteschlange schnell leer
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and "verworfen" not in read(main_file):
        time.sleep(0.2)
    check("verworfene Meldungen stehen im Log", "Log-Meldung(en) verworfen" in read(main_file))
    check("shutdown danach erfolgreich", al.shutdown(timeout=10.0))


def check_env_and_tab() -> None:
    print("Umgebungsvariable und Einstellungen-Reiter")
    os.environ[al.NETWORK_DIR_ENV] = "/mnt/labor/logs"
    check("LABCONTROL_NETWORK_DIR gewinnt gegen die Einstellung", al.effective_network_dir("//nas/x") == "/mnt/labor/logs")
    del os.environ[al.NETWORK_DIR_ENV]
    check("ohne Variable gilt die Einstellung", al.effective_network_dir("//nas/x") == "//nas/x")

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from settings_tab import SettingsTab
    tab = SettingsTab()
    seen = []
    tab.network_dir_changed.connect(seen.append)
    tab.set_network_dir("//nas/labor/logs")
    tab._network_dir_edit.setText("  /mnt/labor/logs ")
    tab._network_dir_edit.editingFinished.emit()
    check("Feld meldet den Ordner (ohne Leerzeichen)", seen == ["/mnt/labor/logs"], str(seen))
    tab.set_log_status({"target": "local", "path": "/home/pi/.local/share/labcontrol/labdash.log",
                        "reason": "Ordner nicht erreichbar"})
    text = tab._log_status_label.text()
    check("Status zeigt Ausweichen mit Grund", "nicht nutzbar (Ordner nicht erreichbar)" in text and "labdash.log" in text, text)
    tab.set_network_dir("", env_override="/mnt/x")
    check("Variable gesetzt: Feld gesperrt", not tab._network_dir_edit.isEnabled() and tab._network_dir_edit.text() == "/mnt/x")
    app.processEvents()


def check_repeating() -> None:
    print("Wiederholte Meldungen (fremder CH340-Port, belegter Port)")
    import device_worker as dw
    seen: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    grab = Grab()
    dw.logger.addHandler(grab)
    try:
        for _ in range(5):
            dw._log_repeating("fg_rejected/COM18", logging.INFO, "CH340-Port %s ist kein Funktionsgenerator", "COM18")
        dw._log_repeating("fg_rejected/COM19", logging.INFO, "CH340-Port %s ist kein Funktionsgenerator", "COM19")
        check("je Port nur einmal im Intervall", seen == ["CH340-Port COM18 ist kein Funktionsgenerator",
                                                          "CH340-Port COM19 ist kein Funktionsgenerator"], str(seen))
        dw._last_logged["fg_rejected/COM18"] -= dw.LOG_REPEAT_INTERVAL_S
        dw._log_repeating("fg_rejected/COM18", logging.INFO, "CH340-Port %s ist kein Funktionsgenerator", "COM18")
        check("nach 10 min wieder", len(seen) == 3)
    finally:
        dw.logger.removeHandler(grab)


def main() -> int:
    check_writer()
    check_pipeline()
    check_repeating()
    check_env_and_tab()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
