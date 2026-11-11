"""Prueft den J-Link (Verbund V3, PI-31 a; 0.48.0) ohne echten J-Link:

parse    -- Auswertung aufgezeichneter Commander-Ausgaben (Sonden, VTref, Kern, Fehler)
driver   -- driver.JLink gegen einen nachgebauten Commander (Skript statt JLink.exe): Aufrufargumente,
            Skript, Erfolg, Fehler, Zeitlimit mit EDU-Hinweis, Dateipruefung
service  -- JLinkService mit MockJLink: Ablauf, "beschaeftigt", Log mit SHA-256
window   -- MainWindow in Simulation: J-Link-Kachel und Dashboard, Bedienen ueber die Kachel,
            Testablauf mit Flashen/Reset/Netzteil, fehlschlagendes Flashen bricht ab, Editor mit
            Dateiauswahl, Speichern/Laden behaelt die Datei

Offscreen, keine Hardware (auch kein echter J-Link -- der EDU-Lizenzdialog wird nie ausgeloest).
Aufruf:  python tools/check_jlink.py
"""
from __future__ import annotations

import json
import logging
import os
import stat
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-jlink-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

from PySide6.QtWidgets import QApplication  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []

SAMPLE_EMU = """SEGGER J-Link Commander V7.94j (Compiled Feb 14 2024 15:37:25)
DLL version V7.94j, compiled Feb 14 2024 15:36:06

J-Link Command File read successfully.
Processing script file...
J-Link>ShowEmuList
J-Link[0]: Connection: USB, Serial number: 801012345, ProductName: J-Link EDU Mini
J-Link[1]: Connection: USB, Serial number: 260100042, ProductName: J-Link PLUS
J-Link>exit
"""
SAMPLE_CONNECT = """Connecting to J-Link via USB...O.K.
Firmware: J-Link EDU Mini V1 compiled Nov 30 2023 11:45:58
Hardware version: V1.00
S/N: 801012345
License(s): FlashBP, GDB
VTref=3.297V
J-Link>connect
Device "S32K144" selected.
Connecting to target via SWD
Found SW-DP with ID 0x2BA01477
Cortex-M4 identified.
J-Link>exit
"""
SAMPLE_FAIL = """Connecting to J-Link via USB...O.K.
VTref=0.000V
J-Link>connect
Connecting to target via SWD
Cannot connect to target.
"""


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def spin(app, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return bool(until()) if until is not None else False


def section_parse() -> None:
    print("parse -- Commander-Ausgaben")
    from jlink.driver import first_error, parse_emulators, parse_info
    check("zwei Sonden mit Seriennummer und Produkt", parse_emulators(SAMPLE_EMU)
          == [("801012345", "J-Link EDU Mini"), ("260100042", "J-Link PLUS")], str(parse_emulators(SAMPLE_EMU)))
    info = parse_info(SAMPLE_CONNECT)
    check("VTref, S/N, Firmware, Kern", info.vtref == 3.297 and info.serial == "801012345"
          and info.firmware.startswith("J-Link EDU Mini") and info.core == "Cortex-M4" and info.connected, str(info))
    check("ohne Zielverbindung kein Kern", not parse_info(SAMPLE_EMU).connected)
    check("Fehlerzeile erkannt", first_error(SAMPLE_FAIL) == "Cannot connect to target.", first_error(SAMPLE_FAIL))
    check("Erfolg ohne Fehlerzeile", first_error(SAMPLE_CONNECT) == "")


FAKE = '''
import sys, time
args = sys.argv[1:]
open(sys.argv[0] + ".args", "w").write("\\n".join(args))
script = open(args[args.index("-CommanderScript") + 1]).read()
open(sys.argv[0] + ".script", "w").write(script)
if "SLEEP" in open(sys.argv[0] + ".mode").read():
    time.sleep(10)
if "loadfile" in script and "kaputt" in script:
    print("Connecting to J-Link via USB...O.K.\\nVTref=3.300V\\nDownloading file...\\n****** Error: Download failed")
    sys.exit(1)
print(%r)
''' % SAMPLE_CONNECT


def make_fake_commander() -> Path:
    folder = DATA / "fake"
    folder.mkdir()
    py = folder / "fakejlink.py"
    py.write_text(FAKE, encoding="utf-8")
    (folder / "fakejlink.py.mode").write_text("", encoding="utf-8")
    if sys.platform == "win32":
        launcher = folder / "JLink.cmd"
        launcher.write_text(f'@"{sys.executable}" "{py}" %*\n', encoding="utf-8")
    else:
        launcher = folder / "JLinkExe"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{py}" "$@"\n', encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    return launcher


def section_driver() -> None:
    print("driver -- gegen einen nachgebauten Commander")
    from jlink import driver
    launcher = make_fake_commander()
    folder = launcher.parent
    probe = driver.JLink(str(launcher), "S32K144", "SWD", 4000, "801012345")
    result = probe.reset()
    args = (folder / "fakejlink.py.args").read_text().split("\n")
    script = (folder / "fakejlink.py.script").read_text()
    check("Reset ok, Kern und VTref gelesen", result.ok and result.info.core == "Cortex-M4"
          and result.info.vtref == 3.297, f"{result.message} {result.info}")
    check("Aufruf mit Seriennummer, Baustein, Schnittstelle, Takt, ohne GUI",
          ["-USB", "801012345"] == args[args.index("-USB"):args.index("-USB") + 2]
          and "S32K144" in args and "SWD" in args and "4000" in args and "-NoGui" in args, str(args))
    check("Skript: verbinden, Reset, Start, Ende", script.split() == ["connect", "r", "g", "exit"], script)
    hexfile = DATA / "fw.hex"
    hexfile.write_text(":00000001FF\n")
    check("Flashen ok", probe.flash(str(hexfile)).ok)
    script = (folder / "fakejlink.py.script").read_text()
    check("Skript: Halt, loadfile, Reset, Start", f'loadfile "{hexfile}"' in script
          and script.split()[:3] == ["connect", "r", "h"], script)
    bad = DATA / "kaputt.hex"
    bad.write_text(":00000001FF\n")
    failed = probe.flash(str(bad))
    check("Fehler beim Flashen gemeldet", not failed.ok and "Download failed" in failed.message, failed.message)
    check("fehlende Datei", not probe.flash(str(DATA / "fehlt.hex")).ok)
    check(".bin abgelehnt (keine Adresse)", "Startadresse" in probe.flash(str(hexfile.with_suffix(".bin"))).message
          if hexfile.with_suffix(".bin").write_text("x") or True else False)
    check("ohne Baustein abgelehnt", "Zielbaustein" in driver.JLink(str(launcher), "").reset().message)
    check("ohne Commander klare Meldung", "nicht gefunden" in driver.JLink("", "S32K144").reset().message)
    (folder / "fakejlink.py.mode").write_text("SLEEP", encoding="utf-8")
    t0 = time.monotonic()
    timed = probe.run(["connect"], timeout=2.0)
    check("Zeitlimit: Abbruch mit EDU-Hinweis", not timed.ok and "EDU" in timed.message
          and time.monotonic() - t0 < 8, timed.message)
    (folder / "fakejlink.py.mode").write_text("", encoding="utf-8")


def section_service(app) -> None:
    print("service -- JLinkService mit Simulation")
    from jlink_service import SIM_ID, JLinkService
    service = JLinkService()
    added: list[str] = []
    service.device_added.connect(lambda _k, d: added.append(d))
    service.apply({"enabled": False}, simulation=True)
    check("Simulation: jlink:SIM", added == [SIM_ID])
    done: list[tuple] = []
    service.finished.connect(lambda *a: done.append(a))
    hexfile = DATA / "fw.hex"
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())
    logging.getLogger("jlink_service").addHandler(Grab())
    logging.getLogger("jlink_service").setLevel(logging.INFO)
    first = service.run(SIM_ID, "JLINK_FLASH", str(hexfile), source="Testablauf")
    second = service.run(SIM_ID, "JLINK_RESET")
    spin(app, 3.0, lambda: len(done) >= 2)
    by_req = {d[0]: d for d in done}
    check("zweite Bedienung waehrend der ersten -> 'beschaeftigt'", second in by_req and not by_req[second][3]
          and "beschäftigt" in by_req[second][4], str(by_req.get(second)))
    check("erste laeuft durch", first in by_req and by_req[first][3], str(by_req.get(first)))
    check("Mock hat geflasht", service.probe(SIM_ID).flashed == [str(hexfile)])
    check("Log mit Datei, SHA-256 und Herkunft", any("JLINK_FLASH" in r and "sha256=" in r and "Testablauf" in r
                                                      and "-> ok" in r for r in records), str(records[-2:]))
    check("Zustand nach dem Flashen", service.state(SIM_ID).get("ok") is True and not service.state(SIM_ID)["busy"])


