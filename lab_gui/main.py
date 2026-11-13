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

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

import app_logging
import data_store
import headless
from app_icon import icon as app_icon
from app_logging import setup_logging
from i18n import Translator, tr
from instance_lock import InstanceLock
import shutdown_inhibit
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
    # Kommandozeile ohne Fenster (0.58.0): "run"/"devices" als erstes Argument
    if headless.is_cli(sys.argv):
        if pyi_splash is not None:
            pyi_splash.close()
        sys.exit(headless.main(sys.argv))
    # Einstellungen vor dem Logging: das Netzlaufwerk steht darin.
    settings = Settings()
    setup_logging(settings.network_dir)
    data_store.setup(app_logging.effective_network_dir(settings.network_dir))
    logger.info("LAB CONTROL v%s startet", __version__)
    app = QApplication(sys.argv)
    app.setDesktopFileName(DESKTOP_FILE_NAME)
    app.setWindowIcon(app_icon())
    Translator.instance().set_language(settings.language)
    ThemeManager.instance().apply(settings.dark_mode)
    # Zweite Instanz auf demselben Datenordner wuerde dieselben Ports oeffnen (0.58.0). 3 s Geduld: ein
    # Neustart des Dienstes kann die alte Instanz noch beim Beenden treffen.
    lock = InstanceLock()
    if not lock.acquire(3000):
        holder = lock.holder()
        logger.warning("LabControl läuft bereits (%s) – zweite Instanz beendet", holder or "?")
        if pyi_splash is not None:
            pyi_splash.close()
        QMessageBox.warning(None, "LAB CONTROL", tr("LabControl läuft bereits."))
        app_logging.shutdown()
        sys.exit(1)
    window = MainWindow(settings)
    # SIGTERM/Strg+C und (Linux) das angekuendigte Herunterfahren beenden wie das Schliessen
    # des Fensters (Netzteile freigeben usw.) -- nur einmal, egal was zuerst kommt.
    close_once = shutdown_signals.once(window.close)
    shutdown_signals.install(close_once, app)   # app haelt den Weck-Timer
    shutdown_inhibit.install(close_once, app)   # vor dem Desktop-Ende, siehe Modul
    mode = show_window(window)
    if mode != "normal":
        logger.info("Fenster: %s (%s)", mode, WINDOW_ENV)
    if pyi_splash is not None:
        pyi_splash.close()
    exit_code = app.exec()
    logger.info("LAB CONTROL beendet (exit_code=%d)", exit_code)
    lock.release()
    data_store.store().stop()   # laufendes Nachschieben beenden (Daemon, haelt nicht auf)
    app_logging.shutdown()   # restliche Meldungen schreiben; ein haengendes Netzlaufwerk haelt nicht auf
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
