"""Prueft PI-43 b auf der Seite des Besitzers (0.56.0): Steuer-Kacheln folgen dem Geraet, Name und Farbe vom
Verbund-Partner werden uebernommen.

- Netzteil: nach jeder erfolgreich gesetzten Spannung/Strombegrenzung meldet der Worker die Sollwerte
  (psu_setpoints) -- aus der Kachel UND aus der Fernsteuerung; die Felder der Kachel folgen, ein Feld mit
  Fokus nicht
- Last: nach jedem Sollwert meldet der Worker Modus und Wert (load_setpoint), LiveState fuehrt sie als
  Felder "setpoint"/"setpoint_mode" (fuer den Partner); Modus-Abfrage stellt die Auswahl nach
- Name/Farbe vom Partner (MainWindow._on_share_meta): Umbenennen und Farbe wie am eigenen Rechner,
  unbekannte Farbe und unbekanntes Geraet abgelehnt; die Freigabe meldet die Farbe in der Kachel

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_verbund_sync.py
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
DATA = Path(tempfile.mkdtemp(prefix="lc-sync-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True, "panel_colors_enabled": True}),
                                    encoding="utf-8")

from PySide6.QtWidgets import QApplication  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    import no_hardware
    no_hardware.block()
    from main_window import MainWindow
    from settings import Settings

    def spin(seconds: float, until=None) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.02)
        return bool(until()) if until is not None else False

    w = MainWindow(Settings())
    w.resize(1400, 900)
    w.show()
    psu = w.control_tab.section("psu:SIM")
    load = w.control_tab.section("load:SIM")
    check("Simulation: Netzteil und Last da", spin(6.0, lambda: (psu := w.control_tab.section("psu:SIM")) is not None
                                                 and w.control_tab.section("load:SIM") is not None))
    psu = w.control_tab.section("psu:SIM")
    load = w.control_tab.section("load:SIM")
    psu_events: list = []
    load_events: list = []
    w._worker.psu_setpoints.connect(lambda d, v, a: psu_events.append((d, round(v, 3), round(a, 3))))
    w._worker.load_setpoint.connect(lambda d, m, v: load_events.append((d, m, round(v, 3))))
    spin(1.0)

    print("Netzteil")
    psu.set_voltage.emit("psu:SIM", 5.0)
    check("Kachel setzt 5 V -> Worker meldet die Sollwerte", spin(3.0, lambda: any(e[:2] == ("psu:SIM", 5.0)
                                                                                for e in psu_events)), str(psu_events))
    w._dispatch_share_action.emit(901, "psu:SIM", "psu", "PSU_VOLT", 6.5, 0)
    check("Fernsteuerung setzt 6,5 V -> gemeldet", spin(3.0, lambda: any(e[:2] == ("psu:SIM", 6.5)
                                                                        for e in psu_events)), str(psu_events))
    check("Feld der Kachel folgt (6,5 V)", spin(2.0, lambda: abs(psu._voltage_spin.value() - 6.5) < 1e-6),
          str(psu._voltage_spin.value()))
    w._dispatch_share_action.emit(902, "psu:SIM", "psu", "PSU_CURR", 1.25, 0)
    check("Strombegrenzung 1,25 A -> gemeldet, Spannung bleibt", spin(3.0, lambda: ("psu:SIM", 6.5, 1.25) in psu_events),
          str(psu_events[-3:]))
    psu._voltage_spin.setValue(9.9)
    psu._voltage_spin.hasFocus = lambda: True   # offscreen gibt setFocus() keinen echten Fokus
    w._dispatch_share_action.emit(903, "psu:SIM", "psu", "PSU_VOLT", 7.0, 0)
    spin(1.5, lambda: any(e[:2] == ("psu:SIM", 7.0) for e in psu_events))
    check("Feld mit Fokus (jemand tippt) wird nicht ueberschrieben", abs(psu._voltage_spin.value() - 9.9) < 1e-6,
          str(psu._voltage_spin.value()))
    del psu._voltage_spin.hasFocus

    print("Last")
    load.apply_setpoint.emit("load:SIM", "VOLT", 12.0)
    check("Kachel setzt CV 12 V -> Worker meldet Modus und Wert",
          spin(3.0, lambda: ("load:SIM", "VOLT", 12.0) in load_events), str(load_events))
    w._dispatch_share_action.emit(904, "load:SIM", "load", "CURR", 2.5, 0)
    check("Fernsteuerung CC 2,5 A -> gemeldet", spin(3.0, lambda: ("load:SIM", "CURR", 2.5) in load_events),
          str(load_events))
    check("Kachel folgt (CC, 2,5 A)", spin(3.0, lambda: load._mode_combo.currentData() == "CURR"
                                           and abs(load._setpoint_spin.value() - 2.5) < 1e-6),
          f"{load._mode_combo.currentData()} {load._setpoint_spin.value()}")
    fields = w._live_state.snapshot(["load:SIM"])["devices"]["load:SIM"]["fields"]
    check("LiveState fuehrt setpoint/setpoint_mode fuer den Partner",
          fields.get("setpoint") == 2.5 and fields.get("setpoint_mode") == "CURR", str(fields))

    print("Name und Farbe vom Partner")
    w._on_share_meta(0, "psu:SIM", {"label": "NT Labor", "color": "teal"})
    app.processEvents()
    check("Umbenannt wie am eigenen Rechner", psu.title() == "NT Labor"
          and w.dashboard._panels["psu:SIM"].title() == "NT Labor", psu.title())
    check("Farbe gespeichert und angezeigt", w._settings.panel_color("psu:SIM") == "teal" and psu._color_key == "teal")
    snap = w._live_state.snapshot(["psu:SIM"])
    check("Freigabe kennt die Farbe (Kachel 'color')", snap.get("colors", {}).get("psu:SIM") == "teal")
    w._on_share_meta(0, "psu:SIM", {"color": "lila"})
    check("unbekannte Farbe abgelehnt", w._settings.panel_color("psu:SIM") == "teal")
    w._on_share_meta(0, "psu:SIM", {"color": None})
    check("Farbe entfernen", w._settings.panel_color("psu:SIM") is None)
    w._on_share_meta(0, "psu:NIX", {"label": "x"})
    check("unbekanntes Geraet: nichts passiert", "psu:NIX" not in w._device_labels)

    w.close()
    app.processEvents()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
