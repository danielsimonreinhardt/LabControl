"""Prueft die CAN-Interface-Karten in den Einstellungen (settings_tab._CanConfigTable, 0.51.0):

Nutzerfeedback 12.11.2026: die Tabelle mit zehn Spalten passte am Pi (1024 px, Touch) nicht ins Fenster.
- Karten passen in 860 px Breite (Seite neben der Navigation), auch im Touch-Profil
- configs() liefert dieselben Eintraege wie gesetzt (Rundlauf), serial_baudrate nur bei slcan
- Serial-Baudrate nur bei slcan sichtbar
- Kanal-Token aus der Auswahl bleibt, Tippen von Hand verwirft es
- Entfernen und Hinzufuegen melden changed; Verbindungsfehler-Symbol an der richtigen Karte

Offscreen, keine Hardware.  Aufruf:  python tools/check_can_cards.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def pump(app, n: int = 10) -> None:
    for _ in range(n):
        app.processEvents()
        QTest.qWait(5)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    import touch_ui
    from settings_tab import _CanConfigTable
    from theme import ThemeManager

    configs = [
        {"interface": "pcan", "channel": "can0", "bitrate": 500000, "label": "", "dbc_path": "",
         "device": "battsim"},
        {"interface": "slcan", "channel": "COM7", "bitrate": 250000, "label": "HIL", "dbc_path": "",
         "serial_baudrate": 115200},
    ]
    for touch in (False, True):
        print("Touch-Profil" if touch else "Desktop")
        ThemeManager.instance().set_touch(touch)
        touch_ui.sizer().set_active(touch)
        table = _CanConfigTable()
        changes: list[int] = []
        table.changed.connect(lambda: changes.append(1))
        table.set_configs(configs)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(table)
        area.resize(860, 500)
        area.show()
        pump(app, 20)
        widest = max(row["card"].minimumSizeHint().width() for row in table._rows)
        check("Karten passen in 860 px", widest <= 860 - 20 and table.width() <= 860, f"{widest} / {table.width()}")
        check("kein waagerechtes Scrollen", not area.horizontalScrollBar().isVisible())
        check("Rundlauf configs()", table.configs() == configs, str(table.configs()))
        check("Serial-Baudrate nur bei slcan sichtbar",
              not table._rows[0]["serial"].isVisible() and table._rows[1]["serial"].isVisible())
        row = table._rows[0]
        row["channel"].setText("PCAN_USBBUS1 (S/N 1)")
        row["channel"].setProperty("_channel_token", "can0")
        check("Kanal-Token aus der Auswahl gilt", table.configs()[0]["channel"] == "can0")
        row["channel"].textEdited.emit("can1")
        row["channel"].setText("can1")
        check("Tippen von Hand verwirft das Token", table.configs()[0]["channel"] == "can1")
        table.set_connect_error("can:slcan:COM7", "Port belegt")
        pump(app)
        check("Fehlersymbol an der richtigen Karte", table._rows[1]["status"].isVisible()
              and not table._rows[0]["status"].isVisible() and table._rows[1]["status"].toolTip() == "Port belegt")
        table.clear_connect_error("can:slcan:COM7")
        check("Fehlersymbol weg", not table._rows[1]["status"].isVisible())
        changes.clear()
        table._rows[1]["remove"].click()
        pump(app)
        check("Entfernen meldet changed, eine Karte weniger", changes and table.rowCount() == 1)
        table.add_row()
        check("Hinzufuegen: neue Karte, leerer Kanal zaehlt nicht", table.rowCount() == 2 and len(table.configs()) == 1)
        area.close()
        area.deleteLater()
        pump(app)
    touch_ui.sizer().set_active(False)
    ThemeManager.instance().set_touch(False)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
