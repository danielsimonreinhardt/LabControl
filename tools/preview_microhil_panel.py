"""Visuelle Vorschau von MicroHilPanel (lab_gui/microhil_panel.py) mit
Beispielwerten, ohne angeschlossene Hardware oder laufende App -- rendert
das Panel einmal je Theme (Light/Amber Dark) und speichert je einen
Screenshot. Reines Entwicklerwerkzeug, nicht Teil der Anwendung (analog zu
tools/generate_splash.py).

Aufruf: QT_QPA_PLATFORM=offscreen python tools/preview_microhil_panel.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # fuer microhil
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lab_gui"))  # fuer microhil_panel, theme, ...

from PySide6.QtWidgets import QApplication

from microhil.driver import AIN_COUNT, AOUT_COUNT, IN_COUNT, OUT_COUNT, PWR12_COUNT, RELAY_COUNT
from microhil_panel import MicroHilPanel
from theme import ThemeManager

OUT_DIR = Path(__file__).resolve().parent.parent / "build" / "preview"


def _populate(panel: MicroHilPanel) -> None:
    panel.update_inputs([True, True, False, False, True, False, False, True][:IN_COUNT])
    panel.update_outputs([False, False, True, False, False, False, False, False][:OUT_COUNT])
    panel.update_relays([True, False, True, False][:RELAY_COUNT])
    panel.update_analog_in([1250, 980, 0, 3300][:AIN_COUNT])
    panel.update_analog_out([1650, 0][:AOUT_COUNT])
    panel.update_pwr12([True, False][:PWR12_COUNT], [145, 0][:PWR12_COUNT])


def _snapshot(panel: MicroHilPanel, app: QApplication):
    """Rendert das Panel auf seine sizeHint()-Groesse -- angelehnt an
    DashboardWidget._relayout_panels() (sizeHint() + setFixedWidth()/
    resize()), NICHT panel.adjustSize() (auf einem elternlosen Top-Level-
    Widget mit addStretch() im Layout liefert das eine spuerbar zu
    grosse Groesse).

    HINWEIS: die in diesem Offscreen-Skript (manuell gepumpte
    processEvents(), kein echter app.exec()-Loop) gemessene Pixel-Hoehe
    der Kompaktansicht kann groesser ausfallen als in der echten,
    laufenden App (beobachtet: bis zu 80px statt 56px, ohne erkennbares
    festes Muster) -- ein Timing-Artefakt dieses Skripts, siehe Git-
    Historie. Positionen/Breiten sind davon nicht betroffen (per direkter
    Geometrie-Pruefung bestaetigt). Fuer eine verbindliche Pixel-Kontrolle
    daher die laufende App verwenden (python lab_gui/main.py), nicht
    dieses Skript."""
    panel.show()
    panel.resize(panel.sizeHint())
    app.processEvents()
    app.processEvents()
    return panel.grab()


def main() -> None:
    app = QApplication(sys.argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, dark in (("light", False), ("dark", True)):
        ThemeManager.instance().apply(dark)
        panel = MicroHilPanel("hil:preview", "microHIL - HIL 1")
        _populate(panel)
        pixmap = _snapshot(panel, app)
        out_path = OUT_DIR / f"microhil_panel_{name}.png"
        pixmap.save(str(out_path))
        print(f"{out_path} ({pixmap.width()}x{pixmap.height()})")

        panel.set_compact(True)
        compact_pixmap = _snapshot(panel, app)
        compact_path = OUT_DIR / f"microhil_panel_{name}_compact.png"
        compact_pixmap.save(str(compact_path))
        print(f"{compact_path} ({compact_pixmap.width()}x{compact_pixmap.height()})")
        panel.deleteLater()

    # Zusaetzlich: offline (getrenntes Geraet) im Light-Theme, siehe
    # MicroHilPanel.set_online -- separater Screenshot, damit die
    # Ausgrau-Darstellung ebenfalls visuell geprueft werden kann.
    ThemeManager.instance().apply(False)
    offline_panel = MicroHilPanel("hil:preview", "microHIL - HIL 1")
    _populate(offline_panel)
    offline_panel.set_online(False)
    offline_pixmap = _snapshot(offline_panel, app)
    out_path = OUT_DIR / "microhil_panel_offline.png"
    offline_pixmap.save(str(out_path))
    print(f"{out_path} ({offline_panel.width()}x{offline_panel.height()})")


if __name__ == "__main__":
    main()
