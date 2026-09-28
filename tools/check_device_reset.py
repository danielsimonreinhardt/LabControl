"""Prueft die beiden Loesch-Knoepfe im Einstellungen-Reiter "Geraete".

  "Nicht verbundene Geraete loeschen"  vergisst nur Geraete, die gerade nicht
                                      verbunden sind (Label, Grenzwerte, Farbe,
                                      Netzwerk-Freigabe, Kacheln/Sektionen).
  "Alle Geraete loeschen"             wie bisher: alles zuruecksetzen,
                                      verbundene Geraete bekommen neue Namen.

Startet die komplette App im Simulationsmodus (offscreen) mit umgelenkter
settings.json/device_labels.json -- der echte Stand des Nutzers bleibt
unberuehrt, echte Geraete werden nicht gesucht. Die Rueckfragen werden
uebersprungen, indem direkt die Signale des Einstellungen-Reiters ausgeloest
werden (die Rueckfrage selbst ist ein QMessageBox.question).

Aufruf:  python tools/check_device_reset.py     (aus dem Repo-Stamm)
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
sys.path.insert(0, str(ROOT / "device-driver"))  # Geraete-Treiber (jds66xx, picoscope2000, ...)
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

    # Zwei Geraete aus "frueheren Sitzungen", die jetzt NICHT verbunden sind,
    # dazu das simulierte Netzteil (wird verbunden) mit eigenem Namen.
    offline = ["psu:COM99", "load:DEADBEEF"]
    registry_mod.LABELS_PATH.write_text(json.dumps({
        "psu:COM99": "Altes Netzteil", "load:DEADBEEF": "Alte Last", "psu:SIM": "Mein Netzteil",
    }), encoding="utf-8")
    settings_mod.SETTINGS_PATH.write_text(json.dumps({
        "simulation_mode": True,
        "panel_colors_enabled": True,
        "panel_colors": {"psu:COM99": "blue", "psu:SIM": "green"},
        "safety_limits": {"psu:COM99": {"max_voltage": {"enabled": True, "value": 5.0}},
                          "psu:SIM": {"max_voltage": {"enabled": True, "value": 12.0}}},
        "share_devices": {"psu:COM99": {"read": True, "control": False, "measure": False},
                          "psu:SIM": {"read": True, "control": False, "measure": False}},
    }), encoding="utf-8")

    # Keine echten Geraete suchen (an ihnen haengt womoeglich ein Aufbau).
    import device_worker as dw
    dw.KoradKEL102.discover_ports = staticmethod(lambda: [])
    dw.HCS34xx.discover_ports = staticmethod(lambda: [])
    dw.MicroHIL.discover = staticmethod(lambda: None)
    dw.JDS66xx.discover_ports = staticmethod(lambda: [])
    dw.picoscope_usb_present = lambda: False

    from main_window import MainWindow
    settings = settings_mod.Settings()
    window = MainWindow(settings)
    window.show()

    def wait_for(predicate, seconds=10.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    registry = window._registry
    known = lambda: {d for _k, d, _l in registry.known_devices()}
    online = lambda: set().union(*window._online_devices.values())

    print("Ausgangslage")
    check("simuliertes Netzteil verbunden", wait_for(lambda: "psu:SIM" in online()))
    check("alte Geraete bekannt, aber nicht verbunden",
          all(d in known() and d not in online() for d in offline), str(known()))
    check("alte Geraete haben Kacheln", all(d in window.dashboard._panels for d in offline))

    print("Nicht verbundene Geraete loeschen")
    sim_before = {d for d in online()}
    window.settings_tab.remove_offline_devices_requested.emit()
    app.processEvents()
    check("alte Geraete aus der Registry entfernt", not (known() & set(offline)), str(known()))
    check("Kacheln der alten Geraete entfernt", not any(d in window.dashboard._panels for d in offline))
    check("Sektionen im Einstellungen-Reiter entfernt",
          not any(d in window.settings_tab._safety_sections for d in offline))
    cfg = settings_mod.Settings()
    check("Grenzwerte, Farbe, Freigabe des alten Netzteils geloescht",
          "psu:COM99" not in cfg.safety_limits and cfg.panel_color("psu:COM99") is None
          and "psu:COM99" not in cfg.share_config["devices"])
    check("verbundenes Netzteil behaelt seinen Namen",
          dict((d, l) for _k, d, l in registry.known_devices()).get("psu:SIM") == "Mein Netzteil")
    check("verbundenes Netzteil behaelt Grenzwert, Farbe, Freigabe",
          "psu:SIM" in cfg.safety_limits and cfg.panel_color("psu:SIM") == "green"
          and cfg.share_config["devices"]["psu:SIM"]["read"] is True)
    check("verbundene Geraete bleiben verbunden und sichtbar",
          online() == sim_before and all(d in window.dashboard._panels for d in sim_before))
    message = window.statusBar().currentMessage()
    check("Statuszeile meldet die Anzahl", message.startswith("2 "), message)

    window.settings_tab.remove_offline_devices_requested.emit()
    app.processEvents()
    check("zweiter Klick ohne Offline-Geraete: Hinweis, nichts geloescht",
          "Keine" in window.statusBar().currentMessage() and "psu:SIM" in known())

    print("Alle Geraete loeschen (unveraendertes Verhalten)")
    window.settings_tab.reset_devices_requested.emit()
    app.processEvents()
    cfg = settings_mod.Settings()
    labels = dict((d, l) for _k, d, l in registry.known_devices())
    check("verbundenes Netzteil bekommt Standardnamen", labels.get("psu:SIM") not in (None, "Mein Netzteil"),
          str(labels.get("psu:SIM")))
    # Mit aktivierten Panel-Farben bekommt ein verbundenes Geraet nach dem Zuruecksetzen
    # sofort automatisch die naechste freie Farbe (main_window, PANEL_COLOR_ORDER) --
    # entscheidend ist, dass die vorher gewaehlte ("green") weg ist.
    check("alle Grenzwerte und Freigaben geloescht, gewaehlte Farbe ersetzt",
          cfg.safety_limits == {} and cfg.share_config["devices"] == {}
          and cfg.panel_color("psu:SIM") != "green",
          f"{cfg.safety_limits} {cfg.panel_color('psu:SIM')} {cfg.share_config['devices']}")

    window.close()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FEHLER:")
        for name in FAILURES:
            print("  - " + name)
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
