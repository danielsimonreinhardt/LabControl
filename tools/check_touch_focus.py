"""Touch-Profil (Pi, 1.0.6): Knoepfe behalten nach dem Tippen keine Fokus- oder Hover-Farbe.

Nutzerhinweis 27.11.2026: nach dem Tippen blieb z.B. "Handbedienung" fokussiert (Auswahl-Toenung) und sah dadurch aus
wie eingeschaltet; ein Tipp hinterlaesst ausserdem den Hover-Zustand (kein Verlassen ohne Maus).

- Touch: ein fokussierter Knopf unter dem "Mauszeiger" sieht aus wie ein unberuehrter -- global, in einer
  eingefaerbten Kachel (apply_panel_tint), in einer Kachel-Zeile (_row_stylesheet), als OUT-Schalter und
  Preset-Knopf; :pressed bleibt als Rueckmeldung
- PC: Fokus und Hover bleiben sichtbar (Tastaturbedienung)

Nur Oberflaeche, keine Hardware.
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ["QT_QPA_PLATFORM"] = "offscreen"   # zeigt ein echtes, aktives Fenster -- nie auf dem Bildschirm

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QGroupBox, QLineEdit, QPushButton, QToolButton, QVBoxLayout, QWidget
    app = QApplication.instance() or QApplication([])
    import no_hardware
    no_hardware.block()
    import theme
    from theme import ThemeManager, form_control_qss
    from panel_color import apply_panel_tint
    import control_tab
    from touch_ui import shell_qss

    manager = ThemeManager.instance()
    manager.apply(True)

    def build():
        host = QWidget()   # ohne WA_DontShowOnScreen: das Fenster muss aktiv werden, sonst gibt es keinen Fokus
        layout = QVBoxLayout(host)
        # haelt den Fokus, solange kein Knopf "getippt" ist (sonst hat der erste Knopf ihn schon beim Anzeigen)
        host.parking = QLineEdit()
        layout.addWidget(host.parking)
        plain = QPushButton("Handbedienung")
        plain.setCheckable(True)
        tool = QToolButton()
        tool.setText("Werkzeug")
        tinted = QGroupBox("Netzteil")
        apply_panel_tint(tinted, "blue")
        tinted_button = QPushButton("EIN", tinted)
        QVBoxLayout(tinted).addWidget(tinted_button)
        row = QWidget()
        row.setStyleSheet(control_tab._row_stylesheet(theme.current()))
        row_button = QPushButton("Setzen", row)
        QVBoxLayout(row).addWidget(row_button)
        out = control_tab._digital_out_toggle(1)
        control_tab._style_digital_out_toggle(out, False, theme.current())
        for widget in (plain, tool, tinted, row, out):
            layout.addWidget(widget)
        host.resize(320, 360)
        host.show()
        host.activateWindow()
        host.parking.setFocus()
        app.processEvents()
        return host, {"global": plain, "QToolButton": tool, "Kachel eingefaerbt": tinted_button,
                      "Kachel-Zeile": row_button, "OUT-Schalter": out}

    def looks(button):
        return button.grab().toImage()

    def touched(button):
        """Zustand nach einem Tipp: Fokus und (ohne Verlassen-Ereignis) unter dem Zeiger."""
        button.setFocus(Qt.FocusReason.MouseFocusReason)
        button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
        if button.isCheckable():
            button.setChecked(True)
            button.setChecked(False)
        app.processEvents()
        return looks(button)

    def reset(button):
        button.window().parking.setFocus()
        button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
        app.processEvents()

    print("PC: Fokus/Hover sichtbar")
    manager.set_touch(False)
    host, buttons = build()
    for name, button in buttons.items():
        before = looks(button)
        after = touched(button)
        check(f"{name}: Fokus/Hover am PC sichtbar", before != after,
              f"Fokus {button.hasFocus()}, {before.pixelColor(3, 3).name()} -> {after.pixelColor(3, 3).name()}")
        reset(button)
    host.close()
    check("PC: QSS mit :focus und :hover", ":focus" in form_control_qss(theme.current(), False)
          and ":hover" in form_control_qss(theme.current(), False))
    check("PC: Navigation mit :hover", ":hover" in shell_qss(theme.current(), False))

    print("Touch: nach dem Tippen wie vorher")
    manager.set_touch(True)
    host, buttons = build()
    for name, button in buttons.items():
        before = looks(button)
        after = touched(button)
        check(f"{name}: nach dem Tippen unveraendert (Fokus {button.hasFocus()})", before == after and button.hasFocus(),
              f"{before.pixelColor(3, 3).name()} -> {after.pixelColor(3, 3).name()}")
        if name != "OUT-Schalter":   # eigener Rahmen-Stil, Rueckmeldung ueber die LED
            button.setDown(True)
            app.processEvents()
            check(f"{name}: beim Druecken hervorgehoben", looks(button) != before)
            button.setDown(False)
        reset(button)
    host.close()
    qss = app.styleSheet()
    check("Touch: App-QSS ohne :focus/:hover der Knoepfe",
          all(s not in qss for s in ("QPushButton:focus", "QPushButton:hover", "QToolButton:focus",
                                     "QToolButton:hover", "up-button:hover", "touchNav QToolButton:hover")))
    check("Touch: :pressed bleibt", "QPushButton:pressed" in qss and "QToolButton:pressed" in qss)
    check("Touch: Kachel-Stylesheet ohne :focus/:hover", ":focus" not in form_control_qss(theme.current())
          .split("QLineEdit")[0] and ":hover" not in form_control_qss(theme.current()))
    check("Touch: Eingabefelder behalten den Fokusrahmen", "QLineEdit:focus" in form_control_qss(theme.current()))

    preset = control_tab._PresetSlotButton()
    preset.apply_style(theme.current())
    sheet = preset._main_button.styleSheet()
    check("Touch: Preset-Knopf ohne :focus/:hover", ":focus" not in sheet and ":hover" not in sheet)

    print("Zurueck an den PC")
    manager.set_touch(False)
    check("PC: App-QSS wieder mit :focus/:hover", "QPushButton:focus" in app.styleSheet()
          and "QPushButton:hover" in app.styleSheet())

    print()
    if FAILURES:
        print("FEHLGESCHLAGEN:", FAILURES)
        return 1
    print("Alles ok. (nur Oberfläche, keine Hardware)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
