"""Prueft das Touch-Profil (lab_gui/touch_ui.py, Pi-Plan Phase 4 Schritt 1, 0.32.0):

- Vorschlag nur bei kleinem Bildschirm (bis 1024 x 600) und echter Anzeige (nicht offscreen)
- Touch an: Navigation links statt Reiterleiste, Uebersicht (Dashboard) als eigener Reiter,
  Leiste oben mit Geraetezustand und ALLE AUS statt Statusleiste, groessere Schrift/Bedienelemente
- Touch aus: alles wieder wie vorher (Dashboard oben, Statusleiste, Reiter, Schrift)
- Abdocken des Dashboards ist im Touch-Profil gesperrt
- Einstellung wird gespeichert; "automatisch" steht im Text, solange nie gewaehlt

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_touch_ui.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-touch-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

from PySide6.QtCore import QSize, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    import touch_ui
    from main_window import MainWindow
    from settings import Settings
    from theme import ThemeManager

    print("Vorschlag nach Bildschirm")
    screen = lambda w, h: SimpleNamespace(size=lambda: QSize(w, h))  # noqa: E731
    real = touch_ui.QGuiApplication.platformName
    touch_ui.QGuiApplication.platformName = staticmethod(lambda: "wayland")
    try:
        check("1024 x 600 -> Touch", touch_ui.suggest_touch(screen(1024, 600)))
        check("800 x 480 -> Touch", touch_ui.suggest_touch(screen(800, 480)))
        check("1920 x 1080 -> kein Touch", not touch_ui.suggest_touch(screen(1920, 1080)))
        check("1280 x 600 -> kein Touch", not touch_ui.suggest_touch(screen(1280, 600)))
    finally:
        touch_ui.QGuiApplication.platformName = real
    check("offscreen (Tests/CI) -> kein Vorschlag", not touch_ui.suggest_touch(screen(800, 600)))

    print("Start ohne Touch (nie gewaehlt, offscreen)")
    settings = Settings()
    from i18n import Translator
    Translator.instance().set_language(settings.language)   # wie main.py (laedt auch die Qt-Uebersetzung)
    w = MainWindow(settings)
    w.resize(1024, 600)
    w.show()
    app.processEvents()
    base_font = app.font().pointSizeF()
    check("Statusleiste sichtbar, Leiste oben/Navigation verborgen",
          w.statusBar().isVisible() and not w._top_bar.isVisible() and not w._nav.isVisible())
    check("vier Reiter, Dashboard ueber den Reitern", w.tabs.count() == 4 and w.dashboard.parent() is w.centralWidget())
    check("Einstellung zeigt 'aus', nicht automatisch", not w.settings_tab._touch_checkbox.isChecked()
          and not w.settings_tab._touch_auto)

    print("Touch an (Haekchen in den Einstellungen)")
    w.settings_tab._touch_checkbox.setChecked(True)
    app.processEvents()
    check("gespeichert", json.loads((DATA / "settings.json").read_text(encoding="utf-8")).get("touch_mode") is True)
    check("Navigation links sichtbar, Reiterleiste verborgen", w._nav.isVisible() and not w.tabs.tabBar().isVisible())
    check("Uebersicht ist eigener Reiter und gewaehlt", w.tabs.count() == 5 and w.tabs.currentWidget() is w._overview_page
          and w._overview_page.isAncestorOf(w.dashboard))
    check("Leiste oben mit ALLE AUS, Statusleiste verborgen", w._top_bar.isVisible() and not w.statusBar().isVisible()
          and w._all_off_button.isVisible() and w._top_bar.isAncestorOf(w._all_off_button))
    check("Geraetezustand in der Leiste oben", w._top_bar.isAncestorOf(w._status_container) and w._status_container.isVisible())
    check("Fernsteuerungs-Hinweis bleibt verborgen", w._remote_status_label.isHidden())
    check("ALLE AUS mindestens 96 x 44 px", w._all_off_button.width() >= 96 and w._all_off_button.height() >= 44,
          f"{w._all_off_button.width()} x {w._all_off_button.height()}")
    check("Schrift groesser", abs(app.font().pointSizeF() - base_font * touch_ui.FONT_SCALE) < 0.01,
          f"{base_font} -> {app.font().pointSizeF()}")
    check("Touch-Regeln im Stylesheet", ThemeManager.instance().touch and "touchNav" in app.styleSheet())
    w._nav.selected.emit(w.timeline_tab)
    app.processEvents()
    check("Navigation wechselt den Reiter", w.tabs.currentWidget() is w.timeline_tab
          and w._nav._buttons[id(w.timeline_tab)][0].isChecked())
    w._detach_dashboard()
    check("Abdocken gesperrt", w._dashboard_window is None and w._overview_page.isAncestorOf(w.dashboard))

    print("Einheitliche Groessen (Durchsicht 10.11.2026)")
    w.tabs.setCurrentWidget(w.control_tab)
    for _ in range(5):
        app.processEvents()
    psu = w.control_tab._sections.get("psu:SIM")
    check("Simulations-Netzteil vorhanden", psu is not None)
    if psu is not None:
        label = psu._form.labelForField(psu._voltage_row)
        check("Zahlenfeld und ✓ 44 px hoch, ✓ quadratisch", psu._voltage_spin.height() == 44
              and psu._voltage_button.height() == 44 and psu._voltage_button.width() == 44,
              f"{psu._voltage_spin.height()} / {psu._voltage_button.size().toTuple()}")
        check("Beschriftung so hoch wie das Feld (mittig)", label.minimumHeight() == 44)
        check("Ausgang EIN/AUS und Handbedienung 44 px", psu._output_on_button.height() == 44
              and psu._manual_button.height() == 44)
    from control_tab import _PresetSlotButton
    slots = w.control_tab.findChildren(_PresetSlotButton)
    check("Presets: '⋯'-Knopf statt kleiner Eck-Knoepfe", slots and all(s._more_button.isVisible()
          and not s._save_button.isVisible() for s in slots))
    from PySide6.QtWidgets import QLabel
    dash_labels = [lb for lb in w.dashboard.findChildren(QLabel) if lb.text().startswith("Spannung")]
    check("Dashboard-Zeilen nicht gestreckt", dash_labels and all(lb.minimumHeight() == 0 for lb in dash_labels))
    hint = w.minimumSizeHint()
    check("Fenster passt auf 1024 x 600", hint.width() <= 1024 and hint.height() <= 600, f"{hint.width()} x {hint.height()}")

    print("Wischen, Anordnen, Beschriftungen (Schritt 4.4)")
    import touch_ui
    swipe = lambda area: bool(getattr(area.viewport(), touch_ui.SWIPE_ATTR, False))  # noqa: E731
    ct_area, dash_area = w.control_tab.scroll_area(), w.dashboard.scroll_area()
    check("Wischen in Steuerung, Dashboard, Uebersicht", swipe(ct_area) and swipe(dash_area) and swipe(w._overview_page))
    check("Kacheln ziehen gesperrt", not w.control_tab._drag_allowed and not w.dashboard._drag_allowed)
    w._top_bar._arrange_button.setChecked(True)
    app.processEvents()
    check("Anordnen: Ziehen erlaubt, kein Wischen dort", w.control_tab._drag_allowed and w.dashboard._drag_allowed
          and not swipe(ct_area) and not swipe(dash_area))
    check("Anordnen-Knopf heisst 'Fertig'", w._top_bar._arrange_button.text() == "Fertig")
    w._top_bar._arrange_button.setChecked(False)
    app.processEvents()
    check("Anordnen beendet: wieder Wischen", swipe(ct_area) and not w.control_tab._drag_allowed)
    w.tabs.setCurrentWidget(w.timeline_tab)
    for _ in range(5):
        app.processEvents()
    pause = w.timeline_tab._pause_button
    check("Symbol-Knoepfe mit Text (Pause, Diagramm)", pause.text() == "Pause"
          and w.timeline_tab._add_chart_button.text() == "Diagramm" and pause.width() > 44,
          f"{pause.text()!r} {pause.width()}")
    pause.setChecked(True)
    check("Text folgt dem Zustand (Weiter)", pause.text() == "Weiter")
    pause.setChecked(False)
    can = w.control_tab._sections.get("can:mock:SIM")
    check("CAN: Senden/Leeren beschriftet", can is not None and can._send_button.text() == "Senden"
          and can._clear_button.text() == "Leeren")
    if psu is not None:
        from PySide6.QtTest import QTest
        w.tabs.setCurrentWidget(w.control_tab)
        w.control_tab.scroll_area().ensureWidgetVisible(psu._manual_button)
        for _ in range(5):
            app.processEvents()
        clicks: list[int] = []
        psu._output_off_button.clicked.connect(lambda: clicks.append(1))
        QTest.mouseClick(psu._output_off_button, Qt.MouseButton.LeftButton)
        for _ in range(20):
            app.processEvents()
            QTest.qWait(15)
        check("Klick in wischbarer Flaeche kommt an", clicks == [1], str(clicks))

        # Echter Fingertipp (ueber das Fenster) auf den Wert eines Zahlenfelds in der wischbaren
        # Steuerung: muss das Tastenfeld oeffnen (am Pi ging es nicht, 11.11.2026)
        from PySide6.QtCore import QPoint
        from touch_keypad import KeypadDialog
        spin = psu._voltage_spin
        w.control_tab.scroll_area().ensureWidgetVisible(spin)
        for _ in range(5):
            app.processEvents()
        touch_device = QTest.createTouchDevice()
        window = w.windowHandle()
        point = spin.mapTo(w, QPoint(spin.width() // 2, spin.height() // 2))
        opened: list[bool] = []

        def close_keypad() -> None:
            dlg = next((d for d in app.topLevelWidgets() if isinstance(d, KeypadDialog) and d.isVisible()), None)
            opened.append(dlg is not None)
            if dlg is not None:
                dlg.reject()
        QTimer.singleShot(400, close_keypad)
        QTest.touchEvent(window, touch_device).press(0, point, window).commit()
        QTest.qWait(30)
        QTest.touchEvent(window, touch_device).release(0, point, window).commit()
        for _ in range(40):
            app.processEvents()
            QTest.qWait(15)
        check("Fingertipp auf ein Zahlenfeld in der Steuerung oeffnet das Tastenfeld", opened == [True], str(opened))

    print("Uebersicht mehrzeilig, Batterie-Simulator 2 x 7, Zeitfenster-Tasten (Schritt 4.5)")
    w.tabs.setCurrentWidget(w._overview_page)
    for _ in range(10):
        app.processEvents()
    dash = w.dashboard
    rects = {d: p.geometry() for d, p in dash._panels.items() if p.isVisible()}
    viewport_w = dash.scroll_area().viewport().width()
    check("Uebersicht: keine Kachel rechts abgeschnitten (kein waagerechtes Scrollen)",
          rects and all(r.right() < viewport_w for r in rects.values())
          and dash.scroll_area().horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
          f"Viewport {viewport_w}, rechts max {max((r.right() for r in rects.values()), default=0)}")
    ids = list(rects)
    overlap = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:] if rects[a].intersects(rects[b])]
    check("Uebersicht: Kacheln ueberlappen nicht", not overlap, str(overlap))
    check("Uebersicht: mehr als zwei Zeilen", len({r.top() for r in rects.values()}) > 2,
          str(sorted({r.top() for r in rects.values()})))
    hil = dash._panels.get("hil:SIM")
    check("HIL-Kachel ganz zu sehen (Inhalt passt)", hil is not None and hil.height() >= hil.sizeHint().height(),
          f"{hil.height() if hil else '-'} / {hil.sizeHint().height() if hil else '-'}")
    check("Ansicht-Umschalter in Fingergroesse", dash._view_toggle_button.height() == 44)
    from control_tab import BattSimControlGroup
    bs = next(iter(w.control_tab.findChildren(BattSimControlGroup)), None)
    check("Batterie-Simulator vorhanden", bs is not None)
    if bs is not None:
        from PySide6.QtWidgets import QAbstractSpinBox
        pos1 = bs._grid.getItemPosition(bs._grid.indexOf(bs._cell_spins[0]))
        pos8 = bs._grid.getItemPosition(bs._grid.indexOf(bs._cell_spins[7]))
        check("Zellen 2 x 7: Zelle 8 rechts neben Zelle 1", pos8[0] == pos1[0] and pos8[1] > pos1[1], f"{pos1} {pos8}")
        check("Zellraster ohne eigenen Scrollbereich", bs._grid_scroll.isHidden() and bs._grid_host.parent() is bs)
        w.tabs.setCurrentWidget(w.control_tab)
        for _ in range(5):
            app.processEvents()
        from PySide6.QtWidgets import QLineEdit
        edit = bs._cell_spins[0].findChild(QLineEdit)
        # Qt 6.12 machte das Textfeld mit NoButtons + Stylesheet-padding 1 px breit (am Pi gesehen)
        check("Zellsollwert: Textfeld zeigt den Wert (mit -/+)", edit.width() >= 60
              and bs._cell_spins[0].buttonSymbols() == QAbstractSpinBox.ButtonSymbols.UpDownArrows,
              f"Textfeld {edit.width()} px")
        check("Batterie-Kachel: Raster passt in die Kachel", bs._grid_host.width() >= bs._grid_host.sizeHint().width(),
              f"{bs._grid_host.width()} / {bs._grid_host.sizeHint().width()}")
        bs.set_state({"remote": True, "output_on": False, "voltage_limit": 4.3,
                      "cells": [{"setpoint": 3.7, "enable": False, "voltage": 0.0, "enabled": False}] * 14})
        check("ausgeschaltete Zelle: kurz und grau statt '(aus)'", bs._cell_actuals[0].text() == "0.000 V"
              and bs._cell_actuals[0].property("mutedColor"), bs._cell_actuals[0].text())
    tl = w.timeline_tab
    check("Zeitfenster als Tasten, Auswahlliste verborgen",
          not tl._window_buttons_host.isHidden() and tl._window_combo.isHidden())
    tl._window_buttons.button(3).click()
    check("Taste setzt das Zeitfenster (5 min)", tl._window_combo.currentIndex() == 3 and tl._window_s == 300.0,
          f"{tl._window_combo.currentIndex()} {tl._window_s}")

    print("Dialoge passen auf 1024 x 600 (Schritt 4.6)")
    from help_dialog import HelpDialog
    from PySide6.QtWidgets import QDialogButtonBox
    from timeline_tab import _SignalPickerDialog, _YAxisDialog
    help_dialog = HelpDialog(w)
    help_dialog.show()
    app.processEvents()
    check("Benutzerhandbuch passt (war 820 x 720)", help_dialog.width() <= 1024
          and help_dialog.height() <= 600 - touch_ui.DIALOG_DECORATION_H, str(help_dialog.size().toTuple()))
    close_text = help_dialog.findChild(QDialogButtonBox).buttons()[0].text().replace("&", "")
    check("Qt-Standardknoepfe deutsch (Qt-Uebersetzung geladen)", close_text == "Schließen", close_text)
    help_dialog.close()

    def cancel_text() -> str:
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        return box.buttons()[0].text().replace("&", "")
    Translator.instance().set_language("en")
    english = cancel_text()
    Translator.instance().set_language("de")
    check("Sprachwechsel nimmt Qt-Texte mit (en: Cancel, de: Abbrechen)",
          english == "Cancel" and cancel_text() == "Abbrechen", f"{english} / {cancel_text()}")
    yaxis =_YAxisDialog("auto", (0.0, 10.0), (0.0, 1.0), True, w)
    yaxis.show()
    for _ in range(3):
        app.processEvents()
    check("Y-Achse: Zahlenfelder mit -/+ Tasten (breit, 44 px)", yaxis._left_min.width() >= 150
          and yaxis._left_min.height() == 44, str(yaxis._left_min.size().toTuple()))
    yaxis.close()
    signals = tl._available_signals()
    picker = _SignalPickerDialog(signals, w)
    picker.show()
    for _ in range(3):
        app.processEvents()
    lst = picker._list
    headers = [lst.item(i) for i in range(lst.count()) if lst.item(i).data(Qt.ItemDataRole.UserRole) is None]
    check("Signal-Auswahl: Liste statt Menue, nach Geraet gegliedert, passt",
          len(signals) > 20 and headers and picker.height() <= 600 - touch_ui.DIALOG_DECORATION_H,
          f"{len(signals)} Signale, {len(headers)} Geraete, Hoehe {picker.height()}")
    check("Signal-Auswahl: wischbar", bool(getattr(lst.viewport(), touch_ui.SWIPE_ATTR, False)))
    first = next(lst.item(i) for i in range(lst.count()) if lst.item(i).data(Qt.ItemDataRole.UserRole) is not None)
    lst.itemClicked.emit(headers[0])
    check("Geraete-Zeile waehlt nichts", picker.isVisible() and picker.selected is None)
    lst.itemClicked.emit(first)
    check("Tipp waehlt das Signal und schliesst", picker.selected is first.data(Qt.ItemDataRole.UserRole)
          and not picker.isVisible())

    from PySide6.QtWidgets import QFileDialog
    file_dialog = QFileDialog(w, "Als CSV exportieren…")
    file_dialog.show()
    app.processEvents()
    check("Datei-Dialog nutzt den Bildschirm, passt", file_dialog.height() == 600 - touch_ui.DIALOG_DECORATION_H
          and file_dialog.width() <= 1024, str(file_dialog.size().toTuple()))
    file_dialog.close()

    print("Vollbild und Ausschalten")
    w._toggle_fullscreen()
    app.processEvents()
    check("Vollbild-Knopf: an, Symbol folgt", w.isFullScreen() and w._top_bar._fullscreen)
    w._toggle_fullscreen()
    app.processEvents()
    check("Vollbild-Knopf: wieder aus", not w.isFullScreen() and not w._top_bar._fullscreen)
    import system_power
    check("Ein/Aus-Knopf nur mit logind (hier nicht)", system_power.available() == (not w._top_bar._power_button.isHidden()))
    calls: list[str] = []
    real_off, real_reboot = system_power.power_off, system_power.reboot
    system_power.power_off = lambda: calls.append("aus") or True
    system_power.reboot = lambda: calls.append("neu") or True

    def answer(text: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        box = next(b for b in app.topLevelWidgets() if isinstance(b, QMessageBox) and b.isVisible())
        next(b for b in box.buttons() if b.text() == text).click()

    try:
        for text, expected in (("Abbrechen", []), ("Neu starten", ["neu"]), ("Herunterfahren", ["neu", "aus"])):
            QTimer.singleShot(200, lambda t=text: answer(t))
            w._on_power_requested()
            check(f"Rueckfrage: {text}", calls == expected, str(calls))
    finally:
        system_power.power_off, system_power.reboot = real_off, real_reboot

    print("Touch aus")
    w.settings_tab._touch_checkbox.setChecked(False)
    app.processEvents()
    check("Statusleiste mit ALLE AUS zurueck", w.statusBar().isVisible() and w._all_off_button.isVisible()
          and w.statusBar().isAncestorOf(w._all_off_button) and not w._top_bar.isVisible())
    check("Dashboard wieder ueber den Reitern, vier Reiter, Reiterleiste da",
          w.dashboard.parent() is w.centralWidget() and w.tabs.count() == 4 and w.tabs.tabBar().isVisible()
          and not w._nav.isVisible() and w.dashboard.isVisible())
    check("Schrift wie vorher", abs(app.font().pointSizeF() - base_font) < 0.01)
    check("Stylesheet ohne Touch-Regeln", not ThemeManager.instance().touch and "touchNav" not in app.styleSheet())
    for _ in range(5):
        app.processEvents()
    if psu is not None:
        check("Groessen zurueck (Feld nicht mehr fest 44, Beschriftung ohne Mindesthoehe)",
              psu._voltage_spin.maximumHeight() > 44 and psu._form.labelForField(psu._voltage_row).minimumHeight() == 0,
              f"{psu._voltage_spin.maximumHeight()}")
    check("Presets wieder mit Eck-Knoepfen", all(not s._more_button.isVisible() for s in slots))
    check("ohne Touch: kein Wischen, Ziehen erlaubt", not swipe(ct_area) and w.control_tab._drag_allowed)
    check("ohne Touch: Symbol-Knoepfe wieder ohne Text", pause.text() == "" and (can is None or can._send_button.text() == ""))
    check("ohne Touch: Dashboard wieder zweizeilig mit Titel", not w.dashboard._multi_row
          and w.dashboard.title() == "Dashboard"
          and len({p.geometry().top() for p in w.dashboard._panels.values() if p.isVisible()}) <= 2)
    if bs is not None:
        check("ohne Touch: Zellen wieder in einer Spalte im Scrollbereich, mit '(aus)'",
              bs._grid_scroll.widget() is bs._grid_host and not bs._grid_scroll.isHidden()
              and bs._grid.getItemPosition(bs._grid.indexOf(bs._cell_spins[7]))[1] == pos1[1]
              and bs._cell_actuals[0].text().endswith("(aus)"), bs._cell_actuals[0].text())
    check("ohne Touch: Zeitfenster-Auswahlliste zurueck, Wahl erhalten",
          tl._window_buttons_host.isHidden() and not tl._window_combo.isHidden() and tl._window_combo.currentIndex() == 3)
    w._closing = True
    w.close()
    app.processEvents()

    print("Neustart mit gespeichertem Touch")
    (DATA / "settings.json").write_text(json.dumps({"simulation_mode": True, "touch_mode": True}), encoding="utf-8")
    w2 = MainWindow(Settings())
    w2.show()
    app.processEvents()
    check("startet im Touch-Profil", w2._touch and w2.tabs.currentWidget() is w2._overview_page and w2._nav.isVisible())
    w2._closing = True
    w2.close()
    app.processEvents()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
