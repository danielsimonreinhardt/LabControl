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

from PySide6.QtCore import QSize, QTimer  # noqa: E402
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
          and w.dashboard.parent() is w._overview_page)
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
    check("Abdocken gesperrt", w._dashboard_window is None and w.dashboard.parent() is w._overview_page)

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
