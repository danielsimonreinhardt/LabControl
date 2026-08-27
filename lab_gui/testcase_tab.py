"""Testcase-Tab: Platzhalter, Testablauf-Definition folgt spaeter."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class TestcaseTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        placeholder = QLabel("Testabläufe werden hier definiert (in Entwicklung)")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: gray; font-size: 14px;")
        layout.addWidget(placeholder)
