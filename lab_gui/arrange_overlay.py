"""Anordnen mit Uebersicht (Touch-Profil, Nutzerwunsch 12.11.2026).

Im Modus "Anordnen" liess sich nicht wischen -- Kacheln ausserhalb des sichtbaren Bereichs waren
nicht erreichbar. Jetzt legt sich ueber Uebersicht und Steuerung ein verkleinertes Abbild ALLER
Kacheln, so weit herausgezoomt, dass alles auf den Bildschirm passt. Eine Kachel wird mit dem Finger
gegriffen und abgelegt; die echte Ansicht ordnet dann mit ihrer eigenen Logik um (Uebersicht:
Reihenfolge, Steuerung: feste Zellen) und das Abbild wird neu aufgenommen.

Seit 1.0.5 (Nutzerwunsch 27.11.2026) ruecken die anderen Kacheln schon WAEHREND des Ziehens zur Seite
und machen Platz, wie beim Ziehen am PC: die echte Ansicht ordnet sich hinter dem Abbild mit ihrer
Vorschau-Logik um (gezogene Kachel ausgeblendet, Platz bleibt frei), das Abbild wird neu aufgenommen und
verschobene Kacheln gleiten an ihren neuen Platz.

Die Ansicht liefert dazu (ArrangeSource):
  arrange_snapshot() -> (QPixmap des ganzen Kachel-Containers, {device_id: QRect im Container})
  arrange_begin(device_id)                      Kachel ausblenden, ihr Platz bleibt frei
  arrange_preview(device_id, QPointF Zeiger, QPoint Griffpunkt) -> bool   Vorschau, True wenn geaendert
  arrange_drop(device_id, QPointF Zeiger im Container, QPoint Griffpunkt in der Kachel)
  arrange_cancel(device_id)                     Vorschau verwerfen, Kachel wieder einblenden
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from theme import current as current_palette

MARGIN = 8
REFRESH_MS = 2000   # Messwerte im Abbild nachziehen
DRAG_START_PX = 6   # so weit muss der Finger wandern, bevor die Kachel angehoben wird (Tipp aendert nichts)
SLIDE_MS = 160      # Kacheln gleiten an ihren neuen Platz


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
        self._press = QPointF()        # Druckpunkt (Overlay-Koordinaten)
        self._lifted = False           # Kachel angehoben, Ansicht zeigt die Vorschau
        self._piece: QPixmap | None = None   # Abbild der gezogenen Kachel (vom Druckpunkt)
        self._piece_size = QRectF()          # ihre Groesse im Container-Massstab
        self._slide_from: dict[str, QRect] = {}   # Kacheln, die gerade gleiten: alter Platz
        self._slide_t = 1.0
        self._slide = QVariantAnimation(self)
        self._slide.setStartValue(0.0)
        self._slide.setEndValue(1.0)
        self._slide.setDuration(SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.valueChanged.connect(self._on_slide)
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
        if self._drag_id is not None and self._lifted:
            self._call("arrange_cancel", self._drag_id)
        self._drag_id = None
        self._slide.stop()
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

    def _call(self, name: str, *args):
        """Methode der Ansicht, falls vorhanden (aeltere Quellen kennen nur snapshot/drop)."""
        method = getattr(self._source, name, None)
        return method(*args) if method is not None else None

    def _resnap(self, slide: bool) -> None:
        """Ansicht neu aufnehmen; Kacheln, die dabei ihren Platz wechseln, gleiten dorthin."""
        old = dict(self._rects)
        self.refresh()
        if not slide:
            return
        moved = {d: old[d] for d, rect in self._rects.items()
                 if d != self._drag_id and d in old and old[d].topLeft() != rect.topLeft()}
        if moved:
            self._slide.stop()
            self._slide_from = moved
            self._slide_t = 0.0
            self._slide.start()

    def _on_slide(self, value) -> None:
        self._slide_t = float(value)
        if self._slide_t >= 1.0:
            self._slide_from = {}
        self.update()

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
        dashed = QPen(QColor(pal.border), 1, Qt.PenStyle.DashLine)
        if self._drag_id is None:
            painter.setPen(dashed)
            for rect in self._rects.values():
                painter.drawRect(self._to_overlay(rect))
            return
        dpr = self._pixmap.devicePixelRatio()
        if self._slide_from and self._slide_t < 1.0:
            # gleitende Kacheln: am neuen Platz ausradieren, auf dem Weg dorthin zeichnen
            for device_id in self._slide_from:
                if device_id in self._rects:
                    painter.fillRect(self._to_overlay(self._rects[device_id]), QColor(pal.bg))
            t = self._slide_t
            for device_id, old in self._slide_from.items():
                new = self._rects.get(device_id)
                if new is None:
                    continue
                x = old.x() + (new.x() - old.x()) * t
                y = old.y() + (new.y() - old.y()) * t
                source = QRectF(new.x() * dpr, new.y() * dpr, new.width() * dpr, new.height() * dpr)
                target = QRectF(self._origin.x() + x * self._scale, self._origin.y() + y * self._scale,
                                new.width() * self._scale, new.height() * self._scale)
                painter.drawPixmap(target, self._pixmap, source)
        slot = self._rects.get(self._drag_id)
        if slot is not None:
            target = self._to_overlay(slot)
            if not self._lifted:
                painter.fillRect(target, QColor(pal.bg))      # Luecke, wo die Kachel herkommt
            painter.setPen(dashed)
            painter.drawRect(target)                            # hier landet sie
        if self._piece is not None:
            corner = self._finger - QPointF(self._grab.x() * self._scale, self._grab.y() * self._scale)
            floating = QRectF(corner.x(), corner.y(), self._piece_size.width() * self._scale,
                              self._piece_size.height() * self._scale)
            painter.setOpacity(0.85)
            painter.drawPixmap(floating, self._piece, QRectF(self._piece.rect()))
            painter.setOpacity(1.0)
            painter.setPen(pen)
            painter.drawRect(floating)

    # -- Finger ---------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton or self._pixmap is None or self._pixmap.isNull():
            return
        tile = self.tile_at(event.position())
        if tile is None:
            return
        rect = self._rects[tile]
        self._drag_id = tile
        self._lifted = False
        point = self._to_container(event.position()).toPoint()
        self._grab = point - rect.topLeft()
        self._press = self._finger = event.position()
        dpr = self._pixmap.devicePixelRatio()
        self._piece = self._pixmap.copy(QRect(round(rect.x() * dpr), round(rect.y() * dpr),
                                              round(rect.width() * dpr), round(rect.height() * dpr)))
        self._piece_size = QRectF(0, 0, rect.width(), rect.height())
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._drag_id is None:
            return
        self._finger = event.position()
        if not self._lifted and (self._finger - self._press).manhattanLength() >= DRAG_START_PX:
            # Kachel anheben: die Ansicht blendet sie aus, ihr Platz bleibt frei
            self._lifted = True
            self._call("arrange_begin", self._drag_id)
            self._resnap(slide=False)
        if self._lifted and self._call("arrange_preview", self._drag_id, self._to_container(self._finger),
                                       QPoint(self._grab)):
            self._resnap(slide=True)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._drag_id is None:
            return
        device_id, self._drag_id = self._drag_id, None
        lifted, self._lifted = self._lifted, False
        self._piece = None
        self._slide.stop()
        self._slide_from = {}
        if lifted:
            self._source.arrange_drop(device_id, self._to_container(event.position()), QPoint(self._grab))
        QTimer.singleShot(0, self.refresh)   # nach dem Umordnen neu aufnehmen
        self.update()


def tile_rects_in(container: QWidget, widgets: dict[str, QWidget]) -> dict[str, QRect]:
    """Sichtbare Kacheln als Rechtecke im Container (fuer arrange_snapshot) -- auch die gerade gezogene,
    die ausgeblendet ist, ihren Platz aber behaelt (Ziel der Vorschau)."""
    rects = {}
    for device_id, widget in widgets.items():
        keeps_slot = (widget.isHidden() and widget.sizePolicy().retainSizeWhenHidden()
                      and widget.parentWidget() is not None and widget.parentWidget().isVisible())
        if widget.isVisible() or keeps_slot:
            rects[device_id] = QRect(widget.mapTo(container, QPoint(0, 0)), widget.size())
    return rects
