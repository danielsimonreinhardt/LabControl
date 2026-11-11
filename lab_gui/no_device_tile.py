"""Platzhalter-Kachel fuer Dashboard und Control-Tab ("Kein Geraet verbunden") und die gemeinsame
Darstellung getrennter Geraete-Kacheln (Dashboard, microHIL, Oszilloskop).

Seit 0.38.3 (GUI-Review UX-2, 11.11.2026) aus den Theme-Farben statt fest grau: das feste #9e9e9e war
im dunklen Theme das hellste Element am Bildschirm und zog den Blick auf das, was gerade NICHT
arbeitet. Getrennt heisst jetzt: zurueckgenommene Flaeche (surface_alt), gestrichelter Rahmen,
gedaempfte Schrift und oben rechts Symbol + "getrennt" (OfflineBadge) -- erkennbar an Form und Wort,
nicht an grellem Grau.
"""
from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from i18n import Translator, tr
from theme import METRICS, Palette, ThemeManager, no_own_background
from theme import current as current_palette

OFFLINE_ICON_NAME = "mdi.close-network-outline"
OFFLINE_ICON_SIZE = 18
OFFLINE_BADGE_MARGIN = 5


def offline_group_qss(palette: Palette) -> str:
    """QGroupBox-Regel fuer eine Kachel ohne aktives Geraet (getrennt oder "kein Geraet")."""
    return (f"QGroupBox {{ background-color: {palette.surface_alt}; "
            f"border: 1px dashed {palette.border_strong}; border-radius: {METRICS.radius_md}px; }}")


class OfflineBadge(QWidget):
    """Symbol + "getrennt" oben rechts in einer getrennten Kachel (Kind-Widget mit absoluter Position,
    siehe place()). Farben aus dem Theme, folgt Theme- und Sprachwechsel selbst."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        no_own_background(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._icon = no_own_background(QLabel())
        self._icon.setFixedSize(OFFLINE_ICON_SIZE, OFFLINE_ICON_SIZE)
        self._text = no_own_background(QLabel())
        layout.addWidget(self._icon)
        layout.addWidget(self._text)
        ThemeManager.instance().changed.connect(self._restyle)
        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()
        self._restyle(current_palette())
        self.hide()

    def _retranslate(self, *_args) -> None:
        self._text.setText(tr("getrennt"))
        self.setToolTip(tr("Verbindung getrennt"))
        self.adjustSize()
        self.place()

    def _restyle(self, palette: Palette) -> None:
        self._icon.setPixmap(qta.icon(OFFLINE_ICON_NAME, color=palette.text_muted).pixmap(OFFLINE_ICON_SIZE, OFFLINE_ICON_SIZE))
        self._text.setStyleSheet(f"color: {palette.text_muted}; background: transparent; font-weight: 600;")
        self.adjustSize()

    def place(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.move(parent.width() - self.width() - OFFLINE_BADGE_MARGIN, OFFLINE_BADGE_MARGIN)
            self.raise_()


class NoDeviceTile(QGroupBox):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(220, 100)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        ThemeManager.instance().changed.connect(self._restyle)
        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()
        self._restyle(current_palette())

    def _restyle(self, palette: Palette) -> None:
        self.setStyleSheet(offline_group_qss(palette))
        self._label.setStyleSheet(f"color: {palette.text_muted}; background: transparent; font-weight: normal;")

    def _retranslate(self) -> None:
        self._label.setText(tr("Kein Gerät verbunden"))
