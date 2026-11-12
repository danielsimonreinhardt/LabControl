"""Symbol "Geraet haengt am anderen Rechner" in Kacheln von Partnergeraeten (Verbund, 0.52.0).

Nutzerwunsch 12.11.2026: Partnergeraete stehen in Uebersicht und Steuerung wie eigene Geraete; das
einzige Unterscheidungsmerkmal ist dieses Symbol direkt hinter dem Kacheltitel (Tooltip: Rechnername).
Kind-Widget mit absoluter Position wie no_device_tile.OfflineBadge; folgt Groesse, Titel und Theme.
"""
from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QGroupBox, QLabel, QStyle, QStyleOptionGroupBox, QWidget

from i18n import tr
from theme import Palette, ThemeManager, no_own_background
from theme import current as current_palette

ICON_NAME = "mdi.lan-connect"
ICON_SIZE = 16
TITLE_X = 10      # Titel beginnt etwa so weit vom linken Rand (wie OfflineBadge.place)
GAP = 6


class RemoteBadge(QLabel):
    def __init__(self, parent: QWidget, host: str) -> None:
        super().__init__(parent)
        no_own_background(self)
        self._host = host
        self.setFixedSize(ICON_SIZE, ICON_SIZE)
        self.setToolTip(tr("Gerät am Rechner {host}", host=host))
        ThemeManager.instance().changed.connect(self._restyle)
        parent.installEventFilter(self)
        self._restyle(current_palette())
        self.place()
        self.show()

    def _restyle(self, palette: Palette) -> None:
        self.setPixmap(qta.icon(ICON_NAME, color=palette.accent).pixmap(ICON_SIZE, ICON_SIZE))

    def eventFilter(self, obj: QObject, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FontChange,
                            QEvent.Type.StyleChange, QEvent.Type.Paint):
            self.place()
        return False

    def place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        if isinstance(parent, QGroupBox) and parent.title():
            # Titelbereich so, wie der Stil ihn zeichnet (Touch-Stylesheet verschiebt ihn)
            opt = QStyleOptionGroupBox()
            parent.initStyleOption(opt)
            label = parent.style().subControlRect(QStyle.ComplexControl.CC_GroupBox, opt,
                                                  QStyle.SubControl.SC_GroupBoxLabel, parent)
            x = label.right() + GAP
            y = max(0, label.center().y() - ICON_SIZE // 2 + 1)
        else:
            x, y = TITLE_X, 2
        if self.pos().x() != x or self.pos().y() != y:
            self.move(x, y)
        self.raise_()


def attach(widget: QWidget, host: str) -> RemoteBadge:
    """Symbol an eine Kachel haengen (einmal je Kachel; ein zweiter Aufruf liefert das vorhandene)."""
    badge = widget.findChild(RemoteBadge)
    return badge if badge is not None else RemoteBadge(widget, host)
