"""Fuehrt eine Liste von TestStep-Schritten sequenziell aus.

Fuer jeden normalen Schritt wird die Aktion per Signal an den
DeviceWorker-Thread geschickt und auf dessen Ergebnis (action_completed)
gewartet, bevor die konfigurierte Wartezeit beginnt und der naechste Schritt
startet. Ein Arbiträrsignal-Schritt (siehe testcase_model.is_arb_action)
laeuft stattdessen als Folge einzelner Sollwert-Updates im Abstand von
step.arb_interval_ms, bis step.duration erreicht ist -- die Geraete koennen
keine echten Signalformen ausgeben, nur diskrete Sollwerte annehmen. Der
naechste Sample wird erst verschickt, nachdem der vorherige bestaetigt wurde
(kein "totes" Nachlegen in die Warteschlange, falls ein Geraet langsamer
antwortet als das konfigurierte Intervall).
Schlaegt ein Schritt (oder ein einzelnes Sample) fehl (Geraet nicht
verbunden oder Kommunikationsfehler), wird der Ablauf sofort angehalten
(step_failed) statt weiterzumachen. Deaktivierte Schritte werden
uebersprungen. Dieser Runner laeuft im GUI-Thread, blockiert ihn aber nicht
(Wartezeiten laufen ueber QTimer statt time.sleep).
"""
from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal, Slot

from testcase_model import TestStep, arb_value, is_arb_action


class TestRunner(QObject):
    execute_action = Signal(str, str, str, float)  # device_id, device_kind, action, value
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

        # Zustand fuer einen laufenden Arbiträrsignal-Schritt.
        self._arb_active = False
        self._arb_clock = QElapsedTimer()
        self._arb_timer = QTimer(self)
        self._arb_timer.setSingleShot(True)
        self._arb_timer.timeout.connect(self._send_arb_sample)

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
        self._arb_timer.stop()
        self._arb_active = False
        self._running = False
        self.run_stopped.emit()

    @Slot(bool, str)
    def on_action_completed(self, success: bool, message: str) -> None:
        if not self._running:
            return  # Ergebnis eines bereits gestoppten Laufs -- ignorieren
        if not success:
            self._running = False
            self._arb_active = False
            self.step_failed.emit(self._index, message)
            return
        if self._arb_active:
            self._continue_arb()
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
        if is_arb_action(step.action):
            self._start_arb(step)
        else:
            self.execute_action.emit(step.device_id, step.device_kind, step.action, step.value)

    # -- Arbiträrsignal-Ausfuehrung --------------------------------------------

    def _start_arb(self, step: TestStep) -> None:
        self._arb_active = True
        self._arb_clock.start()
        self._send_arb_sample()

    def _send_arb_sample(self) -> None:
        step = self._steps[self._index]
        t = self._arb_clock.elapsed() / 1000.0
        value = arb_value(step, t)
        self.execute_action.emit(step.device_id, step.device_kind, step.arb_target, value)

    def _continue_arb(self) -> None:
        step = self._steps[self._index]
        elapsed_s = self._arb_clock.elapsed() / 1000.0
        if elapsed_s >= step.duration:
            self._arb_active = False
            self._advance()
            return
        self._arb_timer.start(max(1, step.arb_interval_ms))
