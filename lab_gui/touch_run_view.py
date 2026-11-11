"""Testablaeufe am Touch-Display nur ausfuehren (Pi-Plan Phase 4 Schritt 3, Entscheidung PI-7 a).

Im Touch-Profil zeigt der Testablauf-Reiter statt des Editors (7 Spalten, viele Dialoge -- am
1024-px-Display nicht sinnvoll bedienbar) diese Ansicht:

- links die gespeicherten Ablaeufe aus dem gemeinsamen Ordner (data_store: Netzlaufwerk
  testcases/, sonst lokal) -- bearbeitet und gespeichert wird am PC
- rechts Name, Schritte als lesbare Liste (aktueller Schritt markiert), Fortschritt, Status
  gross, Start und Stopp als grosse Knoepfe

Ausgefuehrt wird ueber denselben Weg wie im Editor (TestcaseTab laedt die Datei, run_requested,
Stop quittiert auch einen Schrittfehler) -- gleiche Pruefungen, gleiche Sicherheitsabschaltung.
"""
from __future__ import annotations

import re
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from icons import IconButton
from theme import ThemeManager
from theme import current as current_palette
from touch_ui import enable_swipe

ROW_HEIGHT = 52
RUN_BUTTON_FONT_PX = 18   # Start/Stop zwei Stufen ueber der Grundschrift (14 px), GUI-Review UX-6


