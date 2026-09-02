"""Eigenes Increment-Verhalten fuer die Pfeil-Buttons an Sollwert-Eingabe-
feldern (siehe FEATURES.md Punkt 3): einfacher Klick aendert den Wert um
0,1, ein gehaltener Klick um 1,0 pro Schritt alle 0,2s. Ersetzt dafuer Qts
eingebautes Klick-/Halte-Verhalten (singleStep + internes Auto-Repeat)
komplett, indem die Maus-Events fuer die Pfeil-Subcontrols selbst
ausgewertet werden -- Tastatur-Pfeiltasten und Mausrad bleiben unveraendert
(dort gilt weiterhin singleStep, siehe QAbstractSpinBox).
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDoubleSpinBox, QStyle, QStyleOptionSpinBox

_SMALL_STEP = 0.1
_LARGE_STEP = 1.0
_HOLD_THRESHOLD_MS = 300
_REPEAT_INTERVAL_MS = 200


class SteppedDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox mit 0,1/Klick, 1,0/Schritt beim Halten (alle 0,2s)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSingleStep(_SMALL_STEP)
        self._pressed_control = QStyle.SubControl.SC_None
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._start_repeat)
        self._repeat_timer = QTimer(self)
        self._repeat_timer.timeout.connect(lambda: self._apply_step(_LARGE_STEP))

    def _sub_control_at(self, pos):
        opt = QStyleOptionSpinBox()
        self.initStyleOption(opt)
        for sc in (QStyle.SubControl.SC_SpinBoxUp, QStyle.SubControl.SC_SpinBoxDown):
            rect = self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, opt, sc, self)
            if rect.contains(pos):
                return sc
        return QStyle.SubControl.SC_None

    def _begin_press(self, event) -> bool:
        sc = self._sub_control_at(event.position().toPoint())
        if sc not in (QStyle.SubControl.SC_SpinBoxUp, QStyle.SubControl.SC_SpinBoxDown):
            return False
        self._pressed_control = sc
        self._apply_step(_SMALL_STEP)
        self._hold_timer.start(_HOLD_THRESHOLD_MS)
        self.update()
        event.accept()
        return True

    def mousePressEvent(self, event):
        if not self._begin_press(event):
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Ohne diese Weiterleitung wuerde Qt den zweiten Klick eines schnellen
        # Doppelklicks als mouseDoubleClickEvent statt mousePressEvent liefern
        # (Standard-QWidget-Verhalten) -- der Schritt wuerde dann ausbleiben.
        if not self._begin_press(event):
            super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pressed_control != QStyle.SubControl.SC_None:
            self._stop_stepping()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._pressed_control != QStyle.SubControl.SC_None:
            if self._sub_control_at(event.position().toPoint()) != self._pressed_control:
                self._stop_stepping()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _start_repeat(self) -> None:
        if self._pressed_control == QStyle.SubControl.SC_None:
            return
        self._apply_step(_LARGE_STEP)
        self._repeat_timer.start(_REPEAT_INTERVAL_MS)

    def _apply_step(self, step: float) -> None:
        delta = step if self._pressed_control == QStyle.SubControl.SC_SpinBoxUp else -step
        self.setValue(round(self.value() + delta, self.decimals()))

    def _stop_stepping(self) -> None:
        self._hold_timer.stop()
        self._repeat_timer.stop()
        self._pressed_control = QStyle.SubControl.SC_None
        self.update()

    def initStyleOption(self, option: QStyleOptionSpinBox) -> None:
        super().initStyleOption(option)
        if self._pressed_control != QStyle.SubControl.SC_None:
            option.activeSubControls = self._pressed_control
            option.state |= QStyle.StateFlag.State_Sunken
