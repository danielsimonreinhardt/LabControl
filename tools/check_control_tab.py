"""Prueft die Aenderungen am Control-Reiter aus 0.20.0.

  1) Oszilloskop-Kachel: vorhanden, Status/Typ, Umbenennen und Panel-Farbe
     wirken auch auf die Dashboard-Kachel, "Trennen"/"Kurve" folgen dem
     ScopeService.
  2) Preset-Leiste: eigener Kasten mit Ueberschrift.
  3) Drag & Drop wie im Dashboard: gezogene Kachel behaelt ihre Zelle, die
     Vorschau sortiert live um, Loslassen uebernimmt genau die Vorschau und
     speichert sie, Abbrechen stellt den alten Stand her.

Das eigentliche Ziehen (QDrag.exec) laesst sich offscreen nicht mit der Maus
ausfuehren -- geprueft werden die Schritte, die Qt dabei aufruft
(_start_tile_drag ohne exec, DragMove -> _preview_tile_drag, Drop -> _drop_tile).

Startet die komplette App im Simulationsmodus (offscreen) mit umgelenkter
settings.json/device_labels.json; echte Geraete werden nicht gesucht.

Aufruf:  python tools/check_control_tab.py [--shots VERZEICHNIS]   (aus dem Repo-Stamm)
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
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILURES: list[str] = []
SCOPE = "picoscope:SIM"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    shots = None
    if "--shots" in sys.argv:
        shots = pathlib.Path(sys.argv[sys.argv.index("--shots") + 1])
        shots.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QPointF
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
    dw.picoscope_usb_present = lambda: False  # echtes Oszilloskop nicht anfassen

    from control_tab import PicoscopeControlGroup
    from main_window import MainWindow

    def wait_for(predicate, seconds=10.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    window = MainWindow(settings_mod.Settings())
    window.resize(1400, 900)
    window.show()
    tab = window.control_tab
    sections = tab._sections

    print("Ausgangslage")
    check("simulierte Geraete verbunden",
          wait_for(lambda: all(d in sections and not sections[d].isHidden()
                               for d in ("psu:SIM", "load:SIM", SCOPE))), str(list(sections)))
    wait_for(lambda: False, 1.0)

    def overlaps():
        placed = [s for s in sections.values() if s.isVisible()]
        return [(a.title(), b.title()) for i, a in enumerate(placed) for b in placed[i + 1:]
                if a.geometry().intersects(b.geometry())]
    check("keine Kacheln ueberlappen sich", not overlaps(), str(overlaps()))

    print("1) Oszilloskop-Kachel")
    scope = sections.get(SCOPE)
    check("Control-Kachel fuer das Oszilloskop", isinstance(scope, PicoscopeControlGroup))
    if not isinstance(scope, PicoscopeControlGroup):
        return 1
    check("Status vom Worker (Frei)", wait_for(lambda: scope._status_label.text() == "Frei", 10),
          scope._status_label.text())
    check("Typ und Seriennummer", "2204A" in scope._device_label.text(), scope._device_label.text())
    check("Trennen nur bei MCP-Verbindung sichtbar, Kurve ohne Erfassung aus",
          scope._release_button.isHidden() and not scope._plot_button.isEnabled())

    scope.rename_requested.emit("picoscope", SCOPE, "Scope am Eval Board")
    app.processEvents()
    dash_panel = window.dashboard._panels[SCOPE]
    check("Umbenennen: Control-Kachel", scope.title() == "Scope am Eval Board", scope.title())
    check("Umbenennen: Dashboard-Kachel", dash_panel.title() == "Scope am Eval Board", dash_panel.title())
    check("Umbenennen: gespeichert",
          json.loads(registry_mod.LABELS_PATH.read_text(encoding="utf-8")).get(SCOPE) == "Scope am Eval Board")

    window.settings_tab.panel_colors_toggled.emit(True)
    app.processEvents()
    check("Farbknopf sichtbar, sobald Panel-Farben an sind", scope._color_button.isVisible())
    scope.panel_color_requested.emit(SCOPE, "green")
    app.processEvents()
    check("Farbe: gespeichert", settings_mod.Settings().panel_color(SCOPE) == "green")
    check("Farbe: Control-Kachel", scope._color_key == "green")
    check("Farbe: Dashboard-Kachel", dash_panel._color_key == "green")

    window._on_scope_connection_changed(SCOPE, "mcp")
    app.processEvents()
    check("MCP verbunden: Status und Knopf Trennen",
          scope._status_label.text() == "Verbunden (MCP)" and not scope._release_button.isHidden())
    released = []
    window._scope_service.release_async = lambda d: released.append(d)  # nicht wirklich trennen
    scope.release_requested.disconnect()
    scope.release_requested.connect(window._scope_service.release_async)
    scope._release_button.click()
    check("Trennen erreicht den ScopeService", released == [SCOPE], str(released))
    window._on_scope_capture_added(SCOPE, "c-20260928-120000-01", "12:00:00 · A 3.30 Vss 1.000 kHz")
    app.processEvents()
    check("letzte Erfassung + Kurve aktiv",
          "3.30 Vss" in scope._capture_label.text() and scope._plot_button.isEnabled(),
          scope._capture_label.text())
    window._on_scope_connection_changed(SCOPE, "free")
    app.processEvents()
    check("nach dem Trennen: Frei, Trennen weg", scope._status_label.text() == "Frei" and scope._release_button.isHidden())
    check("Preset speichern uebergeht das Oszilloskop ohne Fehler", scope.capture_state() == {})

    print("2) Preset-Leiste")
    bar = tab._preset_bar
    check("eigener Kasten mit Hintergrund", bar._frame.objectName() == "presetBar"
          and "background-color" in bar._frame.styleSheet())
    check("Ueberschrift Presets", bar._heading_label.text() == "Presets")
    check("Kasten buendig mit dem Kachelraster",
          bar._frame.mapTo(tab, bar._frame.rect().topLeft()).x()
          == tab._grid_container.mapTo(tab, tab._grid_container.rect().topLeft()).x() + tab._grid_spacing,
          f"{bar._frame.mapTo(tab, bar._frame.rect().topLeft()).x()} vs "
          f"{tab._grid_container.mapTo(tab, tab._grid_container.rect().topLeft()).x() + tab._grid_spacing}")
    if shots is not None:
        window.tabs.setCurrentWidget(tab)
        wait_for(lambda: False, 0.5)
        window.grab().save(str(shots / "control_tab.png"))
        scope.grab().save(str(shots / "scope_tile.png"))

    print("3) Drag & Drop")
    emitted = []
    tab.tile_order_changed.connect(lambda order: emitted.append(order))
    order_before = tab._placed_order()
    check("mindestens drei platzierte Kacheln", len(order_before) >= 3, str(order_before))
    dragged, target = order_before[0], order_before[-1]
    section = sections[dragged]
    cell_before = tab._tile_rects(order_before)[dragged]

    # Wie _start_tile_drag, nur ohne das blockierende drag.exec().
    tab._set_keeps_space_when_hidden(section, True)
    tab._drag_hidden_section = section
    tab._drag_preview_order = None
    section.hide()
    tab._relayout_grid()
    app.processEvents()
    # Hoehe kann sich per Zellhoehen-Ratsche um wenige Pixel aendern -- es
    # zaehlt, dass die Kachel an ihrem Platz im Raster bleibt.
    grid_pos = tab._grid.getItemPosition(tab._grid.indexOf(section)) if tab._grid.indexOf(section) >= 0 else None
    check("gezogene Kachel behaelt ihre Zelle", tab._placed_order() == order_before
          and grid_pos is not None and grid_pos[:2] == (0, 0)
          and tab._tile_rects(order_before)[dragged].topLeft() == cell_before.topLeft(),
          f"{tab._placed_order()} {grid_pos} {tab._tile_rects(order_before)[dragged]} {cell_before}")
    check("Platzhalter 'kein Geraet' bleibt aus", tab._empty_tile.isHidden())

    tab._preview_tile_drag(dragged, QPointF(cell_before.center()))
    check("ueber der eigenen Luecke: nichts umsortiert", tab._placed_order() == order_before)

    rects = tab._tile_rects(tab._placed_order())
    right_half = QPointF(rects[target].right() - 5, rects[target].center().y())
    tab._preview_tile_drag(dragged, right_half)
    app.processEvents()
    preview = tab._placed_order()
    check("Vorschau: hinter die Ziel-Kachel einsortiert",
          preview[-1] == dragged and preview[:-1] == order_before[1:], str(preview))
    check("Vorschau noch nicht gespeichert", not emitted and tab._tile_order[0] == dragged)
    wait_for(lambda: False, 0.4)  # Animationen auslaufen lassen
    check("keine Ueberlappung in der Vorschau", not overlaps(), str(overlaps()))

    tab._drop_tile(dragged, QPointF(0, 0))  # Drop-Position egal: Vorschau gilt
    app.processEvents()
    check("Loslassen uebernimmt die Vorschau", tab._placed_order() == preview, str(tab._placed_order()))
    check("Kachel wieder sichtbar", not section.isHidden() and tab._drag_hidden_section is None)
    check("neue Reihenfolge gemeldet und gespeichert",
          emitted and emitted[-1] == tab._tile_order
          and settings_mod.Settings().control_tile_order == tab._tile_order)
    wait_for(lambda: False, 0.4)
    check("keine Ueberlappung nach dem Ablegen", not overlaps(), str(overlaps()))

    # Abbrechen: Vorschau verworfen, Stand wie vorher.
    order_before = tab._placed_order()
    dragged = order_before[0]
    section = sections[dragged]
    tab._set_keeps_space_when_hidden(section, True)
    tab._drag_hidden_section = section
    section.hide()
    rects = tab._tile_rects(order_before)
    tab._preview_tile_drag(dragged, QPointF(rects[order_before[-1]].right() - 5, rects[order_before[-1]].center().y()))
    tab._finish_tile_drag()
    tab._relayout_grid()
    app.processEvents()
    check("Abbrechen stellt die alte Reihenfolge her",
          tab._placed_order() == order_before and not section.isHidden())

    window.close()
    app.processEvents()
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
