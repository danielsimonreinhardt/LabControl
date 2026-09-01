"""Flow-Layout: reiht Widgets nebeneinander und bricht erst in die naechste
Zeile um, wenn die verfuegbare Breite nicht mehr reicht (wie CSS flex-wrap).

Qt bringt so ein Layout nicht von Haus aus mit -- dies ist die uebliche
Anpassung des offiziellen Qt-Beispiels ("Flow Layout"), erweitert um das
Ueberspringen unsichtbarer Widgets (siehe control_tab.py: Geraete-Sektionen
werden beim Trennen versteckt statt zerstoert und sollen dann keinen Platz
mehr beanspruchen).
"""
from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidgetItem


class FlowLayout(QLayout):
    def __init__(self, parent: object = None, spacing: int = 12) -> None:
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(QMargins(0, 0, 0, 0))
        self.setSpacing(spacing)
        self._items: list[QWidgetItem] = []

    def addItem(self, item: QWidgetItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QWidgetItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QWidgetItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        margins = self.contentsMargins()
        content_width = width - margins.left() - margins.right()
        content_height = self._do_layout(QRect(0, 0, content_width, 0), test_only=True)
        return content_height + margins.top() + margins.bottom()

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        # _do_layout() platziert Items relativ zum uebergebenen Rect (x/y als
        # Startpunkt, right() als Umbruchgrenze) -- ohne marginsRemoved() hier
        # wuerden contentsMargins() zwar in minimumSize() eingerechnet (der
        # Container fordert dadurch mehr Platz an), aber beim tatsaechlichen
        # Positionieren ignoriert: die Items klebten dann trotzdem buendig am
        # Rand des vollen Rects statt einen Aussenabstand einzuhalten.
        self._do_layout(rect.marginsRemoved(self.contentsMargins()), test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            if item.widget() is not None and not item.widget().isVisible():
                continue
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            widget = item.widget()
            if widget is not None and not widget.isVisible():
                continue

            item_size = item.sizeHint()
            style = widget.style() if widget is not None else self.parentWidget().style()
            space_x = spacing + style.layoutSpacing(
                QSizePolicy.ControlType.PushButton, QSizePolicy.ControlType.PushButton, Qt.Orientation.Horizontal
            )
            space_y = spacing + style.layoutSpacing(
                QSizePolicy.ControlType.PushButton, QSizePolicy.ControlType.PushButton, Qt.Orientation.Vertical
            )

            next_x = x + item_size.width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item_size.width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y()
