"""Freigabe-Tabelle: Haken stimmen auch fuer Geraete, die erst NACH dem Laden der Einstellungen erkannt werden.

Fehlerbild (1.0.2 gemeldet): nach dem Start standen die Haken "Lesen/Steuern/Messen" leer, obwohl die Freigabe
galt; erst der erste Klick zeigte den richtigen Stand. Ursache: set_share_config setzte nur vorhandene Zeilen,
die Geraete kommen aber spaeter ueber on_device_known.

Nur Oberflaeche (offscreen), keine Hardware.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def boxes(tab, device_id: str) -> tuple[bool, bool, bool]:
    table = tab._share_table
    return tuple(bool(table._box(device_id, col).isChecked())
                 for col in (table.COL_READ, table.COL_CONTROL, table.COL_MEASURE))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from settings_tab import SettingsTab

    tab = SettingsTab()
    emitted: list[tuple] = []
    tab.share_device_changed.connect(lambda *a: emitted.append(a))
    config = {"enabled": True, "devices": {
        "psu:A": {"read": True, "control": True},
        "scpiscope:1.2.3.4": {"read": True, "measure": True},
        "fg:1.2.3.4:AWG1": {"read": True, "control": True},
    }}
    print("Stand zuerst, Geraete danach (wie beim Start)")
    tab.set_share_config(config)
    tab.on_device_known("psu", "psu:A", "Netzteil")
    tab.on_device_known("scpiscope", "scpiscope:1.2.3.4", "Scope")
    tab.on_device_known("fg", "fg:1.2.3.4:AWG1", "Generator")
    tab.on_device_known("load", "load:B", "Last")
    app.processEvents()
    check("Netzteil: Lesen + Steuern", boxes(tab, "psu:A") == (True, True, False), str(boxes(tab, "psu:A")))
    check("Oszilloskop: Lesen + Messen", boxes(tab, "scpiscope:1.2.3.4") == (True, False, True),
          str(boxes(tab, "scpiscope:1.2.3.4")))
    check("Generator: Lesen + Steuern", boxes(tab, "fg:1.2.3.4:AWG1") == (True, True, False),
          str(boxes(tab, "fg:1.2.3.4:AWG1")))
    check("nicht freigegebenes Geraet leer", boxes(tab, "load:B") == (False, False, False), str(boxes(tab, "load:B")))
    check("Befuellen meldet nichts als Nutzeraktion", not emitted, str(emitted))

    print("Geraete zuerst, Stand danach")
    tab2 = SettingsTab()
    tab2.on_device_known("psu", "psu:A", "Netzteil")
    tab2.set_share_config(config)
    check("Netzteil: Lesen + Steuern", boxes(tab2, "psu:A") == (True, True, False), str(boxes(tab2, "psu:A")))

    print("Geraet weg und wieder da")
    tab.forget_device("psu:A")
    tab.on_device_known("psu", "psu:A", "Netzteil")
    check("Netzteil wieder mit Haken", boxes(tab, "psu:A") == (True, True, False), str(boxes(tab, "psu:A")))

    print("Klick wirkt weiter")
    table = tab._share_table
    table._box("load:B", table.COL_CONTROL).setChecked(True)
    check("Steuern anhaken setzt Lesen mit und meldet", boxes(tab, "load:B")[:2] == (True, True)
          and emitted and emitted[-1][0] == "load:B", str(emitted[-1:] if emitted else emitted))

    print()
    if FAILURES:
        print("FEHLGESCHLAGEN:", FAILURES)
        return 1
    print("Alles ok. (nur Oberflaeche, keine Hardware)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
