"""Anordnen mit Uebersicht (Touch-Profil, Nutzerwunsch 12.11.2026).

Im Modus "Anordnen" liess sich nicht wischen -- Kacheln ausserhalb des sichtbaren Bereichs waren
nicht erreichbar. Jetzt legt sich ueber Uebersicht und Steuerung ein verkleinertes Abbild ALLER
Kacheln, so weit herausgezoomt, dass alles auf den Bildschirm passt. Eine Kachel wird mit dem Finger
gegriffen und abgelegt; die echte Ansicht ordnet dann mit ihrer eigenen Logik um (Uebersicht:
Reihenfolge, Steuerung: feste Zellen) und das Abbild wird neu aufgenommen.

Die Ansicht liefert dazu (ArrangeSource):
  arrange_snapshot() -> (QPixmap des ganzen Kachel-Containers, {device_id: QRect im Container})
  arrange_drop(device_id, QPointF Zeiger im Container, QPoint Griffpunkt in der Kachel)
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from theme import current as current_palette

MARGIN = 8
REFRESH_MS = 2000   # Messwerte im Abbild nachziehen


class ArrangeOverlay(QWidget):
    def __init__(self, area: QAbstractScrollArea, source) -> None:
        super().__init__(area)
        self._area = area
        self._source = source
        self._pixmap = None
        self._rects: dict[str, QRect] = {}
        self._scale = 1.0
        self._origin = QPointF(MARGIN, MARGIN)
        self._drag_id: str | None = None
        self._grab = QPoint()          # Griffpunkt in der Kachel (Container-Massstab)
        self._finger = QPointF()       # aktuelle Position (Overlay-Koordinaten)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh_idle)
        area.installEventFilter(self)
        self.setGeometry(area.rect())
        self.refresh()
        self.show()
        self.raise_()
        self._timer.start()

    def close_overlay(self) -> None:
        self._timer.stop()
        self._area.removeEventFilter(self)
        self.hide()
        self.deleteLater()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if obj is self._area and event.type() == event.Type.Resize:
            self.setGeometry(self._area.rect())
            self.refresh()
        return False

    # -- Abbild -------------------------------------------------------------------

    def refresh(self) -> None:
        self._pixmap, self._rects = self._source.arrange_snapshot()
        if self._pixmap is None or self._pixmap.isNull():
            self.update()
            return
        avail_w = max(1, self.width() - 2 * MARGIN)
        avail_h = max(1, self.height() - 2 * MARGIN)
        size = self._pixmap.deviceIndependentSize()
        self._scale = min(1.0, avail_w / max(1.0, size.width()), avail_h / max(1.0, size.height()))
        self._origin = QPointF(MARGIN + (avail_w - size.width() * self._scale) / 2, MARGIN)
        self.update()

    def _refresh_idle(self) -> None:
        if self._drag_id is None and self.isVisible():
            self.refresh()

    def scale(self) -> float:
        return self._scale

    def _to_container(self, pos: QPointF) -> QPointF:
        return QPointF((pos.x() - self._origin.x()) / self._scale, (pos.y() - self._origin.y()) / self._scale)

    def _to_overlay(self, rect: QRect) -> QRectF:
        return QRectF(self._origin.x() + rect.x() * self._scale, self._origin.y() + rect.y() * self._scale,
                      rect.width() * self._scale, rect.height() * self._scale)

    def tile_at(self, pos: QPointF) -> str | None:
        point = self._to_container(pos).toPoint()
        return next((d for d, r in self._rects.items() if r.contains(point)), None)

    # -- Zeichnen -------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        pal = current_palette()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(pal.bg))
        if self._pixmap is None or self._pixmap.isNull():
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        size = self._pixmap.deviceIndependentSize()
        painter.drawPixmap(QRectF(self._origin.x(), self._origin.y(), size.width() * self._scale,
                                  size.height() * self._scale), self._pixmap, QRectF(self._pixmap.rect()))
        pen = QPen(QColor(pal.accent))
        pen.setWidth(2)
        if self._drag_id is not None and self._drag_id in self._rects:
            source = self._rects[self._drag_id]
            target = self._to_overlay(source)
            painter.fillRect(target, QColor(pal.bg))          # Luecke, wo die Kachel herkommt
            painter.setPen(QPen(QColor(pal.border), 1, Qt.PenStyle.DashLine))
            painter.drawRect(target)
            dpr = self._pixmap.devicePixelRatio()
            piece = QRectF(source.x() * dpr, source.y() * dpr, source.width() * dpr, source.height() * dpr)
            corner = self._finger - QPointF(self._grab.x() * self._scale, self._grab.y() * self._scale)
            floating = QRectF(corner, target.size())
            painter.setOpacity(0.85)
            painter.drawPixmap(floating, self._pixmap, piece)
            painter.setOpacity(1.0)
            painter.setPen(pen)
            painter.drawRect(floating)
        else:
            painter.setPen(QPen(QColor(pal.border), 1, Qt.PenStyle.DashLine))
            for rect in self._rects.values():
                painter.drawRect(self._to_overlay(rect))

    # -- Finger ---------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        tile = self.tile_at(event.position())
        if tile is None:
            return
        self._drag_id = tile
        point = self._to_container(event.position()).toPoint()
        self._grab = point - self._rects[tile].topLeft()
        self._finger = event.position()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._drag_id is not None:
            self._finger = event.position()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._drag_id is None:
            return
        device_id, self._drag_id = self._drag_id, None
        if (event.position() - self._to_overlay(self._rects[device_id]).topLeft()
                - QPointF(self._grab.x() * self._scale, self._grab.y() * self._scale)).manhattanLength() >= 4:
            self._source.arrange_drop(device_id, self._to_container(event.position()), QPoint(self._grab))
        QTimer.singleShot(0, self.refresh)   # nach dem Umordnen neu aufnehmen
        self.update()


def tile_rects_in(container: QWidget, widgets: dict[str, QWidget]) -> dict[str, QRect]:
    """Sichtbare Kacheln als Rechtecke im Container (fuer arrange_snapshot)."""
    rects = {}
    for device_id, widget in widgets.items():
        if widget.isVisible():
            rects[device_id] = QRect(widget.mapTo(container, QPoint(0, 0)), widget.size())
    return rects
