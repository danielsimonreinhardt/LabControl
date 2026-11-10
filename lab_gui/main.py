"""Einstiegspunkt der Labor-Steuerungs-GUI."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # fuer scope_api
# Geraete-Treiber (korad_kel102, hcs34xx, ...) liegen seit 0.20.2 gesammelt in
# device-driver/ -- wegen des Bindestrichs kein Paket, sondern ein Suchpfad,
# damit die Importe ("from hcs34xx.driver import ...") unveraendert bleiben.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "device-driver"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # fuer lokale Module (control_tab, ...)

import logging
import os

from PySide6.QtWidgets import QApplication, QWidget

import app_logging
from app_icon import icon as app_icon
from app_logging import setup_logging
from i18n import Translator
import shutdown_signals
from main_window import MainWindow
from settings import Settings
from theme import ThemeManager
from version import __version__

logger = logging.getLogger(__name__)

# pyi_splash steuert den PyInstaller-Bootloader-Splash (siehe .spec:
# Splash(...)) -- der ist bereits waehrend der Onefile-Entpackung sichtbar,
# also bevor dieser Code ueberhaupt laeuft, und muss hier nur noch
# geschlossen werden. Existiert nur in einem so gebauten .exe; im
# Dev-Betrieb (python lab_gui/main.py) schlicht nicht importierbar -> No-op.
try:
    import pyi_splash
except ImportError:
    pyi_splash = None

# Fensterzustand beim Start, z. B. am Raspberry Pi per systemd-Dienst (tools/pi/labcontrol.service):
# "maximized" oder "fullscreen"; leer/unbekannt = normales Fenster wie bisher.
WINDOW_ENV = "LABCONTROL_WINDOW"
# Feste Wayland-App-ID (sonst "python3"), damit der Compositor das Fenster erkennt.
DESKTOP_FILE_NAME = "labcontrol"


def show_window(window: QWidget, mode: str | None = None) -> str:
    """Zeigt das Hauptfenster im gewuenschten Zustand und gibt den verwendeten zurueck."""
    mode = (os.environ.get(WINDOW_ENV, "") if mode is None else mode).strip().lower()
    if mode == "fullscreen":
        window.showFullScreen()
    elif mode == "maximized":
        window.showMaximized()
    else:
        mode = "normal"
        window.show()
    return mode


def main() -> None:
    # Einstellungen vor dem Logging: der Log-Ordner (Netzlaufwerk) steht darin.
    settings = Settings()
    setup_logging(settings.log_dir)
    logger.info("LAB CONTROL v%s startet", __version__)
    app = QApplication(sys.argv)
    app.setDesktopFileName(DESKTOP_FILE_NAME)
    app.setWindowIcon(app_icon())
    Translator.instance().set_language(settings.language)
    ThemeManager.instance().apply(settings.dark_mode)
    window = MainWindow(settings)
    # SIGTERM/Strg+C beenden wie das Schliessen des Fensters (Netzteile freigeben usw.)
    shutdown_signals.install(window.close, app)   # app haelt den Weck-Timer
    mode = show_window(window)
    if mode != "normal":
        logger.info("Fenster: %s (%s)", mode, WINDOW_ENV)
    if pyi_splash is not None:
        pyi_splash.close()
    exit_code = app.exec()
    logger.info("LAB CONTROL beendet (exit_code=%d)", exit_code)
    app_logging.shutdown()   # restliche Meldungen schreiben; ein haengendes Netzlaufwerk haelt nicht auf
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
