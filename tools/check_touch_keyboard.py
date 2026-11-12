"""Prueft die Texttastatur im Touch-Profil (lab_gui/touch_keyboard.py, PI-37 a, 0.51.0).

Am Pi (Kiosk) oeffnete ein Tipp auf ein Textfeld keine Tastatur (12.11.2026).
- Touch an: Tipp auf ein Textfeld oeffnet die Texttastatur; Tasten, Umschalt (ein Buchstabe),
  Zeichenebene, Ruecktaste, Loeschen; "Übernehmen" setzt den Text und meldet textEdited + editingFinished
- "Abbruch" laesst das Feld unveraendert
- Validator: ungueltiger Text sperrt "Übernehmen"; maxLength begrenzt
- ausgenommen: Zahlenfeld-Innenleben, Hex-Feld (eigenes Tastenfeld), schreibgeschuetzt, gesperrt
- Bildschirmtastatur des Systems wird fuer Textfelder nicht mehr angefordert
- Touch aus: Tipp oeffnet nichts, normales Textfeld

Offscreen, keine Hardware.  Aufruf:  python tools/check_touch_keyboard.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRegularExpression, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QInputMethodQueryEvent, QRegularExpressionValidator  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,  # noqa: E402
                               QWidget)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def wants_keyboard(widget) -> bool:
    event = QInputMethodQueryEvent(Qt.InputMethodQuery.ImEnabled)
    QApplication.sendEvent(widget, event)
    return bool(event.value(Qt.InputMethodQuery.ImEnabled))


def pump(app, n: int = 10) -> None:
    for _ in range(n):
        app.processEvents()
        QTest.qWait(5)


def key(dialog, text: str):
    from PySide6.QtWidgets import QPushButton
    return next((b for b in dialog.findChildren(QPushButton) if b.text() == text and b.isVisible()), None)


def tap_and_run(app, edit: QLineEdit, actions) -> object:
    """Tippt auf das Feld; actions(dialog) laeuft, sobald die Tastatur offen ist. Liefert den Dialog (oder None)."""
    from touch_keyboard import TextKeyboardDialog
    seen: list = []

    def act() -> None:
        dlg = next((d for d in app.topLevelWidgets() if isinstance(d, TextKeyboardDialog) and d.isVisible()), None)
        seen.append(dlg)
        if dlg is not None:
            actions(dlg)
        else:
            for d in app.topLevelWidgets():
                if isinstance(d, TextKeyboardDialog):
                    d.reject()
    QTimer.singleShot(150, act)
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
    pump(app, 40)
    return seen[0] if seen else None


def main() -> int:
    app = QApplication.instance() or QApplication([])
    import touch_ui
    from theme import ThemeManager
    from touch_keypad import attach_hex_keypad

    form = QWidget()
    layout = QFormLayout(form)
    addr = QLineEdit("192.168.178.95:8420")
    layout.addRow(QLabel("Adresse"), addr)
    ip = QLineEdit()
    ip.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{1,3}(\.\d{1,3}){3}")))
    layout.addRow(QLabel("IP"), ip)
    short = QLineEdit()
    short.setMaxLength(3)
    layout.addRow(QLabel("Kurz"), short)
    hex_edit = QLineEdit()
    attach_hex_keypad(hex_edit)
    layout.addRow(QLabel("Hex"), hex_edit)
    ro = QLineEdit("fest")
    ro.setReadOnly(True)
    layout.addRow(QLabel("Nur lesen"), ro)
    spin = QDoubleSpinBox()
    layout.addRow(QLabel("Zahl"), spin)
    form.resize(500, 400)
    form.show()
    pump(app)

    events: list[str] = []
    addr.textEdited.connect(lambda t: events.append(f"edited:{t}"))
    addr.editingFinished.connect(lambda: events.append("finished"))

    print("Touch aus")
    dlg = tap_and_run(app, addr, lambda d: d.reject())
    check("Tipp oeffnet keine Tastatur", dlg is None)
    check("normales Textfeld fragt die System-Tastatur an", wants_keyboard(addr))

    print("Touch an")
    ThemeManager.instance().set_touch(True)
    touch_ui.sizer().set_active(True)
    pump(app)
    check("Textfeld fragt die System-Tastatur nicht mehr an", not wants_keyboard(addr))

    def type_address(d) -> None:
        check("Kopfzeile = Feldname", d.windowTitle() == "Adresse", d.windowTitle())
        key(d, "Löschen").click()
        for ch in "192.168.178.81:8420":
            b = key(d, ch)
            if b is None:   # ":" liegt fest unten, Ziffern oben -- alles muss es geben
                events.append(f"FEHLT {ch}")
                return
            b.click()
        key(d, "Übernehmen").click()
    dlg = tap_and_run(app, addr, type_address)
    check("Tipp oeffnet die Texttastatur", dlg is not None)
    check("Text uebernommen", addr.text() == "192.168.178.81:8420", addr.text())
    check("meldet textEdited und editingFinished", events == ["edited:192.168.178.81:8420", "finished"], str(events))

    def shift_and_symbols(d) -> None:
        key(d, "Löschen").click()
        key(d, "⇧").click()
        key(d, "l").click() if key(d, "l") else key(d, "L").click()
        key(d, "a").click()
        key(d, "b").click()
        key(d, "#+=").click()
        key(d, "!").click()
        key(d, "#+=").click()
        key(d, "ü").click()
        key(d, "⌫").click()
        key(d, "Leerzeichen").click()
        key(d, "1").click()
        key(d, "Übernehmen").click()
    tap_and_run(app, addr, shift_and_symbols)
    check("Umschalt fuer einen Buchstaben, Zeichenebene, Ruecktaste, Leerzeichen", addr.text() == "Lab! 1", addr.text())

    def cancel(d) -> None:
        key(d, "Löschen").click()
        key(d, "x").click()
        key(d, "Abbruch").click()
    tap_and_run(app, addr, cancel)
    check("Abbruch laesst das Feld unveraendert", addr.text() == "Lab! 1", addr.text())

    def invalid_ip(d) -> None:
        for ch in "192.168":
            key(d, ch).click()
        enabled_partial = key(d, "Übernehmen").isEnabled()
        for ch in ".1.2":
            key(d, ch).click()
        events.append(f"ip:{enabled_partial}:{key(d, 'Übernehmen').isEnabled()}")
        key(d, "Übernehmen").click()
    events.clear()
    tap_and_run(app, ip, invalid_ip)
    check("Validator: unvollstaendige IP sperrt Übernehmen, gueltige gibt frei", events == ["ip:False:True"], str(events))
    check("gueltige IP uebernommen", ip.text() == "192.168.1.2", ip.text())

    def too_long(d) -> None:
        for ch in "abcdef":
            key(d, ch).click()
        key(d, "Übernehmen").click()
    tap_and_run(app, short, too_long)
    check("maxLength begrenzt die Eingabe", short.text() == "abc", short.text())

    from touch_keypad import KeypadDialog
    seen: list = []

    def grab_hex() -> None:
        from touch_keyboard import TextKeyboardDialog
        for d in app.topLevelWidgets():
            if d.isVisible() and isinstance(d, (KeypadDialog, TextKeyboardDialog)):
                seen.append(type(d).__name__)
                d.reject()
    QTimer.singleShot(150, grab_hex)
    QTest.mouseClick(hex_edit, Qt.MouseButton.LeftButton)
    pump(app, 40)
    check("Hex-Feld bekommt weiter das Hex-Tastenfeld", seen == ["KeypadDialog"], str(seen))
    check("schreibgeschuetztes Feld: keine Tastatur", tap_and_run(app, ro, lambda d: d.reject()) is None)
    check("Zahlenfeld-Innenleben: keine Texttastatur", tap_and_run(app, spin.lineEdit(), lambda d: d.reject()) is None)

    touch_ui.sizer().set_active(False)
    ThemeManager.instance().set_touch(False)
    form.close()
    form.deleteLater()
    pump(app)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
