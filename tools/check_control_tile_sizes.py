"""Kachelgroessen der Steuerung (1.0.4): J-Link und Generator mit einem Kanal nicht groesser als noetig.

- J-Link: eine Rasterzelle (vorher zwei Zellen hoch, untere Haelfte leer); Knoepfe in der Kopfzeile
- Generator im UNI-T (ein Kanal): eine Spalte, zwei Zellen hoch; Modulation und Last unter den Feldern des
  Kanals vor "Ausgang", ohne eigenen Rahmen; Beschriftungen gleich breit
- Generator mit zwei Kanaelen (JDS2915): unveraendert 2 x 2, Modulation wieder daneben, Titel "Kanal 1"
- Steuerung im Simulationsmodus: Spannweiten aus der Kachel (tile_size_key), J-Link und UNI-T-Generator eine
  Spalte breit
- nur unter Windows (echte Schrift): J-Link passt in die Hoehe einer Oszilloskop-Kachel, der Generator in eine
  Spalte und zwei Zellen

Nur Oberflaeche, keine Hardware.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform.startswith("win") else "offscreen")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def _show(widget, app) -> None:
    from PySide6.QtCore import Qt
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.show()
    app.processEvents()


def awg_state(**overrides) -> dict:
    from unit_mso_awg import driver as awg
    from unit_mso_awg.driver import UnitMsoAwg
    from unit_mso_awg.mock import SimulatedAwg
    state = awg.state_dict(UnitMsoAwg(SimulatedAwg()))
    state.update(overrides)
    return state


def jds_state() -> dict:
    return {"outputs": [False, False], "phase": 0.0,
            "channels": [{"waveform": 0, "frequency": 1000.0, "amplitude": 1.0, "offset": 0.0, "duty": 50.0}] * 2}


def section_tiles(app) -> None:
    from control_tab import TILE_SIZE_BY_KIND, FgControlGroup, JLinkControlGroup, PicoscopeControlGroup
    print("tiles -- Kacheln einzeln")
    check("J-Link: eine Zelle", TILE_SIZE_BY_KIND.get("jlink") == "small", str(TILE_SIZE_BY_KIND.get("jlink")))
    jlink = JLinkControlGroup("jlink:X", "J-Link")
    _show(jlink, app)
    check("J-Link: Knoepfe in der Kopfzeile", jlink.layout().itemAt(0).layout().indexOf(jlink._button_row) >= 0)

    fg = FgControlGroup("fg:X:AWG1", "Generator")
    sizes: list[str] = []
    fg.tile_size_changed.connect(sizes.append)
    _show(fg, app)
    check("vor der ersten Meldung: 2 x 2", fg.tile_size_key() == "big")
    fg.set_state(awg_state())
    app.processEvents()
    box = fg._boxes[1]
    check("UNI-T: eine Spalte (mid_v)", fg.tile_size_key() == "mid_v")
    check("UNI-T: Groesse gemeldet", sizes == ["fg:X:AWG1"], str(sizes))
    check("UNI-T: Modulation im Kanalkasten vor 'Ausgang'", box.layout().indexOf(fg._modulation) == 1,
          str(box.layout().indexOf(fg._modulation)))
    check("UNI-T: ohne Titel 'Kanal 1' und 'Modulation und Last'", box.title() == "" and fg._modulation.title() == "")
    labels = [box.form.labelForField(row) for row in box.rows] + fg._modulation.labels()
    widths = {label.minimumWidth() for label in labels}
    check("UNI-T: Beschriftungen gleich breit", len(widths) == 1 and widths != {0}, str(widths))
    check("UNI-T: Untertitel kurz", fg._subtitle.text().startswith("Generator ("), fg._subtitle.text())

    native = app.platformName() != "offscreen"
    if native:
        scope = PicoscopeControlGroup("scpiscope:X", "Scope", kind="scpiscope")
        _show(scope, app)
        cell_h = scope.sizeHint().height()
        cell_w = 300   # gemessen am PC: Kacheln 300 px breit (24.11.2026)
        hint = jlink.sizeHint()
        check(f"J-Link passt in eine Zelle ({hint.width()} x {hint.height()} <= {cell_w} x {cell_h})",
              hint.width() <= cell_w and hint.height() <= cell_h)
        hint = fg.sizeHint()
        check(f"UNI-T-Generator passt in 1 x 2 ({hint.width()} x {hint.height()} <= {cell_w} x {2 * cell_h + 12})",
              hint.width() <= cell_w and hint.height() <= 2 * cell_h + 12)
        scope.close()
    else:
        print("  --   Pixelmasse uebersprungen (offscreen, Schrift weicht ab)")

    fg.set_state(jds_state())
    app.processEvents()
    check("JDS danach: 2 x 2", fg.tile_size_key() == "big")
    check("JDS danach: Modulation wieder daneben", box.layout().indexOf(fg._modulation) < 0
          and fg._channels_row.indexOf(fg._modulation) >= 0)
    check("JDS danach: Titel 'Kanal 1'", box.title() == "Kanal 1", box.title())
    check("JDS danach: Beschriftungen frei", all(label.minimumWidth() == 0 for label in labels))
    fg.close()
    jlink.close()


def section_control_tab(app) -> None:
    print("control -- Steuerung im Simulationsmodus")
    import settings as settings_mod
    import device_registry as registry_mod
    tmp = pathlib.Path(tempfile.mkdtemp())
    settings_mod.SETTINGS_PATH = tmp / "settings.json"
    registry_mod.LABELS_PATH = tmp / "device_labels.json"
    settings_mod.SETTINGS_PATH.write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")
    import no_hardware
    no_hardware.block()
    import device_worker as dw
    from main_window import MainWindow
    window = MainWindow()
    try:
        _show(window, app)
        tab = window.control_tab
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not (
                dw.SIM_AWG_ID in tab._sections and tab._tile_span(dw.SIM_AWG_ID) == (1, 2)):
            app.processEvents()
            time.sleep(0.05)
        check("UNI-T-Generator: 1 x 2", tab._tile_span(dw.SIM_AWG_ID) == (1, 2), str(tab._tile_span(dw.SIM_AWG_ID)))
        check("JDS-Generator: 2 x 2", tab._tile_span(dw.SIM_FG_ID) == (2, 2), str(tab._tile_span(dw.SIM_FG_ID)))
        jlink_id = next((d for d, k in tab._section_kind.items() if k == "jlink"), None)
        check("J-Link: 1 x 1", jlink_id is not None and tab._tile_span(jlink_id) == (1, 1), str(jlink_id))
        app.processEvents()
        awg = tab._sections[dw.SIM_AWG_ID]
        check("UNI-T-Generator eine Spalte breit", awg.minimumWidth() == tab._cell_width,
              f"{awg.minimumWidth()} / {tab._cell_width}")
        # 1.0.5: die Groessenmeldung kam, als die Kachel noch zwei Kanaele breit war -- die Zelle (waechst nur)
        # wurde dadurch ~580 px breit, in die Steuerung passten nur noch ein bis zwei Spalten
        from control_tab import cell_size_ratchet
        need = cell_size_ratchet(0, [(tab._sections[d].sizeHint().width(), tab._tile_span(d)[0])
                                     for d in tab._placed_ids()])
        check("Rasterzelle nicht breiter als die Kacheln brauchen", tab._cell_width <= need,
              f"Zelle {tab._cell_width} px, noetig {need} px")
    finally:
        window.close()
        app.processEvents()


def main() -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from theme import ThemeManager
    ThemeManager.instance().apply(True)
    sections = sys.argv[1:] or ["tiles", "control"]
    if "tiles" in sections:
        section_tiles(app)
    if "control" in sections:
        section_control_tab(app)
    print()
    if FAILURES:
        print("FEHLGESCHLAGEN:", FAILURES)
        return 1
    print("Alles ok. (nur Oberfläche, keine Hardware)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
