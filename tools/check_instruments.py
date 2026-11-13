"""Prueft FT-8 Messmittel (0.59.1): Kennungen, messmittel.json, Messmittel in Bericht/JUnit/run-result.json.

- parse    -- instruments.parse_idn fuer KEL102, microHIL, SCPI
- store    -- Instruments: Datum pruefen, Datei schreiben/lesen, abgelaufen
- window   -- Hauptfenster (Simulation): Kennungen der simulierten Geraete, Tabelle in den Einstellungen
              (Eingabe speichert, falsches Datum wird verworfen), Lauf mit abgelaufener Kalibrierung ->
              Warnung in Bericht, JUnit und run-result.json, Lauf trotzdem bestanden (FP-21 a)

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_instruments.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-instruments-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []


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
    print("parse -- Kennungen")
    from instruments import parse_idn
    kel = parse_idn("KORAD-SLAB KEL102 V2.20 SN:00010925")
    check("KEL102", kel == {"model": "KORAD-SLAB KEL102", "serial": "00010925", "firmware": "V2.20"}, str(kel))
    hil = parse_idn("microHIL,fw=0.1.0,SN=2065386A5631")
    check("microHIL", hil == {"model": "microHIL", "serial": "2065386A5631", "firmware": "0.1.0"}, str(hil))
    scpi = parse_idn("UNI-T Technologies,MSO1154HD-S,SN123,1.00.0009")
    check("SCPI *IDN?", scpi["serial"] == "SN123" and scpi["firmware"] == "1.00.0009"
          and "MSO1154HD-S" in scpi["model"], str(scpi))
    check("leer", parse_idn("") == {"model": "", "serial": "", "firmware": ""})


def section_store() -> None:
    print("store -- messmittel.json")
    from instruments import Instruments
    path = DATA / "store" / "messmittel.json"
    inst = Instruments(lambda: path)
    try:
        inst.set_calibration("psu:X", due="13.11.2026")
        rejected = False
    except ValueError:
        rejected = True
    check("Datum nicht JJJJ-MM-TT -> abgelehnt", rejected and not path.exists())
    inst.set_calibration("psu:X", inventory="INV-7", calibrated="2025-01-10", due="2026-01-10")
    other = Instruments(lambda: path)
    check("zweite Instanz liest dieselbe Datei", other.calibration("psu:X")["inventory"] == "INV-7")
    rows = other.for_run(["psu:X", "load:Y"], {"psu:X": "NT"}, today=date(2026, 11, 13))
    check("abgelaufen erkannt", rows[1]["expired"] and rows[1]["label"] == "NT" and not rows[0]["expired"], str(rows))
    other.set_calibration("psu:X", inventory="", calibrated="", due="")
    check("leere Eintraege fallen aus der Datei",
          json.loads(path.read_text(encoding="utf-8"))["devices"] == {})


def section_window() -> None:
    print("window -- Hauptfenster")
    import no_hardware
    no_hardware.block()
    import data_store
    import device_worker as dw
    import testcase_model as tm
    from PySide6.QtWidgets import QApplication
    from main_window import MainWindow
    from settings import Settings

    app = QApplication.instance() or QApplication([])
    window = MainWindow(Settings())
    window.resize(1280, 800)
    window.show()
    inst = window._instruments
    spin(app, 5.0, lambda: inst.identity(dw.SIM_LOAD_ID).get("model") and inst.identity(dw.SIM_PSU_ID).get("model"))
    check("Last meldet Typ", bool(inst.identity(dw.SIM_LOAD_ID).get("model")), str(inst.identity(dw.SIM_LOAD_ID)))
    check("Netzteil: Typ aus den Nennwerten", inst.identity(dw.SIM_PSU_ID).get("model", "").startswith("HCS-34xx"),
          str(inst.identity(dw.SIM_PSU_ID)))

    editor = window.settings_tab.instruments_editor
    editor.refresh()
    rows = {editor.table.item(r, 0).data(0x0100): r for r in range(editor.table.rowCount())}
    check("Tabelle nennt das Netzteil", dw.SIM_PSU_ID in rows, str(list(rows)))
    row = rows.get(dw.SIM_PSU_ID, 0)
    editor.table.item(row, 6).setText("kaputt")
    check("falsches Datum verworfen", inst.calibration(dw.SIM_PSU_ID)["due"] == "" and editor.table.item(row, 6).text() == "")
    editor.table.item(row, 4).setText("NT-001")
    editor.table.item(row, 6).setText("2020-01-01")
    path = data_store.store().shared_file("messmittel.json")
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    check("Eingabe gespeichert", saved.get("devices", {}).get(dw.SIM_PSU_ID, {}).get("due") == "2020-01-01"
          and saved["devices"][dw.SIM_PSU_ID]["inventory"] == "NT-001", str(saved))

    case = DATA / "mm.json"
    tm.save_steps([tm.TestStep(device_kind="psu", device_id=dw.SIM_PSU_ID, action="PSU_VOLT", value=3.0,
                               duration=0.3, check_enabled=True, check_field="voltage", check_min=0, check_max=100)],
                  case)
    window.testcase_tab._load_path(case)
    window._on_run_requested()
    spin(app, 15.0, lambda: not window._test_runner.is_running())
    spin(app, 1.0)
    record = window._run_recorder.record()
    check("Lauf bestanden trotz abgelaufener Kalibrierung", record is not None and record.outcome == "passed",
          record.outcome if record else "kein Lauf")
    psu_rows = [r for r in (record.instruments if record else []) if r["device_id"] == dw.SIM_PSU_ID]
    check("Messmittel im Lauf, abgelaufen", psu_rows and psu_rows[0]["expired"] and psu_rows[0]["inventory"] == "NT-001",
          str(psu_rows))
    entry = window._live_state.runs.list()[0]
    files = {k: window._live_state.runs.file(entry["id"], k) for k in ("result", "junit", "report")}
    result = json.loads(files["result"].read_text(encoding="utf-8")) if files["result"] else {}
    check("run-result.json: Messmittel und Warnung", result.get("instruments") and result.get("warnings"),
          str(result.get("warnings")))
    suite = ET.parse(files["junit"]).getroot().find("testsuite") if files["junit"] else None
    props = {p.get("name"): p.get("value") for p in suite.find("properties")} if suite is not None else {}
    check("JUnit: calibration.expired", props.get("calibration.expired") == dw.SIM_PSU_ID, str(props))
    check("JUnit: Warnung in system-err, keine failure",
          suite is not None and "Kalibrierung abgelaufen" in (suite.findtext("system-err") or "")
          and suite.get("failures") == "0")
    report = files["report"].read_text(encoding="utf-8") if files["report"] else ""
    check("Bericht: Abschnitt Messmittel mit Warnung", "Messmittel" in report and "Kalibrierung abgelaufen" in report
          and "NT-001" in report)
    window.close()
    app.processEvents()


def main() -> int:
    section_parse()
    section_store()
    section_window()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
