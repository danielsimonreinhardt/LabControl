"""IconButton: QPushButton mit Material-Design-Icon (qtawesome, mdi.*) statt
oder zusaetzlich zu Text.

QIcon-Pixmaps reagieren nicht von selbst auf QSS-Pseudozustaende -- deshalb
wird die Icon-Farbe hier manuell nachgezogen, exakt synchron zu den Farben,
die theme.stylesheet() bereits fuer QPushButton-Text definiert (pal.text im
Ruhezustand, pal.surface bei Hover, pal.text_muted wenn disabled). So wirken
Icon und Text/Rahmen wie aus einem Guss, auch bei Theme-Wechsel.
"""
from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QPushButton, QToolButton

from theme import Palette, ThemeManager
from theme import current as current_palette

ICON_SIZE = QSize(18, 18)
ICON_ONLY_SIZE = QSize(34, 30)
# Ein QPushButton mit gesetztem Menu (siehe setMenu-Override unten) zeichnet
# zusaetzlich einen kleinen Dropdown-Pfeil neben dem Icon -- bei der knappen
# ICON_ONLY_SIZE quetscht das den 18px-Icon sichtbar an den linken Rand
# (leicht abgeschnitten). Etwas breiter, damit beide nebeneinander Platz haben.
ICON_MENU_SIZE = QSize(46, 30)


class _IconColorMixin:
    """Icon-Einfaerbe-Logik (Hover/Disabled/Theme-Wechsel), geteilt zwischen
    IconButton (QPushButton) und SplitIconButton (QToolButton) -- beide sind
    QAbstractButton-Subklassen mit identischem Faerbeverhalten, nur die
    Popup-Mechanik (ganze Flaeche vs. getrennter Menue-Pfeil) unterscheidet
    sich zwischen ihnen."""

    _icon_name: str
    _hovered: bool
    _color_override: str | None = None  # nur von IconButton genutzt (set_color_override)

    def _init_icon_color(self, icon_name: str) -> None:
        self._icon_name = icon_name
        self._hovered = False
        self._apply_icon(current_palette())
        ThemeManager.instance().changed.connect(self._apply_icon)

    def set_icon(self, icon_name: str) -> None:
        self._icon_name = icon_name
        self._apply_icon(current_palette())

    def _apply_icon(self, pal: Palette) -> None:
        if not self.isEnabled():
            color = pal.text_muted
        elif self._hovered:
            color = pal.surface
        elif self._color_override:
            color = self._color_override
        else:
            color = pal.text
        self.setIcon(qta.icon(self._icon_name, color=color))

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 (Qt override)
        super().setEnabled(enabled)
        self._apply_icon(current_palette())

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._hovered = True
        self._apply_icon(current_palette())
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._hovered = False
        self._apply_icon(current_palette())
        super().leaveEvent(event)


class IconButton(_IconColorMixin, QPushButton):
    def __init__(self, icon_name: str, tooltip: str, text: str = "") -> None:
        super().__init__(text)
        self.setToolTip(tooltip)
        self.setIconSize(ICON_SIZE)
        if not text:
            self.setFixedSize(ICON_ONLY_SIZE)
        self._init_icon_color(icon_name)

    def setMenu(self, menu) -> None:  # noqa: N802 (Qt override)
        super().setMenu(menu)
        # Icon-only Buttons mit Menue brauchen Platz fuer den Dropdown-Pfeil
        # zusaetzlich zum Icon (siehe ICON_MENU_SIZE) -- Text-Buttons wachsen
        # ohnehin automatisch mit ihrem Inhalt, kein Fixed-Size-Eingriff noetig.
        if not self.text() and menu is not None:
            self.setFixedSize(ICON_MENU_SIZE)

    def set_color_override(self, color: str | None) -> None:
        """Erzwingt eine feste Icon-Farbe unabhaengig vom Theme-Text -- z.B.
        fuer den Aufnahme-Button in timeline_tab.py, der bewusst immer
        rot/blinkend statt in der normalen Theme-Textfarbe erscheinen soll."""
        self._color_override = color
        self._apply_icon(current_palette())


class SplitIconButton(_IconColorMixin, QToolButton):
    """Icon-Button mit Menue, bei dem Icon-Klick und Menue-Pfeil-Klick getrennte
    Ziele sind (anders als IconButton/QPushButton, wo ein Klick auf die
    gesamte Flaeche immer das Menue oeffnet). Nutzt Qt's MenuButtonPopup, das
    Icon- und Pfeil-Bereich als eigene Klickzonen zeichnet: Klick aufs Icon
    feuert `clicked`, Klick auf den Pfeil oeffnet das per setMenu() gesetzte
    Menue -- exakt das in FEATURES.md Punkt 1 gewuenschte Verhalten fuer den
    "Zeile hinzufuegen"-Button im Testablauf-Reiter."""

    def __init__(self, icon_name: str, tooltip: str) -> None:
        super().__init__()
        self.setToolTip(tooltip)
        self.setIconSize(ICON_SIZE)
        self.setFixedSize(ICON_MENU_SIZE)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._init_icon_color(icon_name)