class TouchRunView(QWidget):
    file_selected = Signal(object)    # Path
    start_requested = Signal()
    stop_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setSpacing(12)

        # -- links: Ablaeufe -------------------------------------------------------
        left = QVBoxLayout()
        head = QHBoxLayout()
        self._list_title = QLabel()
        self._list_title.setStyleSheet("font-weight: bold;")
        head.addWidget(self._list_title, 1)
        self._refresh_button = IconButton("mdi.refresh", "")
        self._refresh_button.clicked.connect(self.refresh_requested)
        head.addWidget(self._refresh_button)
        left.addLayout(head)
        self._files = QListWidget()
        self._files.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._files.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._files.itemClicked.connect(self._on_file_clicked)
        enable_swipe(self._files)
        left.addWidget(self._files, 1)
        # Leerer Zustand (GUI-Review UX-7): Hinweis mittig statt leerer Liste mit Kleingedrucktem darunter
        self._empty_box = QWidget()
        empty = QVBoxLayout(self._empty_box)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setSpacing(10)
        self._empty_icon = QLabel()
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_title = QLabel()
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_title.setWordWrap(True)
        self._empty_text = QLabel()
        self._empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_text.setWordWrap(True)
        self._empty_refresh = IconButton("mdi.refresh", "", text=tr("Neu einlesen"))
        self._empty_refresh.clicked.connect(self.refresh_requested)
        for widget in (self._empty_icon, self._empty_title, self._empty_text):
            empty.addWidget(widget)
        empty.addWidget(self._empty_refresh, 0, Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self._empty_box, 1)
        self._folder_label = QLabel()
        self._folder_label.setWordWrap(True)
        left.addWidget(self._folder_label)
        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setMaximumWidth(300)
        outer.addWidget(left_box)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        outer.addWidget(line)

        # -- rechts: gewaehlter Ablauf ------------------------------------------------
        right = QVBoxLayout()
        self._name_label = QLabel()
        self._name_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        right.addWidget(self._name_label)
        self._name_label.setWordWrap(True)
        self._info_label = QLabel()
        self._info_label.setWordWrap(True)   # sonst 656 px breit bei 14-px-Schrift (Fenster > 1024 px)
        right.addWidget(self._info_label)
        self._steps = QListWidget()
        self._steps.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._steps.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._steps.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        enable_swipe(self._steps)
        right.addWidget(self._steps, 1)
        # Platzhalter ohne gewaehlten Ablauf: haelt Titel und Hinweis oben zusammen, Start/Stop unten
        self._filler = QWidget()
        right.addWidget(self._filler, 1)
        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m")
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        right.addWidget(self._progress)
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setMinimumHeight(48)
        right.addWidget(self._status_label)
        buttons = QHBoxLayout()
        # Mit Text anlegen: ohne Text setzt IconButton eine feste Symbol-Knopf-Groesse
        self._start_button = IconButton("mdi.play", "", text=tr("Start"))
        self._stop_button = IconButton("mdi.stop", "", text=tr("Stop"))
        for button in (self._start_button, self._stop_button):
            button.setIconSize(QSize(26, 26))
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            buttons.addWidget(button, 1)
        self._start_button.clicked.connect(self.start_requested)
        self._stop_button.clicked.connect(self.stop_requested)
        right.addLayout(buttons)
        outer.addLayout(right, 1)

        self._current_step = -1
        self._status_kind = "info"
        self._folder: Path | None = None
        self.set_test(None, [])
        self.set_running(False, False)
        ThemeManager.instance().changed.connect(lambda _pal: self._restyle())
        self.retranslate()

    # -- Inhalt ------------------------------------------------------------------

    def set_files(self, folder: Path, files: list[Path], selected: Path | None) -> None:
        self._folder = folder
        self._files.clear()
        for path in files:
            item = QListWidgetItem(path.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self._files.addItem(item)
            if selected is not None and path == selected:
                item.setSelected(True)
                self._files.setCurrentItem(item)
        self.retranslate()

    def set_test(self, name: str | None, step_lines: list[str]) -> None:
        self._name_label.setText(name or tr("Kein Ablauf gewählt"))
        self._steps.clear()
        for number, text in enumerate(step_lines, start=1):
            item = QListWidgetItem(f"{number}.  {text}")
            item.setSizeHint(QSize(0, 40))
            self._steps.addItem(item)
        self._info_label.setText(tr("{n} Schritte", n=len(step_lines)) if name else
                                 tr("Links einen Ablauf antippen. Bearbeitet und gespeichert wird am PC."))
        self._progress.setRange(0, max(1, len(step_lines)))
        self._progress.setValue(0)
        self._progress.setFormat("%v / %m" if name else "")
        self._current_step = -1
        # Ohne gewaehlten Ablauf keine leeren Rahmen fuer Schritte/Fortschritt/Status (GUI-Review UX-7)
        for widget in (self._steps, self._progress, self._status_label):
            widget.setVisible(bool(name))
        self._filler.setVisible(not name)

    def set_running(self, running: bool, can_start: bool) -> None:
        self._start_button.setEnabled(can_start and not running)
        self._stop_button.setEnabled(running)
        self._files.setEnabled(not running)
        self._refresh_button.setEnabled(not running)
        self._empty_refresh.setEnabled(not running)
        self._restyle()

    def set_status(self, text: str, kind: str) -> None:
        self._status_kind = kind
        self._status_label.setText(text)
        self._restyle()

    def set_progress(self, index: int) -> None:
        """index: 0-basiert, gerade begonnener Schritt (-1: noch keiner)."""
        self._progress.setValue(max(0, index + 1))
        if 0 <= self._current_step < self._steps.count():
            self._steps.item(self._current_step).setBackground(QColor(Qt.GlobalColor.transparent))
        self._current_step = index
        if 0 <= index < self._steps.count():
            item = self._steps.item(index)
            item.setBackground(QColor(current_palette().selection))
            self._steps.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    # -- Anzeige -------------------------------------------------------------------

    def retranslate(self) -> None:
        self._list_title.setText(tr("Testabläufe"))
        self._refresh_button.setToolTip(tr("Liste neu einlesen"))
        self._start_button.setText(tr("Start"))
        self._stop_button.setText(tr("Stop"))
        empty = self._files.count() == 0
        self._files.setVisible(not empty)
        self._empty_box.setVisible(empty)
        self._folder_label.setVisible(not empty)
        self._empty_title.setText(tr("Noch keine Testabläufe"))
        # Umbruchstellen hinter den Pfadtrennern: ein langer Pfad ohne Leerzeichen erzwaenge sonst seine
        # volle Breite (Fenster wurde breiter als 1024 px)
        breakable = re.sub(r"([\\/])", "\\1\u200b", str(self._folder or ""))
        self._empty_text.setText(tr("Abläufe am PC erstellen und in diesem Ordner speichern:\n{folder}",
                                    folder=breakable))
        self._empty_refresh.setText(tr("Neu einlesen"))
        self._folder_label.setText(str(self._folder or ""))
        self._restyle()

    def _restyle(self) -> None:
        pal = current_palette()
        self._folder_label.setStyleSheet(f"color: {pal.text_muted};")
        self._info_label.setStyleSheet(f"color: {pal.text_muted};")
        self._empty_icon.setPixmap(qta.icon("mdi.playlist-remove", color=pal.text_muted).pixmap(48, 48))
        self._empty_title.setStyleSheet("font-size: 17px; font-weight: bold;")
        self._empty_text.setStyleSheet(f"color: {pal.text_muted};")
        color = {"error": pal.danger, "success": pal.success}.get(self._status_kind, pal.text)
        self._status_label.setStyleSheet(f"color: {color}; font-size: 17px; font-weight: bold;")
        self._start_button.setStyleSheet(
            f"QPushButton {{ background-color: {pal.accent}; color: {pal.on_accent}; font-weight: bold;"
            f" font-size: {RUN_BUTTON_FONT_PX}px; }}"
            f"QPushButton:disabled {{ background-color: {pal.surface_alt}; color: {pal.text_muted}; }}")
        self._start_button.set_color_override(pal.on_accent if self._start_button.isEnabled() else None)
        self._stop_button.setStyleSheet(
            f"QPushButton {{ background-color: {pal.danger}; color: {pal.on_status}; font-weight: bold;"
            f" font-size: {RUN_BUTTON_FONT_PX}px; }}"
            f"QPushButton:disabled {{ background-color: {pal.surface_alt}; color: {pal.text_muted}; }}")
        self._stop_button.set_color_override(pal.on_status if self._stop_button.isEnabled() else None)
        if 0 <= self._current_step < self._steps.count():
            self._steps.item(self._current_step).setBackground(QColor(pal.selection))

    def _on_file_clicked(self, item: QListWidgetItem) -> None:
        self.file_selected.emit(Path(item.data(Qt.ItemDataRole.UserRole)))
