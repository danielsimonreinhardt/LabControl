"""Platzhalter-Kachel fuer Dashboard und Control-Tab: erscheint anstelle der
Geraete-Panels/-Sektionen, solange kein einziges Geraet (weder Last noch
Netzteil) jemals verbunden war.

Fest grau -- ausdruecklich NICHT aus theme.Palette abgeleitet und unabhaengig
von einer individuellen Panel-Farbe (panel_color.py): die Kachel steht fuer
"kein Geraet", sie soll deshalb weder wie ein normales (hell/dunkel je nach
Theme gefaerbtes) Geraete-Panel noch wie ein farblich markiertes wirken,
sondern sich davon bewusst abheben. Dieselben Farben markieren in
dashboard._DevicePanel ein einzelnes, inzwischen getrenntes Geraete-Panel
("ausgegraut", siehe dort) -- fuer denselben "kein aktives Geraet"-Zustand
soll beides gleich aussehen.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout

from i18n import Translator, tr

# Fest verdrahtete Farben (siehe Docstring oben) statt theme.Palette-Werten --
# oeffentlich, damit dashboard._DevicePanel dieselben Werte fuer sein eigenes
# "getrennt"-Ausgrauen wiederverwenden kann.
OFFLINE_BACKGROUND = "#9e9e9e"
OFFLINE_TEXT = "#212121"
OFFLINE_BORDER = "#6e6e6e"


class NoDeviceTile(QGroupBox):
    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QGroupBox {{ background-color: {OFFLINE_BACKGROUND}; "
            f"border: 1px solid {OFFLINE_BORDER}; border-radius: 6px; }}"
        )
        self.setMinimumSize(220, 100)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(f"color: {OFFLINE_TEXT}; background: transparent; font-weight: normal;")
        layout.addWidget(self._label)

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._label.setText(tr("Kein Gerät verbunden"))
