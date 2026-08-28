"""Einstiegspunkt der Labor-Steuerungs-GUI."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # fuer korad_kel102/hcs34xx
sys.path.insert(0, str(Path(__file__).resolve().parent))  # fuer lokale Module (control_tab, ...)

from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from settings import Settings
from theme import ThemeManager


def main() -> None:
    app = QApplication(sys.argv)
    settings = Settings()
    ThemeManager.instance().apply(settings.dark_mode)
    window = MainWindow(settings)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
