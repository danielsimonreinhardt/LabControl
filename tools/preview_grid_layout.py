"""Visuelle Vorschau der neuen Kachel-Raster in Control-Tab und Dashboard-Tab
(FEATURES.md Punkt 4/5) mit mehreren Mock-Geraeten, ohne angeschlossene
Hardware oder laufende App -- rendert je Tab einen Screenshot je Theme.
Reines Entwicklerwerkzeug, nicht Teil der Anwendung (analog zu
tools/preview_microhil_panel.py).

Aufruf: QT_QPA_PLATFORM=offscreen python tools/preview_grid_layout.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # fuer microhil
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab_gui"))

from PySide6.QtWidgets import QApplication

from control_tab import ControlTab
from dashboard import DashboardWidget
from presets import PresetStore
from theme import ThemeManager

OUT_DIR = Path(__file__).resolve().parent.parent / "build" / "preview"

# (kind, device_id, label) -- deckt alle vier Kachelgroessen im Control-Tab
# (small/small/mid_v/big) sowie beide Hoehenklassen im Dashboard (einfach/
# doppelt) ab, inkl. eines zweiten Small-Geraets, damit sich im Dashboard
# zwei einfache Kacheln uebereinander stapeln koennen (siehe FEATURES.md
# Punkt 5).
DEVICES = [
    ("psu", "psu:1", "Netzteil 1"),
    ("load", "load:1", "Last 1"),
    ("can", "can:1", "CAN 1"),
    ("hil", "hil:1", "microHIL 1"),
    ("picoscope", "picoscope:1", "PicoScope 1"),
]


def _build_control_tab() -> ControlTab:
    tab = ControlTab(PresetStore())
    for kind, device_id, label in DEVICES:
        tab.on_device_known(kind, device_id, label)
        tab._set_online(device_id, True)
    return tab


def _build_dashboard() -> DashboardWidget:
    dash = DashboardWidget()
    for kind, device_id, label in DEVICES:
        dash.on_device_known(kind, device_id, label)
        getattr(dash, f"set_{kind}_online")(device_id, True)
    return dash


def _snapshot(widget, app: QApplication, width: int):
    """Wie preview_microhil_panel.py::_snapshot: zeigt das Widget in einer
    festen Breite (Kiosk-Aufloesung 1024px, siehe uds-diag/labor-dashboard-
    Konvention) und liest danach die tatsaechlich benoetigte Hoehe
    (sizeHint) zurueck -- Raster-Umbruch/Packing haengt direkt von der
    verfuegbaren Breite ab (siehe ControlTab._relayout_grid), ein
    unskaliertes adjustSize() waere hier irrefuehrend."""
    widget.show()
    widget.resize(width, widget.sizeHint().height() or 200)
    for _ in range(10):
        app.processEvents()
    widget.resize(width, widget.sizeHint().height())
    for _ in range(5):
        app.processEvents()
    return widget.grab()


def _snapshot_scroll_content(tab: ControlTab, app: QApplication, width: int):
    """Zeigt `tab` selbst in fester Breite (loest damit ein echtes
    Resize-Event auf scroll_area.viewport() aus -- ControlTab._relayout_grid
    braucht dessen tatsaechliche Breite fuer den Spaltenumbruch), greift den
    Screenshot danach aber vom gescrollten Innenbereich (scroll_area.widget())
    statt von `tab` selbst: die ScrollArea hat (anders als DashboardWidget,
    siehe dessen explizites setFixedHeight in _relayout_panels) bewusst KEINE
    an den Inhalt angeglichene sizeHint (Control-Tab soll auf dem kleinen
    Kiosk-Display bei vielen Geraeten tatsaechlich scrollen) -- ein Grab von
    `tab` selbst waere an der Scrollbar abgeschnitten."""
    tab.show()
    tab.resize(width, 700)
    for _ in range(10):
        app.processEvents()
    content = tab._scroll_area.widget()
    content.resize(content.width(), content.sizeHint().height())
    for _ in range(5):
        app.processEvents()
    return content.grab()


def main() -> None:
    app = QApplication(sys.argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, dark in (("light", False), ("dark", True)):
        ThemeManager.instance().apply(dark)

        control_tab = _build_control_tab()
        pixmap = _snapshot_scroll_content(control_tab, app, 1024)
        out_path = OUT_DIR / f"grid_control_tab_{name}.png"
        pixmap.save(str(out_path))
        print(f"{out_path} ({pixmap.width()}x{pixmap.height()})")
        control_tab.deleteLater()

        dashboard = _build_dashboard()
        pixmap = _snapshot(dashboard, app, 1024)
        out_path = OUT_DIR / f"grid_dashboard_{name}.png"
        pixmap.save(str(out_path))
        print(f"{out_path} ({pixmap.width()}x{pixmap.height()})")
        dashboard.deleteLater()


if __name__ == "__main__":
    main()
