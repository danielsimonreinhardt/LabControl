"""Einstiegspunkt der Labor-Steuerungs-GUI."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # fuer korad_kel102/hcs34xx
sys.path.insert(0, str(Path(__file__).resolve().parent))  # fuer lokale Module (control_tab, ...)

import logging

from PySide6.QtWidgets import QApplication

from app_logging import setup_logging
from i18n import Translator
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


def main() -> None:
    setup_logging()
    logger.info("LAB CONTROL v%s startet", __version__)
    app = QApplication(sys.argv)
    settings = Settings()
    Translator.instance().set_language(settings.language)
    ThemeManager.instance().apply(settings.dark_mode)
    window = MainWindow(settings)
    window.show()
    if pyi_splash is not None:
        pyi_splash.close()
    exit_code = app.exec()
    logger.info("LAB CONTROL beendet (exit_code=%d)", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
