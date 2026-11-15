"""Prueft PI-54 (0.65.0) Flashen ueber CAN: canflash_service (Befehl, Ablauf mit Kanal-Freigabe, Fortschritt,
Fehler, Abbruch, Zeitueberschreitung), DeviceWorker.hold_can, Schritt "Firmware flashen (CAN)" (uds_steps,
Dialog, Kachel-Knopf) und `labcontrol flash` in der Simulation.

canflash selbst wird durch ein kleines Ersatzskript nachgebildet (gleiche Ausgabe wie --progress steps).
Offscreen, keine Hardware.  Aufruf:  python tools/check_canflash.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-canflash-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []

FAKE_CANFLASH = '''import os, sys, time
args = sys.argv[1:]
print("canflash fake " + " ".join(args), flush=True)
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "hang":
    time.sleep(30)
for p in range(10, 101, 10):
    print(f"  {p * 100:7d}/10000 bytes ({p:3d} %)", flush=True)
    time.sleep(0.01)
    if mode == "fail" and p == 50:
        print("canflash: no response in time (TransferData)", file=sys.stderr, flush=True)
        sys.exit(1)
print("  12.0 KB/s")
print("Done, ECU reset.")
'''


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def spin(app, seconds: float, until=None) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.005)


def section_unit() -> None:
    print("unit -- Befehl, Schritt-Parameter")
    import canflash_service as cs
    import uds_steps as us

    index = lambda serial, ch: {(59177, 0): 0, (59177, 1): 1, (75816, 0): 2}.get((serial, ch), ch)
    check("Vector: Seriennummer:Kanal -> globaler Index",
          cs.bus_arguments("can:vector:75816:0", index) == ["--interface", "vector", "--channel", "2"])
    check("Vector: alte Zahl bleibt", cs.bus_arguments("can:vector:1", index)[-1] == "1")
    check("PCAN mit Zahl", cs.bus_arguments("can:pcan:0", index) == ["--interface", "pcan", "--channel", "0"])
    for bad in ("can:socketcan:can0", "", "psu:1"):
        try:
            cs.bus_arguments(bad, index)
            check(f"abgelehnt: {bad!r}", False)
        except ValueError:
            check(f"abgelehnt: {bad!r}", True)
    program, args = cs.build_command("/x/nx", "can:vector:59177:1", "/fw/app.hex", 30, "drive_unit", index)
    check("Programm = Python des neXtBSW-venv", ".venv" in program.replace("\\", "/"))
    check("Argumente", args[1:] == ["flash", "/fw/app.hex", "--interface", "vector", "--channel", "1",
                                    "--progress", "steps", "--wait", "30", "--ecu", "drive_unit"], str(args))
    check("Skript", args[0].replace("\\", "/").endswith("tools/canflash/canflash.py"))
    check("ohne Warten/ECU keine Optionen", "--wait" not in cs.build_command("/x", "can:pcan:0", "a.hex", 0, "",
                                                                                index)[1])
    check("Schritt-Parameter", us.flash_args({"file": "a.hex", "wait": "2,5"}) == {"file": "a.hex", "wait": 2.5,
                                                                                    "ecu": ""})
    check("Befehl", us.command_for("UDS_FLASH", {"file": "a.hex"})[0] == "flash")
    for bad in ({}, {"file": "a.hex", "wait": 400}):
        try:
            us.flash_args(bad)
            check(f"Parameter abgelehnt {bad}", False)
        except ValueError:
            check(f"Parameter abgelehnt {bad}", True)
    check("Kurztext", us.summary("UDS_FLASH", {"file": "C:/fw/app.hex", "wait": 10}) == "app.hex, Bootloader ≤ 10 s")


def _fake_nextbsw() -> Path:
    base = DATA / "nextbsw"
    (base / "tools" / "canflash").mkdir(parents=True, exist_ok=True)
    (base / "tools" / "canflash" / "canflash.py").write_text(FAKE_CANFLASH, encoding="utf-8")
    return base


def section_service(app) -> None:
    print("service -- Ablauf mit Kanal-Freigabe (Ersatz-canflash)")
    from PySide6.QtCore import QObject, QTimer, Signal

    import canflash_service as cs

    cs.nextbsw_python = lambda _d: Path(sys.executable)      # Ersatzskript mit diesem Python
    nextbsw = _fake_nextbsw()
    firmware = DATA / "app.hex"
    firmware.write_text(":00000001FF\n", encoding="ascii")

    class FakeWorker(QObject):
        can_hold_changed = Signal(str, bool)

        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[str, bool]] = []
            self.answer = True

        def hold_can(self, device_id: str, on: bool) -> None:
            self.calls.append((device_id, on))
            if self.answer:
                QTimer.singleShot(20, lambda: self.can_hold_changed.emit(device_id, on))

    worker = FakeWorker()
    service = cs.CanFlashService()
    service.hold_requested.connect(worker.hold_can)
    worker.can_hold_changed.connect(service.on_can_hold_changed)
    service.configure({"can_device": "can:pcan:0", "nextbsw_dir": str(nextbsw)}, simulation=False)
    check("eingerichtet", service.problem() == "", service.problem())
    results: list[tuple[int, bool, str]] = []
    progress: list[int] = []
    service.finished.connect(lambda r, ok, m: results.append((r, ok, m)))
    service.progress.connect(lambda _r, p, _line: progress.append(p))

    os.environ["FAKE_MODE"] = "ok"
    request = service.start(str(firmware), 0, "", source="check")
    check("busy", service.busy())
    spin(app, 20, lambda: results)
    check("fertig, bestanden", results and results[0][:2] == (request, True), str(results))
    check("Meldung nennt die Datei", results and "app.hex" in results[0][2], str(results))
    check("Kanal frei -> canflash -> Kanal zurueck", worker.calls == [("can:pcan:0", True), ("can:pcan:0", False)],
          str(worker.calls))
    check("Fortschritt bis 100 %", progress and progress[-1] == 100 and 50 in progress, str(progress))
    check("nicht mehr busy", not service.busy())

    lines: list[str] = []
    service.progress.connect(lambda _r, _p, line: lines.append(line))
    results.clear()
    service.configure({"can_device": "can:pcan:0", "nextbsw_dir": str(nextbsw), "flash_ecu": "evalboard_demo"},
                      simulation=False)
    service.start(str(firmware))
    spin(app, 20, lambda: results)
    check("Projekt-Vorgabe aus den Einstellungen -> --ecu", any("--ecu evalboard_demo" in l for l in lines),
          str(lines[:1]))
    results.clear()
    lines.clear()
    service.start(str(firmware), ecu="andere_ecu")
    spin(app, 20, lambda: results)
    check("Schritt-Angabe geht vor", any("--ecu andere_ecu" in l for l in lines), str(lines[:1]))

    results.clear()
    worker.calls.clear()
    os.environ["FAKE_MODE"] = "fail"
    service.start(str(firmware))
    spin(app, 20, lambda: results)
    check("Exit 1 -> nicht bestanden", results and results[0][1] is False, str(results))
    check("Meldung mit canflash-Grund", results and "no response in time" in results[0][2], str(results))
    check("Kanal auch nach Fehler zurueck", worker.calls[-1] == ("can:pcan:0", False), str(worker.calls))

    results.clear()
    worker.calls.clear()
    os.environ["FAKE_MODE"] = "hang"
    service.start(str(firmware))
    spin(app, 3, lambda: service._process is not None)
    spin(app, 0.3)
    service.cancel()
    spin(app, 10, lambda: results)
    check("Abbruch -> nicht bestanden", results and results[0][1] is False and "abgebrochen" in results[0][2],
          str(results))
    check("Kanal nach Abbruch zurueck", worker.calls and worker.calls[-1] == ("can:pcan:0", False), str(worker.calls))

    results.clear()
    worker.calls.clear()
    os.environ["FAKE_MODE"] = "ok"
    worker.answer = False
    cs_hold = cs.HOLD_CONFIRM_S
    service._hold_timer.setInterval(200)
    cs.HOLD_CONFIRM_S = 0.2
    service.start(str(firmware))
    spin(app, 5, lambda: results)
    cs.HOLD_CONFIRM_S = cs_hold
    worker.answer = True
    check("Kanal nicht freigegeben -> Fehler ohne canflash", results and results[0][1] is False
          and "nicht freigegeben" in results[0][2], str(results))
    check("Kanal danach wieder angefordert", worker.calls[-1] == ("can:pcan:0", False), str(worker.calls))

    results.clear()
    service.start(str(DATA / "fehlt.hex"))
    spin(app, 1, lambda: results)
    check("fehlende Datei -> sofort Fehler", results and results[0][1] is False and "nicht gefunden" in results[0][2])
    results.clear()
    request = service.start(str(firmware))
    second = service.start(str(firmware))
    spin(app, 20, lambda: len(results) >= 2)
    check("zweites Flashen gleichzeitig abgelehnt", any(r == second and not ok for r, ok, _m in results), str(results))
    check("erstes Flashen trotzdem bestanden", any(r == request and ok for r, ok, _m in results), str(results))

    service.configure({"can_device": "can:pcan:0", "nextbsw_dir": str(DATA / "nix")}, simulation=False)
    check("fehlendes canflash gemeldet", "canflash" in service.problem(), service.problem())
    service.configure({}, simulation=True)
    check("Simulation ohne neXtBSW: eingerichtet", service.problem() == "")
    results.clear()
    worker.calls.clear()
    service.start(str(firmware))
    spin(app, 5, lambda: results)
    check("Simulation: bestanden ohne Kanal-Freigabe", results and results[0][1] and not worker.calls, str(results))


def section_worker(app) -> None:
    print("worker -- hold_can")
    from device_worker import DeviceWorker

    worker = DeviceWorker(simulation_mode=False, can_configs=[])
    errors: list[str] = []
    holds: list[tuple[str, bool]] = []
    worker.can_connect_error.connect(lambda device_id, _m: errors.append(device_id))
    worker.can_hold_changed.connect(lambda device_id, on: holds.append((device_id, on)))
    cfg = {"interface": "vector", "channel": "99999:7", "bitrate": 500000}
    worker._can_configs = [cfg]
    device_id = "can:vector:99999:7"
    worker.hold_can(device_id, True)
    worker._reconnect_can()
    check("freigegebener Kanal wird nicht wieder geoeffnet", device_id not in errors, str(errors))
    worker.hold_can(device_id, False)
    check("nach der Rueckgabe wird es wieder versucht", device_id in errors, str(errors))
    check("Bestaetigungen", holds == [(device_id, True), (device_id, False)], str(holds))


def section_ui(app) -> None:
    print("ui -- Dialog und Kachel")
    from uds_step_dialog import UdsStepDialog
    from uds_tile import UdsControlGroup

    dialog = UdsStepDialog("UDS_FLASH", {"file": "C:/fw/app.hex", "wait": 5, "ecu": "evalboard"})
    check("Erwartungszeile ausgeblendet", not dialog._form.isRowVisible(dialog._expect))
    dialog._accept()
    params = dialog.params()
    check("Parameter zurueck", params.get("file") == "C:/fw/app.hex" and float(params.get("wait")) == 5.0
          and params.get("ecu") == "evalboard", str(params))
    tile = UdsControlGroup("uds:dut", "Prüfling")
    check("Kachel hat Flash-Knopf", tile._flash_button.toolTip().startswith("Firmware"))
    tile.set_state({"busy": True})
    check("Flash-Knopf waehrend busy gesperrt", not tile._flash_button.isEnabled())
    tile.set_state({"busy": False})
    check("Flash-Knopf auch ohne ODX bedienbar", tile._flash_button.isEnabled())


def section_cli() -> None:
    print("cli -- labcontrol flash (Simulation)")
    firmware = DATA / "cli.hex"
    firmware.write_text(":00000001FF\n", encoding="ascii")
    data = DATA / "cli"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")
    env = dict(os.environ, LABCONTROL_DATA_DIR=str(data), PYTHONIOENCODING="utf-8")
    out = data / "r.json"
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "check_headless_run.py"), "--child", "flash",
                           str(firmware), "--simulation", "--json", str(out)],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    result = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    check("Exit 0", proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:])
    check("Fortschritt ausgegeben", "100 %" in proc.stdout, proc.stdout[-400:])
    check("run-result bestanden", result.get("outcome") == "passed", str(result.get("outcome")))
    leftovers = list(Path(tempfile.gettempdir()).glob("labcontrol_flash_*.json"))
    check("temporaerer Ablauf geloescht", not any(p.stat().st_mtime > time.time() - 60 for p in leftovers))
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "check_headless_run.py"), "--child", "flash",
                           str(DATA / "fehlt.hex"), "--simulation"],
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    check("fehlende Datei -> Exit 2", proc.returncode == 2, f"{proc.returncode} {proc.stdout[-300:]}")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    section_unit()
    section_service(app)
    section_worker(app)
    section_ui(app)
    section_cli()
    print(f"\n{'ALLES OK' if not FAILURES else f'{len(FAILURES)} FEHLER: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
