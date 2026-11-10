"""Prueft das Zahlen- und Hex-Tastenfeld (lab_gui/touch_keypad.py, Pi-Plan Phase 4 Schritt 2, PI-6 a):

- Eingabe: erster Tastendruck ersetzt den alten Wert, Komma, Nachkommastellen begrenzt, ±, Loeschen, ⌫
- Bereich: ausserhalb -> "Setzen" gesperrt; Hex-Werte und Nutzdaten (bis 8 Bytes)
- Zahlenfeld: im Touch-Profil oeffnet ein Tipp auf den Wert das Tastenfeld (ohne Touch nicht);
  "Setzen" uebernimmt den Wert und loest den Uebernehmen-Knopf der Zeile aus (kein Haekchen)
- CAN-ID (Basis 16) mit Hex-Tasten, Nutzdaten-Textfeld mit Hex-Tastenfeld

Offscreen, keine Hardware.  Aufruf:  python tools/check_touch_keypad.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QPushButton  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def keypad_open(app):
    from touch_keypad import KeypadDialog
    return next((w for w in app.topLevelWidgets() if isinstance(w, KeypadDialog) and w.isVisible()), None)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from theme import ThemeManager
    from touch_keypad import KeypadDialog
    import touch_keypad as tk
    from control_tab import _row
    from step_spinbox import SteppedDoubleSpinBox, SteppedSpinBox

    print("Eingabe")
    d = KeypadDialog("Spannung", "13,5", minimum=1.0, maximum=60.0, decimals=1, unit=" V")
    check("Startwert gilt", d.number() == 13.5 and d._set_button.isEnabled())
    d._press("2")
    check("erster Tastendruck ersetzt", d.text() == "2")
    for key in ",", "5", "7":
        d._press(key)
    check("nur so viele Nachkommastellen wie das Feld", d.text() == "2,5" and d.number() == 2.5, d.text())
    d._clear()
    for key in "7", "5":
        d._press(key)
    check("75 V > 60 V: gesperrt", d.number() == 75 and not d._set_button.isEnabled())
    d._backspace()
    check("⌫: 7 V wieder gueltig", d.number() == 7 and d._set_button.isEnabled())
    d._clear()
    check("leer: gesperrt", not d._set_button.isEnabled())
    neg = KeypadDialog("Offset", "1,00", minimum=-10.0, maximum=10.0, decimals=2)
    neg._press("3")
    neg._press("±")
    check("±-Taste bei negativem Bereich", neg.number() == -3.0 and neg._set_button.isEnabled())
    plain = KeypadDialog("Spannung", "1,0", minimum=1.0, maximum=60.0, decimals=1)
    labels = {b.text() for b in plain.findChildren(QPushButton)}
    check("ohne negativen Bereich keine ±-Taste, mit Nachkommastellen ein Komma", "±" not in labels and "," in labels)
    ints = KeypadDialog("Zyklen", "5", minimum=1, maximum=100, decimals=0)
    check("Ganzzahl-Feld ohne Komma", "," not in {b.text() for b in ints.findChildren(QPushButton)})

    print("Hex")
    h = KeypadDialog("ID", "7FF", minimum=0, maximum=0x7FF, hex_mode=True)
    check("Hex-Wert", h.number() == 0x7FF and h._set_button.isEnabled())
    h._press("8")
    h._press("0")
    h._press("0")
    check("0x800 > 0x7FF: gesperrt", h.number() == 0x800 and not h._set_button.isEnabled())
    data = KeypadDialog("Daten", "01 A2", hex_mode=True, allow_space=True)
    check("Nutzdaten gueltig", data._set_button.isEnabled())
    data._clear()
    for key in "1", "2", "3":
        data._press(key)
    check("Byte mit drei Stellen: gesperrt", not data._set_button.isEnabled())

    print("Zahlenfeld im Touch-Profil")
    spin = SteppedDoubleSpinBox()
    spin.setRange(1.0, 60.0)
    spin.setDecimals(1)
    spin.setSuffix(" V")
    spin.setValue(12.0)
    applied: list[float] = []
    apply_button = QPushButton("✓")
    apply_button.clicked.connect(lambda: applied.append(spin.value()))
    row = _row(spin, apply_button)
    row.resize(400, 60)
    row.show()
    app.processEvents()
    check("Zeile merkt sich den Uebernehmen-Knopf", getattr(spin, tk.APPLY_PARTNER, None) is apply_button)
    center = QPoint(spin.width() // 3, spin.height() // 2)

    ThemeManager.instance().set_touch(False)
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=center)
    app.processEvents()
    check("ohne Touch: kein Tastenfeld", keypad_open(app) is None)

    ThemeManager.instance().set_touch(True)

    def type_and_set():
        dlg = keypad_open(app)
        if dlg is None:
            return
        for key in "2", "4":
            dlg._press(key)
        dlg._set_button.click()

    QTimer.singleShot(100, type_and_set)
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=center)
    for _ in range(30):
        app.processEvents()
        QTest.qWait(20)
    check("Touch: Tipp oeffnet das Tastenfeld, Setzen uebernimmt und sendet", spin.value() == 24.0 and applied == [24.0],
          f"{spin.value()}, {applied}")

    def cancel():
        dlg = keypad_open(app)
        if dlg is not None:
            dlg._press("9")
            dlg.reject()

    QTimer.singleShot(100, cancel)
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=center)
    for _ in range(30):
        app.processEvents()
        QTest.qWait(20)
    check("Abbruch aendert nichts", spin.value() == 24.0 and applied == [24.0])

    print("CAN-ID und Nutzdaten")
    id_spin = SteppedSpinBox()
    id_spin.setDisplayIntegerBase(16)
    id_spin.setPrefix("0x")
    id_spin.setRange(0, 0x7FF)
    extended = QCheckBox("Extended")
    id_row = _row(id_spin, extended)
    check("Haekchen ist kein Uebernehmen-Knopf", getattr(id_spin, tk.APPLY_PARTNER, None) is None)

    def hex_set():
        dlg = keypad_open(app)
        if dlg is not None:
            for key in "1", "2", "3":
                dlg._press(key)
            dlg._set_button.click()

    QTimer.singleShot(100, hex_set)
    tk.open_for_spinbox(id_spin)
    check("CAN-ID per Hex-Tasten, Extended unveraendert", id_spin.value() == 0x123 and not extended.isChecked())

    edit = QLineEdit()
    edit.resize(200, 40)
    tk.attach_hex_keypad(edit)
    edit.show()
    finished: list[str] = []
    edit.editingFinished.connect(lambda: finished.append(edit.text()))

    def data_set():
        dlg = keypad_open(app)
        if dlg is not None:
            for key in "0", "1", " ", "A", "2":
                dlg._press(key)
            dlg._set_button.click()

    QTimer.singleShot(100, data_set)
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
    for _ in range(30):
        app.processEvents()
        QTest.qWait(20)
    check("Nutzdaten per Hex-Tastenfeld", edit.text() == "01 A2" and finished == ["01 A2"], f"{edit.text()!r} {finished}")
    ThemeManager.instance().set_touch(False)
    id_row.deleteLater()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
