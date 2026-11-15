"""Screenshots fuer die Dokumentation (PI-63 a): LabControl im Simulationsmodus mit Beispieldaten, offscreen.

Erzeugt die Bilder in docs/de/bilder/ (Dateinamen siehe SHOTS unten -- die Doku verweist darauf). Keine echte
Hardware (no_hardware), eigener Datenordner. Unter Windows mit der Systemschrift aus C:/Windows/Fonts, unter Linux
mit den installierten Schriften.

Aufruf:  python tools/docs_screenshots.py            (alle)
         python tools/docs_screenshots.py testablauf  (nur Bilder, deren Name das Wort enthaelt)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
# Neutraler Ort: Pfade erscheinen in den Bildern (Log-Pfad, DBC) -- kein Benutzername im Bild
DATA = (Path(os.environ.get("PUBLIC", "C:/Users/Public")) if sys.platform == "win32"
        else Path(tempfile.gettempdir())) / "LabControl-Doku"
shutil.rmtree(DATA, ignore_errors=True)
DATA.mkdir(parents=True)
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
OUT = ROOT / "docs" / "de" / "bilder"

DEMO_DBC = '''VERSION ""

NS_ :

BS_:

BU_: PC ECU

BO_ 528 PC_Command: 8 PC
 SG_ Setpoint_mV : 7|16@0- (0.5,1000) [-4000|6000] "mV" ECU
 SG_ Offset_mV : 16|12@1- (1,-100) [-2148|1947] "mV" ECU
 SG_ Mode : 28|2@1+ (1,0) [0|3] "" ECU
 SG_ PcCrc : 32|8@1+ (1,0) [0|255] "" ECU
 SG_ PcCounter : 40|4@1+ (1,0) [0|15] "" ECU

BO_ 272 EVB_Status: 8 ECU
 SG_ Led1 : 0|1@1+ (1,0) [0|1] "" PC
 SG_ MotorNtc_mV : 8|16@1+ (1,0) [0|65535] "mV" PC

BA_DEF_ BO_  "GenMsgCycleTime" INT 0 65535;
BA_DEF_DEF_  "GenMsgCycleTime" 0;
BA_ "GenMsgCycleTime" BO_ 528 100;
BA_ "GenMsgCycleTime" BO_ 272 100;
VAL_ 528 Mode 0 "Off" 1 "Slow" 2 "Fast" ;
'''


def prepare() -> None:
    (DATA / "demo.dbc").write_text(DEMO_DBC, encoding="utf-8")
    os.chdir(DATA)
    settings = {
        "simulation_mode": True,
        "dut": {"name": "Evalboard (Simulation)"},
        "restbus": {"can:mock:SIM": {"dbc": str(DATA / "demo.dbc"), "messages": [
            {"name": "PC_Command", "cycle_ms": 100, "enabled": True, "values": {"Mode": 2},
             "e2e": {"profile": "P01", "data_id": 0x210, "data_id_mode": "BOTH", "crc": "PcCrc",
                     "counter": "PcCounter"}}]}},
        "selected_variant": "PowerUnit",
    }
    (DATA / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    import variants
    folder = DATA / "varianten"
    variants.save_variant(variants.Variant("PowerUnit", {"Versorgung": "psu:SIM", "Last": "load:SIM"},
                                           {"U_nom": 12.0, "U_min": 11.5, "U_max": 12.5},
                                           "PowerUnit am Pi"), folder)
    variants.save_variant(variants.Variant("DriveUnit", {"Versorgung": "psu:SIM", "Last": "load:SIM"},
                                           {"U_nom": 24.0, "U_min": 23.0, "U_max": 25.0}), folder)


def demo_steps():
    from testcase_model import TestStep
    volt = TestStep(device_kind="psu", device_id="role:Versorgung", action="PSU_VOLT", value=12.0, duration=0.5,
                    params={"refs": {"value": "U_nom"}})
    on = TestStep(device_kind="psu", device_id="role:Versorgung", action="PSU_OUT_ON", duration=1.0,
                  check_enabled=True, check_field="voltage", check_min=11.5, check_max=12.5,
                  params={"refs": {"check_min": "U_min", "check_max": "U_max"}})
    on.requirements = ["SYS-12"]
    loop = TestStep(step_type="loop", loop_count=3)
    curr = TestStep(device_kind="load", device_id="role:Last", action="CURR", value=0.5, duration=0.5)
    load_on = TestStep(device_kind="load", device_id="role:Last", action="OUT_ON", duration=0.8,
                       check_enabled=True, check_field="current", check_min=0.4, check_max=0.6)
    load_off = TestStep(device_kind="load", device_id="role:Last", action="OUT_OFF", duration=0.2)
    end = TestStep(step_type="end")
    did = TestStep(device_kind="uds", device_id="uds:dut", action="UDS_READ_DID", duration=0.2,
                   params={"did": "EcuConditions", "element": "supply"})
    off = TestStep(device_kind="psu", device_id="role:Versorgung", action="PSU_OUT_OFF", duration=0.2)
    return [volt, on, loop, curr, load_on, load_off, end, did, off]


SHOTS: list[tuple[str, str]] = [
    ("steuerung.png", "Steuerung mit Kacheln (Simulation)"),
    ("testablauf_editor.png", "Testablauf-Editor mit Beispielablauf"),
    ("testablauf_lauf.png", "Testablauf nach einem Lauf"),
    ("testablauf_pause.png", "Testablauf angehalten am Haltepunkt"),
    ("verlauf.png", "Verlauf mit Messwerten"),
    ("ergebnisse.png", "Reiter Ergebnisse"),
    ("einstellungen_allgemein.png", "Einstellungen – Allgemein"),
    ("einstellungen_geraete.png", "Einstellungen – Geräte (mit Prüfling)"),
    ("einstellungen_can.png", "Einstellungen – CAN"),
    ("einstellungen_sicherheit.png", "Einstellungen – Sicherheit"),
    ("einstellungen_netzwerk.png", "Einstellungen – Netzwerk"),
    ("varianten.png", "Dialog Varianten"),
    ("restbus.png", "Restbus-Ansicht"),
    ("touch_uebersicht.png", "Touch-Oberfläche 1024 × 600: Übersicht"),
    ("touch_testablauf.png", "Touch-Oberfläche: Testablauf ausführen"),
]


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    prepare()
    import no_hardware
    no_hardware.block()
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    if sys.platform == "win32":
        app.setFont(QFont("Segoe UI", 9))
    import testcase_model as tm
    from i18n import Translator
    from main_window import MainWindow
    from settings import Settings
    from theme import ThemeManager

    def spin(seconds: float, until=None) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if until is not None and until():
                return
            time.sleep(0.01)

    OUT.mkdir(parents=True, exist_ok=True)

    def page(widget) -> None:
        """Seite ueber die Navigation waehlen (wie ein Klick, damit auch die Markierung links stimmt)."""
        entry = w._nav._buttons.get(id(widget))
        if entry is not None:
            entry[0].click()
        else:
            w.tabs.setCurrentWidget(widget)
        spin(0.5)

    from PySide6.QtWidgets import QLabel, QLineEdit

    def masked(text: str) -> str:
        for form in (str(DATA), str(DATA).replace("\\", "/"), DATA.as_posix()):
            text = text.replace(form, "<Datenordner>")
        return text

    def save(widget, name: str) -> None:
        """Aufnahme; Pfade des Datenordners erscheinen als <Datenordner> (Review DR-2), danach zurueck."""
        if only and only not in name:
            return
        spin(0.3)
        restore = []
        for child in [widget, *widget.findChildren(QLabel), *widget.findChildren(QLineEdit)]:
            if isinstance(child, (QLabel, QLineEdit)) and masked(child.text()) != child.text():
                restore.append((child, child.text()))
                child.setText(masked(child.text()))
        app.processEvents()
        widget.grab().save(str(OUT / name))
        for child, text in restore:
            child.setText(text)
        print("  ", name, f"({len(restore)} Pfade ersetzt)" if restore else "")

    settings = Settings()
    Translator.instance().set_language(settings.language)
    w = MainWindow(settings)
    w.resize(1280, 800)
    w.show()
    spin(6, lambda: w._directory.is_online("psu:SIM") and w._directory.is_online("load:SIM"))
    spin(2)

    page(w.control_tab)
    w.resize(1280, 1000)
    save(w, "steuerung.png")
    w.resize(1280, 800)

    folder = DATA / "testcases"
    folder.mkdir(exist_ok=True)
    path = folder / "Ladetest PowerUnit.json"
    tm.save_steps(demo_steps(), path, meta={"description": "Versorgung hochfahren, Last in drei Stufen, "
                                                           "UDS-Versorgung lesen", "requirements": ["SYS-12"]})
    page(w.testcase_tab)
    w.testcase_tab._load_path(path)
    w.testcase_tab.reload_variants()
    save(w, "testablauf_editor.png")

    # Verlauf: Netzteil-Spannung und Last-Strom ins erste Diagramm (vor den Laeufen, damit Daten da sind)
    timeline = w.timeline_tab
    for series in list(timeline._series.values()):
        if (series.device_id, series.field) in {("psu:SIM", "voltage"), ("load:SIM", "current")}:
            timeline._on_add_signal(timeline._charts[0], series)
    runner = w._test_runner
    for _run in range(3):
        w._on_run_requested()
        spin(20, lambda: not runner.is_running())
        spin(1)
    save(w, "testablauf_lauf.png")

    page(w.timeline_tab)
    save(w, "verlauf.png")

    page(w.results_tab)
    w.results_tab.reload()
    w.results_tab._tabs.setCurrentIndex(1)
    save(w, "ergebnisse.png")

    page(w.testcase_tab)
    w.testcase_tab.set_breakpoint(4, True)
    w._on_run_requested()
    spin(20, lambda: runner.is_paused())
    save(w, "testablauf_pause.png")
    runner.stop()
    w._safe_stop("Screenshots")
    w.testcase_tab.set_breakpoint(4, False)
    spin(1)

    page(w.settings_tab)
    for index, name in enumerate(("einstellungen_allgemein.png", "einstellungen_geraete.png",
                                  "einstellungen_can.png", "einstellungen_sicherheit.png",
                                  "einstellungen_netzwerk.png")):
        w.settings_tab._subtabs.setCurrentIndex(index)
        save(w, name)

    import variants
    from variant_dialog import VariantDialog
    dialog = VariantDialog(DATA / variants.FOLDER, {d: (k, lbl) for k, d, lbl in w._registry.known_devices()}
                           if hasattr(w, "_registry") else {"psu:SIM": ("psu", "Netzteil Sim")}, w)
    dialog.resize(860, 520)
    dialog.show()
    save(dialog, "varianten.png")
    dialog.close()

    w._open_restbus("can:mock:SIM")
    restbus = w._restbus_dialogs.get("can:mock:SIM")
    if restbus is not None:
        w._restbus_cmd.emit("can:mock:SIM", "start", "", "", "")
        restbus._messages.setCurrentCell(1, 1)
        restbus._show_message(1)
        spin(1.5)
        save(restbus, "restbus.png")
        w._restbus_cmd.emit("can:mock:SIM", "stop", "", "", "")
        restbus.close()

    w.settings_tab._touch_checkbox.setChecked(True)   # wie in den Einstellungen (check_touch_ui)
    w.resize(1024, 600)
    spin(1.5)
    page(w._overview_page)
    save(w, "touch_uebersicht.png")
    page(w.testcase_tab)
    save(w, "touch_testablauf.png")
    w.settings_tab._touch_checkbox.setChecked(False)

    w._safe_stop("Screenshots fertig")
    spin(0.5)
    w.close()
    shutil.rmtree(DATA, ignore_errors=True)
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
