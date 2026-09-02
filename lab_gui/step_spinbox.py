"""Eigenes Increment-Verhalten fuer die Pfeil-Buttons an Zahlen-Eingabe-
feldern (siehe FEATURES.md Punkt 3, BUGS.md #21 fuer die app-weite
Ausweitung): einfacher Klick aendert den Wert um einen kleinen Schritt, ein
gehaltener Klick um einen groesseren Schritt alle 0,2s. Ersetzt dafuer Qts
eingebautes Klick-/Halte-Verhalten (singleStep + internes Auto-Repeat)
komplett, indem die Maus-Events fuer die Pfeil-Subcontrols selbst
ausgewertet werden -- Tastatur-Pfeiltasten und Mausrad bleiben unveraendert
(dort gilt weiterhin singleStep, siehe QAbstractSpinBox).

Zwei konkrete Klassen teilen sich die Maus-Event-Logik ueber
_SteppedSpinMixin und unterscheiden sich nur in Schrittweite und
Wertrundung: SteppedDoubleSpinBox (0,1/1,0, wie urspruenglich nur in
control_tab.py verwendet) und SteppedSpinBox fuer Ganzzahl-Felder (1/10,
z.B. Durchlaufzahl, max. Iterationen, Intervall, Zeilenbereich).
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox, QStyle, QStyleOptionSpinBox

_DOUBLE_SMALL_STEP = 0.1
_DOUBLE_LARGE_STEP = 1.0
_INT_SMALL_STEP = 1
_INT_LARGE_STEP = 10
_HOLD_THRESHOLD_MS = 300
_REPEAT_INTERVAL_MS = 200


class _SteppedSpinMixin:
    """Gemeinsame Maus-Event-Auswertung fuer SteppedDoubleSpinBox/
    SteppedSpinBox (siehe Modul-Docstring). Muss als ERSTE Basisklasse vor
    dem jeweiligen Q*SpinBox stehen, damit super() innerhalb dieses Mixins
    per MRO auf die Qt-Basisklasse durchgreift."""

    def _init_stepping(self, small_step, large_step) -> None:
        self._small_step = small_step
        self._large_step = large_step
        self.setSingleStep(small_step)
        self._pressed_control = QStyle.SubControl.SC_None
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._start_repeat)
        self._repeat_timer = QTimer(self)
        self._repeat_timer.timeout.connect(lambda: self._apply_step(self._large_step))

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
        self._apply_step(self._small_step)
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
        self._apply_step(self._large_step)
        self._repeat_timer.start(_REPEAT_INTERVAL_MS)

    def _apply_step(self, step) -> None:
        delta = step if self._pressed_control == QStyle.SubControl.SC_SpinBoxUp else -step
        self.setValue(self._stepped_value(self.value() + delta))

    def _stepped_value(self, value):
        """Rundung des neuen Werts vor setValue() -- Ganzzahl-Felder brauchen
        keine, Dezimalfelder runden auf self.decimals() (siehe
        SteppedDoubleSpinBox), sonst summieren sich Gleitkomma-Ungenauigkeiten
        ueber viele Schritte auf."""
        return value

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


class SteppedDoubleSpinBox(_SteppedSpinMixin, QDoubleSpinBox):
    """QDoubleSpinBox mit 0,1/Klick, 1,0/Schritt beim Halten (alle 0,2s) per
    Default -- ueber small_step/large_step pro Feld anpassbar, falls die
    Groessenordnung des Werts (z.B. Tastgrad in %) andere Schritte sinnvoller
    macht als die Default-0,1/1,0 fuer Sollwerte."""

    def __init__(self, parent=None, small_step: float = _DOUBLE_SMALL_STEP, large_step: float = _DOUBLE_LARGE_STEP):
        super().__init__(parent)
        self._init_stepping(small_step, large_step)

    def _stepped_value(self, value: float) -> float:
        return round(value, self.decimals())


class SteppedSpinBox(_SteppedSpinMixin, QSpinBox):
    """QSpinBox (Ganzzahl) mit 1/Klick, 10/Schritt beim Halten (alle 0,2s)
    per Default -- fuer Ganzzahl-Felder wie Durchlaufzahl, max. Iterationen,
    Intervall (ms) oder Zeilenbereich (BUGS.md #21). small_step/large_step
    pro Feld anpassbar (z.B. 50/200 fuer ein Intervall-Feld in ms-Schritten)."""

    def __init__(self, parent=None, small_step: int = _INT_SMALL_STEP, large_step: int = _INT_LARGE_STEP):
        super().__init__(parent)
        self._init_stepping(small_step, large_step)

    def _stepped_value(self, value: float) -> int:
        return int(round(value))
