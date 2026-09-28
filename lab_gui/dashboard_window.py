"""Eigenes Fenster fuer das abgedockte Dashboard (z.B. auf einem zweiten Bildschirm).

Traegt dasselbe DashboardWidget wie das Hauptfenster -- es wird nur umgehaengt,
nicht kopiert, damit alle Verbindungen zu Worker, Einstellungen und Kacheln
unveraendert weiterlaufen. Das Umhaengen macht MainWindow (_detach_dashboard/
_dock_dashboard); dieses Fenster meldet nur sein Schliessen.

Bewusst ohne Eltern-Fenster: ein Qt-Fenster mit Eltern bleibt unter Windows immer
ueber dem Hauptfenster und wird mit ihm minimiert -- auf einem zweiten Bildschirm
soll es aber unabhaengig stehen bleiben, mit eigenem Taskleisten-Eintrag.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from i18n import Translator, tr
from version import __version__


class DashboardWindow(QWidget):
    # Fenster wurde geschlossen (Schliessen-Kreuz, Knopf im Dashboard oder
    # Programmende) -- MainWindow holt das Dashboard dann zurueck.
    closed = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self._layout = QVBoxLayout(self)
        # Das Dashboard ist vertikal Fixed (siehe DashboardWidget.__init__) --
        # zusaetzliche Fensterhoehe geht an den Stretch darunter.
        self._layout.addStretch(1)
        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self.setWindowTitle(f"LAB CONTROL v{__version__} – {tr('Dashboard')}")

    def set_dashboard(self, dashboard: QWidget) -> None:
        self._layout.insertWidget(0, dashboard)
        dashboard.show()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().closeEvent(event)
        self.closed.emit()
