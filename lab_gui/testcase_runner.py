"""Fuehrt eine Liste von TestStep-Schritten sequenziell aus.

Fuer jeden Schritt wird die Aktion per Signal an den DeviceWorker-Thread
geschickt und auf dessen Ergebnis (action_completed) gewartet, bevor die
konfigurierte Wartezeit beginnt und der naechste Schritt startet.
Schlaegt ein Schritt fehl (Geraet nicht verbunden oder Kommunikationsfehler),
wird der Ablauf sofort angehalten (step_failed) statt weiterzumachen.
Deaktivierte Schritte werden uebersprungen. Dieser Runner laeuft im
GUI-Thread, blockiert ihn aber nicht (Wartezeiten laufen ueber QTimer statt
time.sleep).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from testcase_model import TestStep


class TestRunner(QObject):
    execute_action = Signal(str, str, float)  # device, action, value
    step_started = Signal(int, object)         # index, TestStep
    step_failed = Signal(int, str)             # index, Fehlermeldung
    run_finished = Signal()
    run_stopped = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._steps: list[TestStep] = []
        self._index = -1
        self._running = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)

    def is_running(self) -> bool:
        return self._running

    def start(self, steps: list[TestStep]) -> None:
        if self._running:
            return
        self._steps = steps
        self._index = -1
        self._running = True
        self._advance()

    def stop(self) -> None:
        if not self._running:
            return
        self._timer.stop()
        self._running = False
        self.run_stopped.emit()

    @Slot(bool, str)
    def on_action_completed(self, success: bool, message: str) -> None:
        if not self._running:
            return  # Ergebnis eines bereits gestoppten Laufs -- ignorieren
        if not success:
            self._running = False
            self.step_failed.emit(self._index, message)
            return
        step = self._steps[self._index]
        self._timer.start(max(0, round(step.duration * 1000)))

    def _advance(self) -> None:
        if not self._running:
            return

        self._index += 1
        while self._index < len(self._steps) and not self._steps[self._index].enabled:
            self._index += 1

        if self._index >= len(self._steps):
            self._running = False
            self.run_finished.emit()
            return

        step = self._steps[self._index]
        self.step_started.emit(self._index, step)
        self.execute_action.emit(step.device, step.action, step.value)
