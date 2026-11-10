"""Beenden per Betriebssystem-Signal auf demselben Weg wie das Schliessen des Fensters.

Freigabe der Netzteile, "Alle aus" und das Abmelden der CAN-Knoten laufen in
MainWindow.closeEvent. Ohne diese Handler beendet SIGTERM (systemd stop,
Herunterfahren des Raspberry Pi) bzw. Strg+C den Prozess, ohne dass etwas davon
passiert (10.11.2026, Portierung auf den Pi).

Python-Signalhandler laufen nur, wenn der Interpreter die Kontrolle hat; im
Qt-Event-Loop gibt ihm ein leerer Timer dazu regelmaessig Gelegenheit.
"""
from __future__ import annotations

import logging
import signal
from typing import Callable

from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)

WAKE_INTERVAL_MS = 250
SIGNAL_NAMES = ("SIGTERM", "SIGINT", "SIGHUP")   # SIGHUP gibt es unter Windows nicht


def install(close: Callable[[], object], parent: QObject | None = None) -> QTimer:
    """Legt SIGTERM/SIGINT/SIGHUP auf `close` (z.B. MainWindow.close). Nur der
    erste Aufruf schliesst: ein zweites Signal waehrend des Abbaus wuerde sonst
    closeEvent erneut gegen einen schon beendeten Worker-Thread laufen lassen.
    Liefert den Weck-Timer; ohne `parent` muss der Aufrufer ihn am Leben halten."""
    received: list[int] = []

    def handler(signum: int, _frame) -> None:
        if received:
            logger.info("Signal %s waehrend des Beendens ignoriert", signal.Signals(signum).name)
            return
        received.append(signum)
        logger.info("Signal %s empfangen -> LabControl wird beendet", signal.Signals(signum).name)
        QTimer.singleShot(0, close)

    for name in SIGNAL_NAMES:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError) as exc:   # ValueError: nicht im Haupt-Thread
            logger.warning("Signal %s nicht abfangbar: %s", name, exc)

    timer = QTimer(parent)
    timer.timeout.connect(lambda: None)
    timer.start(WAKE_INTERVAL_MS)
    return timer
