"""Prueft die Handbedienung des Netzteils im Hauptfenster: Umschalter in der Kachel -> Worker ->
Dashboard, Statusleiste, Kachel. Offscreen, Simulationsmodus (MockHCS34xx), keine Hardware.

Aufruf:  python tools/check_psu_manual_window.py   (aus dem Repo-Stamm)
"""
from __future__ import annotations

import json
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

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    import settings as settings_mod
    import device_registry as registry_mod
    tmp = pathlib.Path(tempfile.mkdtemp())
    settings_mod.SETTINGS_PATH = tmp / "settings.json"
    registry_mod.LABELS_PATH = tmp / "device_labels.json"
    settings_mod.SETTINGS_PATH.write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

    import device_worker as dw
    dw.KoradKEL102.discover_ports = staticmethod(lambda: [])
    dw.HCS34xx.discover_ports = staticmethod(lambda: [])
    dw.MicroHIL.discover = staticmethod(lambda: None)
    dw.JDS66xx.discover_ports = staticmethod(lambda: [])
    dw.picoscope_usb_present = lambda: False

    from main_window import MainWindow
    window = MainWindow(settings_mod.Settings())
    window.resize(1400, 900)
    window.show()

    def spin(seconds: float, until=None) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        return until() if until is not None else False

    SIM = dw.SIM_PSU_ID
    spin(3.0, until=lambda: SIM in window.control_tab._sections)
    tile = window.control_tab._sections[SIM]
    panel = window.dashboard._panels[SIM]
    mock = window._worker._psus[SIM]

    def status_text() -> str:
        # Seit PI-17 a (0.43.0) ein Chip "Ⓗ NT1"; der Zustand in Worten steht im Tooltip "Name: Zustand"
        return window._status_labels[SIM].toolTip()

    check("Netzteil ist verbunden", panel._online and "verbunden" in status_text(), status_text())

    print("Umschalter in der Kachel")
    tile._manual_button.click()
    spin(8.0, until=lambda: SIM in window._manual_devices)
    check("Worker schaltet auf Handbedienung", SIM in window._worker._psu_manual)
    check("Geraet freigegeben: 1 V / 0 A / Ausgang EIN",
          mock.get_setpoint() == (dw.PSU_MIN_VOLTAGE, 0.0) and mock.get_output() is True, str(mock.get_setpoint()))
    check("Kachel gesperrt, Umschalter bedienbar", not tile._voltage_spin.isEnabled() and tile._manual_button.isEnabled())
    check("Kachel bleibt sichtbar (Zurueckschalten moeglich)", tile.isVisible())
    check("Statusleiste zeigt 'Handbedienung'", "Handbedienung" in status_text(), status_text())
    check("Dashboard-Kachel wie nicht verfuegbar", not panel._online)
    check("Netzwerk-Freigabe/MCP: Geraet nicht verfuegbar", window._device_online.get(SIM) is False)

    print("Zurueck zur Kontrolle durch LabControl")
    mock.set_voltage(6.0)
    mock.set_current(2.0)
    tile._manual_button.click()
    spin(8.0, until=lambda: SIM not in window._manual_devices and tile._voltage_spin.isEnabled())
    check("Worker nimmt die Kontrolle zurueck", SIM not in window._worker._psu_manual)
    check("Statusleiste zeigt wieder 'verbunden'", "verbunden" in status_text() and "Hand" not in status_text(), status_text())
    check("Dashboard-Kachel wieder verfuegbar", panel._online)
    check("Kachel wieder bedienbar", tile._voltage_spin.isEnabled())
    check("Felder zeigen die am Geraet eingestellten Werte",
          tile._voltage_spin.value() == 6.0 and tile._current_spin.value() == 2.0,
          f"{tile._voltage_spin.value()} V / {tile._current_spin.value()} A")

    print("Beenden")
    window._worker.set_psu_voltage(SIM, 12.0)
    window._worker.set_psu_current(SIM, 3.0)
    window._worker.set_psu_output(SIM, False)
    spin(0.5)
    window.close()
    check("Fenster schliessen gibt das Netzteil frei (1 V / 0 A / EIN)",
          mock.get_setpoint() == (dw.PSU_MIN_VOLTAGE, 0.0) and mock.get_output() is True,
          f"{mock.get_setpoint()} {mock.get_output()}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
