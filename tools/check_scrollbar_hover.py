"""Prueft Entscheidung PI-20 b (11.11.2026): am PC schmale Scrollleisten, beim Ueberfahren breit zum Greifen.

Die Leiste ist fest 12 px breit (der Inhalt daneben springt nicht), der Griff 6 px; beim Betreten setzt
theme.ScrollbarHover die Eigenschaft "hot", der Griff fuellt dann die Leiste. Im Touch-Profil bleibt die
6-px-Positionsanzeige (UX-8) und der Filter ist nicht installiert. Offscreen, ohne Geraete.

Aufruf:  python tools/check_scrollbar_hover.py   (aus dem Repo-Stamm)
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollBar  # noqa: E402

app = QApplication.instance() or QApplication([])
from theme import ThemeManager  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def handle_columns(bar: QScrollBar) -> list[int]:
    app.processEvents()
    img = bar.grab().toImage()
    color = QColor(ThemeManager.instance().palette.border_strong).name()
    y = 10   # Griff steht oben (Wert 0), unterhalb des oberen Rands
    return [x for x in range(img.width()) if img.pixelColor(x, y).name() == color]


manager = ThemeManager.instance()
manager.apply(dark=True)
bar = QScrollBar(Qt.Orientation.Vertical)
bar.setRange(0, 100)
bar.setPageStep(20)
bar.resize(bar.sizeHint().width(), 200)
bar.move(400, 300)   # nicht unter dem (virtuellen) Mauszeiger bei 0,0 -- sonst kommt ein echtes Enter
bar.show()
app.processEvents()

print("PC")
check("Leiste 12 px breit", bar.sizeHint().width() == 12, str(bar.sizeHint().width()))
cols = handle_columns(bar)
check("Griff schmal (6 px)", len(cols) == 6, str(cols))
QApplication.sendEvent(bar, QEvent(QEvent.Type.Enter))
cols_hot = handle_columns(bar)
check("beim Ueberfahren breit (10 px), Leiste bleibt 12 px",
      len(cols_hot) == 10 and bar.sizeHint().width() == 12, f"{cols_hot} / {bar.sizeHint().width()}")
QApplication.sendEvent(bar, QEvent(QEvent.Type.Leave))
check("beim Verlassen wieder schmal", len(handle_columns(bar)) == 6)

print("Touch-Profil")
manager.set_touch(True)
bar.resize(bar.sizeHint().width(), 200)
app.processEvents()
cols = handle_columns(bar)
check("Leiste 6 px, Griff fuellt sie (Positionsanzeige)", bar.sizeHint().width() == 6 and len(cols) >= 4,
      f"{bar.sizeHint().width()} / {cols}")
QApplication.sendEvent(bar, QEvent(QEvent.Type.Enter))
check("kein Hover-Filter im Touch-Profil", bar.property("hot") in (None, False), str(bar.property("hot")))
manager.set_touch(False)

bar.close()
bar.deleteLater()
app.processEvents()
print()
if FAILURES:
    print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
    sys.exit(1)
print("Alles ok. (offscreen, ohne Geraete)")