def section_window(app) -> None:
    print("window -- Hauptfenster in Simulation")
    import no_hardware
    no_hardware.block()
    from main_window import MainWindow
    from settings import Settings
    from testcase_model import TestStep, load_steps, save_steps
    from testcase_tab import COL_VALUE
    import device_worker as dw
    from jlink_service import SIM_ID

    window = MainWindow(Settings())
    window.resize(1280, 800)
    window.show()
    ready = spin(app, 10.0, lambda: SIM_ID in window.control_tab._sections and dw.SIM_PSU_ID in window._worker._psus)
    check("J-Link-Kachel in der Steuerung", ready)
    section = window.control_tab._sections.get(SIM_ID)
    check("Dashboard-Kachel", SIM_ID in window.dashboard._panels)
    check("in der Geraeteauswahl des Testablaufs", window.testcase_tab.knows_device(SIM_ID, "jlink", "J-Link 1"),
          str(window.testcase_tab._known_devices.get(SIM_ID)))
    mock = window._jlink.probe(SIM_ID)
    section._reset_button.click()
    spin(app, 3.0, lambda: mock.resets == 1 and "✓" in section._last_label.text())
    check("Kachel: Reset ausgefuehrt und angezeigt", mock.resets == 1 and "Reset" in section._last_label.text(),
          section._last_label.text())

    folder = DATA / "testcases"
    folder.mkdir(exist_ok=True)
    hexfile = DATA / "app.hex"
    hexfile.write_text(":00000001FF\n")
    path = folder / "Flashen.json"
    save_steps([TestStep(device_kind="jlink", device_id=SIM_ID, action="JLINK_FLASH", file_path=str(hexfile)),
                TestStep(device_kind="jlink", device_id="", action="JLINK_RESET", duration=0.2),
                TestStep(device_kind="psu", device_id=dw.SIM_PSU_ID, action="PSU_VOLT", value=4.0, duration=0.2)],
               path)
    tab = window.testcase_tab
    check("Laden", tab._load_path(path))
    value_stack = tab._table.cellWidget(0, COL_VALUE)
    check("Editor: Dateiauswahl beim Flashen", value_stack.currentIndex() == 5
          and value_stack.widget(5)._label.text() == "app.hex", value_stack.widget(5)._label.text())
    check("Editor: Datei bleibt im Schritt", tab.steps()[0].file_path == str(hexfile))
    finished, failed = [], []
    window._test_runner.run_finished.connect(lambda: finished.append(1))
    window._test_runner.step_failed.connect(lambda _i, m: failed.append(m))
    window._on_run_requested()
    spin(app, 10.0, lambda: finished or failed)
    check("Ablauf Flashen + Reset (automatisch) + Netzteil fertig", finished and not failed, str(failed))
    check("geflasht und zurueckgesetzt", mock.flashed[-1:] == [str(hexfile)] and mock.resets == 2,
          f"{mock.flashed} {mock.resets}")
    check("Netzteil-Schritt danach", window._worker._psus[dw.SIM_PSU_ID].get_setpoint()[0] == 4.0)

    mock.fail_next = "Cannot connect to target."
    finished.clear()
    window._on_run_requested()
    spin(app, 10.0, lambda: finished or failed)
    check("fehlschlagendes Flashen bricht den Ablauf ab", failed and "Cannot connect" in failed[-1], str(failed))

    file_steps, known = tab._steps_for_file(tab.steps())
    save_steps(file_steps, path, known)
    check("Speichern/Laden behaelt die Datei", load_steps(path)[0].file_path == str(hexfile))
    window.close()
    app.processEvents()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    section_parse()
    section_driver()
    section_service(app)
    section_window(app)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
