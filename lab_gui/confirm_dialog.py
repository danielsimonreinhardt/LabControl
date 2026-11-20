"""Rueckfrage eines Bestaetigungsschritts im Testablauf (PI-71 a, 1.0.1).

Der Lauf haelt an, bis der Nutzer hier "Weiter" oder "Abbrechen" waehlt. Bewusst nicht modal: ALLE AUS, Stopp
und die Kacheln bleiben bedienbar, waehrend die Rueckfrage offen ist. Kein Knopf ist Standardknopf, damit ein
versehentliches Enter nichts bestaetigt; Schliessen/Escape zaehlt wie "Abbrechen" (sichere Richtung). Im
Touch-Profil greift wie bei jedem Dialog touch_ui.TouchSizer (Knopfhoehe, Bildschirmgroesse).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from i18n import tr


class ConfirmStepDialog(QDialog):
    answered = Signal(bool)   # True = Weiter, False = Abbrechen

    def __init__(self, step_number: int, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("confirmStepDialog")
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle(tr("Bestätigung – Schritt {n}", n=step_number))
        self._answered = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(16)
        self.text_label = QLabel(text or tr("Weiter?"))
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        font = QFont(self.text_label.font())
        font.setPointSizeF(font.pointSizeF() * 1.25)
        self.text_label.setFont(font)
        self.text_label.setMinimumWidth(360)
        layout.addWidget(self.text_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton(tr("Abbrechen"))
        self.cancel_button.setToolTip(tr("Lauf abbrechen wie Stopp (ALLE AUS)"))
        self.continue_button = QPushButton(tr("Weiter"))
        for button in (self.cancel_button, self.continue_button):
            button.setAutoDefault(False)
            button.setDefault(False)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.continue_button.clicked.connect(lambda: self._answer(True))
        self.cancel_button.clicked.connect(lambda: self._answer(False))

    def _answer(self, accepted: bool) -> None:
        if not self._answered:
            self._answered = True
            self.answered.emit(accepted)
        super().done(1 if accepted else 0)

    def dismiss(self) -> None:
        """Ohne Antwort schliessen (Lauf wurde anders beendet: Stopp, Fehler, ueber das Netzwerk)."""
        self._answered = True
        super().done(0)

    def reject(self) -> None:   # Escape und Fensterkreuz (QDialog.closeEvent ruft reject)
        self._answer(False)
