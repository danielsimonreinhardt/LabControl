"""Herunterfahren und Neustart unter Linux: Netzteile freigeben, BEVOR der Desktop endet.

Am Raspberry Pi (10.11.2026) zeigte sich: Beim Neustart aus dem Desktop-Menue endet der
Wayland-Compositor (labwc) oft vor LabControl. Qt beendet den Prozess dann sofort
("The Wayland connection broke", Exit-Code 255), ohne closeEvent -- keine Freigabe der
Netzteile, kein Log. SIGTERM (shutdown_signals) kommt in diesem Fall zu spaet.

Abhilfe ueber systemd-logind: LabControl haelt eine Verzoegerungs-Sperre ("delay inhibitor")
fuer "shutdown". Vor dem Herunterfahren sendet logind PrepareForShutdown(true) und wartet,
bis die Sperre frei ist (hoechstens InhibitDelayMaxSec, tools/pi/setup_pi.sh setzt 15 s).
LabControl schliesst darauf auf demselben Weg wie beim Schliessen des Fensters; mit dem
Prozessende schliesst sich der Datei-Deskriptor der Sperre und logind faehrt fort.

Ohne logind/System-D-Bus (Windows, Container, WSL ohne systemd) tut install() nichts.
"""
from __future__ import annotations

import logging
import sys
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Slot, SLOT

logger = logging.getLogger(__name__)

LOGIND_SERVICE = "org.freedesktop.login1"
LOGIND_PATH = "/org/freedesktop/login1"
LOGIND_INTERFACE = "org.freedesktop.login1.Manager"
WHO = "LabControl"
WHY = "Netzteile freigeben und Geraete sauber trennen"


class ShutdownInhibitor(QObject):
    """Haelt die logind-Sperre und ruft beim angekuendigten Herunterfahren `close` auf."""

    def __init__(self, close: Callable[[], object], bus, parent: QObject | None = None,
                 service: str = LOGIND_SERVICE, path: str = LOGIND_PATH,
                 interface: str = LOGIND_INTERFACE) -> None:
        super().__init__(parent)
        self._close = close
        self._bus = bus
        self._service, self._path, self._interface = service, path, interface
        self._lock = None          # QDBusUnixFileDescriptor; solange er lebt, gilt die Sperre
        self.closing = False

    @property
    def active(self) -> bool:
        return self._lock is not None and self._lock.isValid()

    def start(self) -> bool:
        if not self._bus.connect(self._service, self._path, self._interface, "PrepareForShutdown",
                                 self, SLOT("on_prepare_for_shutdown(bool)")):
            logger.warning("Herunterfahren: Signal PrepareForShutdown nicht abonnierbar (%s)",
                           self._bus.lastError().message())
            return False
        return self._take_lock()

    def _take_lock(self) -> bool:
        from PySide6.QtDBus import QDBusInterface, QDBusMessage, QDBusUnixFileDescriptor
        iface = QDBusInterface(self._service, self._path, self._interface, self._bus)
        reply = iface.call("Inhibit", "shutdown", WHO, WHY, "delay")
        args = reply.arguments() if reply.type() == QDBusMessage.MessageType.ReplyMessage else []
        if not args or not isinstance(args[0], QDBusUnixFileDescriptor) or not args[0].isValid():
            logger.warning("Herunterfahren: keine logind-Sperre erhalten (%s)",
                           reply.errorMessage() or "unerwartete Antwort")
            self._lock = None
            return False
        self._lock = args[0]
        return True

    @Slot(bool)
    def on_prepare_for_shutdown(self, start: bool) -> None:
        if not start:
            # Herunterfahren abgebrochen: Sperre fuers naechste Mal erneuern
            if not self.closing and not self.active:
                self._take_lock()
            return
        if self.closing:
            return
        self.closing = True
        logger.info("System wird heruntergefahren -> LabControl wird beendet")
        QTimer.singleShot(0, self._close)


def install(close: Callable[[], object], parent: QObject | None = None) -> ShutdownInhibitor | None:
    """Unter Linux mit logind: Sperre nehmen und `close` beim Herunterfahren aufrufen.
    Liefert den Inhibitor (haengt an `parent`) oder None, wenn es nicht geht."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        from PySide6.QtDBus import QDBusConnection
    except ImportError as exc:   # PySide6 ohne QtDBus
        logger.warning("Herunterfahren: QtDBus fehlt (%s), Freigabe nur ueber SIGTERM", exc)
        return None
    bus = QDBusConnection.systemBus()
    if not bus.isConnected():
        logger.info("Herunterfahren: kein System-D-Bus, Freigabe nur ueber SIGTERM")
        return None
    inhibitor = ShutdownInhibitor(close, bus, parent)
    if not inhibitor.start():
        inhibitor.deleteLater()
        return None
    logger.info("Herunterfahren: logind-Sperre gehalten, Netzteile werden vor dem Desktop freigegeben")
    return inhibitor
