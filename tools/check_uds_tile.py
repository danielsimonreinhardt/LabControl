"""Prueft FT-1 Kachel (0.60.2): Geraet "Pruefling" erscheint mit eingestelltem ODX in Steuerung, Uebersicht und
Statusleiste, Bedienung ueber die Kachel (Session, Entsperren, DID lesen, Fehlerspeicher, Reset), Sperre waehrend
eines Laufs, Neustart mit gespeichertem Geraet.

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_uds_tile.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-udstile-check-"))
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


def make_window():
    from main_window import MainWindow
    from settings import Settings
    window = MainWindow(Settings())
    window.resize(1280, 800)
    window.show()
    return window


def main() -> int:
    import no_hardware
    no_hardware.block()
    from PySide6.QtWidgets import QApplication, QMessageBox
    from main_window import UDS_DEVICE_ID
    import testcase_model as tm
    app = QApplication.instance() or QApplication([])
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)

    print("Kachel")
    window = make_window()
    tile = None
    spin(app, 6.0, lambda: UDS_DEVICE_ID in window.control_tab._sections
         and UDS_DEVICE_ID in window._online_devices.get("uds", set()))
    tile = window.control_tab._sections.get(UDS_DEVICE_ID)
    check("Kachel 'Pruefling' in der Steuerung", tile is not None)
    check("online, sobald der simulierte CAN-Bus da ist", UDS_DEVICE_ID in window._online_devices.get("uds", set()))
    check("Kachel in der Uebersicht", UDS_DEVICE_ID in window.dashboard._panels)
    check("Chip in der Statusleiste", UDS_DEVICE_ID in window._status_labels)
    check("Auswahllisten aus dem ODX", tile._session_combo.count() == 2 and tile._did_combo.count() >= 5)

    def press(button, until_key="busy"):
        button.click()
        return spin(app, 5.0, lambda: not window._uds_state.get(until_key))

    tile._session_combo.setCurrentIndex(tile._session_combo.findData("Extended"))
    press(tile._session_button)
    check("Session wechseln", window._uds_state.get("session") == "Extended" and "Extended" in tile._session_label.text(),
          str(window._uds_state))
    press(tile._unlock_button)
    check("Entsperren", window._uds_state.get("unlocked") is True and "entsperrt" in tile._session_label.text())
    tile._did_combo.setCurrentIndex(tile._did_combo.findData(0x0120))
    press(tile._read_button)
    check("DID lesen zeigt Werte mit Einheit", "supply = 13800 mV" in tile._values_label.text(), tile._values_label.text())
    press(tile._dtc_read_button)
    check("Fehlerspeicher lesen", window._uds_state.get("dtc_count") == 2 and "Simulierter Fehler" in tile._dtc_label.text())
    check("Uebersicht zeigt DTC-Zahl", window.dashboard._panels[UDS_DEVICE_ID]._values["ud_dtcs"].text() == "2"
          if hasattr(window.dashboard._panels[UDS_DEVICE_ID], "_values") else True)
    press(tile._dtc_clear_button)
    check("Fehlerspeicher loeschen", window._uds_state.get("dtc_count") == 0)
    press(tile._reset_button)
    check("Reset -> Default, gesperrt", window._uds_state.get("session") == "Default"
          and not window._uds_state.get("unlocked"))

    print("Sperre waehrend eines Laufs")
    path = DATA / "warten.json"
    tm.save_steps([tm.TestStep(device_kind="uds", action="UDS_SESSION", params={"session": "Extended"}),
                   tm.TestStep(step_type="wait", duration=3.0)], path)
    window.testcase_tab._load_path(path)
    window._on_run_requested()
    spin(app, 1.0)
    before = dict(window._uds_state)
    tile._read_button.click()
    spin(app, 0.5)
    check("Kachel-Befehl waehrend des Laufs abgelehnt", window._uds_state.get("last") == before.get("last")
          and not window._uds_state.get("busy"))
    spin(app, 6.0, lambda: not window._test_runner.is_running())
    record = window._run_recorder.record()
    check("Lauf mit UDS-Schritt bestanden", record is not None and record.outcome == "passed",
          record.outcome if record else "")
    window.close()
    app.processEvents()

    print("Neustart")
    window = make_window()
    spin(app, 6.0, lambda: UDS_DEVICE_ID in window._online_devices.get("uds", set()))
    check("gespeichertes Geraet kommt als Pruefling wieder (nicht als CAN)",
          window._device_kinds.get(UDS_DEVICE_ID) == "uds"
          and type(window.control_tab._sections.get(UDS_DEVICE_ID)).__name__ == "UdsControlGroup")
    def tile_label() -> str:
        return {d: label for _k, d, label in window._registry.known_devices()}.get(UDS_DEVICE_ID, "")

    check("ohne Namen: automatischer Name", tile_label().startswith("Prüfling"), tile_label())
    window._settings.set_dut_config(name="Evalboard S32K144")
    spin(app, 0.3)
    check("Name aus den Einstellungen wird Kachelname", tile_label() == "Evalboard S32K144", tile_label())
    window._settings.set_dut_config(name="Evalboard B")
    spin(app, 0.3)
    check("geaenderter Name folgt", tile_label() == "Evalboard B", tile_label())
    window._registry.rename("uds", UDS_DEVICE_ID, "Mein Board")
    window._settings.set_dut_config(name="Evalboard C")
    spin(app, 0.3)
    check("selbst vergebener Kachelname bleibt", tile_label() == "Mein Board", tile_label())
    window._settings.set_dut_config(odx=str(DATA / "gibtsnicht.pdx"))
    spin(app, 1.0)
    check("ohne lesbares ODX: Pruefling getrennt", UDS_DEVICE_ID not in window._online_devices.get("uds", set()))
    window.close()
    app.processEvents()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
