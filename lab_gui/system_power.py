"""Rechner herunterfahren / neu starten ueber systemd-logind (Raspberry Pi, 10.11.2026).

Fuer den Knopf in der Touch-Leiste: Am Pi gibt es sonst keine Moeglichkeit, das Geraet sauber
auszuschalten, wenn LabControl im Vollbild (Kiosk) laeuft. Der Weg ist derselbe wie beim
Herunterfahren aus dem Desktop-Menue: logind kuendigt es an (PrepareForShutdown), LabControl gibt
die Netzteile frei und beendet sich (shutdown_inhibit.py), erst dann faehrt der Pi herunter.

Als Dienst des angemeldeten Benutzers erlaubt logind das ohne Passwort ("yes"); aus einer
SSH-Sitzung waere es eine Rueckfrage ("challenge") -- available() prueft genau das.
Unter Windows und ohne logind: available() ist False, der Knopf erscheint nicht.
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

LOGIND = ("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager")


def _manager():
    if not sys.platform.startswith("linux"):
        return None
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface
    except ImportError:
        return None
    bus = QDBusConnection.systemBus()
    if not bus.isConnected():
        return None
    iface = QDBusInterface(*LOGIND, bus)
    return iface if iface.isValid() else None


def available() -> bool:
    """Darf dieser Prozess ohne Rueckfrage herunterfahren (logind CanPowerOff == "yes")?"""
    iface = _manager()
    if iface is None:
        return False
    reply = iface.call("CanPowerOff")
    args = reply.arguments()
    return bool(args) and args[0] == "yes"


def _call(method: str) -> bool:
    iface = _manager()
    if iface is None:
        logger.warning("%s nicht moeglich: kein logind", method)
        return False
    reply = iface.call(method, False)   # interactive=False: keine Passwortabfrage
    if reply.errorMessage():
        logger.warning("%s abgelehnt: %s", method, reply.errorMessage())
        return False
    logger.info("%s angefordert (Bedienung am Geraet)", method)
    return True


def power_off() -> bool:
    return _call("PowerOff")


def reboot() -> bool:
    return _call("Reboot")
