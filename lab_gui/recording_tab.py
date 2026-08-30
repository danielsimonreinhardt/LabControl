"""Aufzeichnung-Reiter: Start/Stop einer Messwert-Aufzeichnung ueber alle
bekannten Geraete sowie Export als CSV oder MF4.

Die eigentliche Sammlung uebernimmt Recorder (recording.py), an den dieser
Tab nur die Steuerung (Start/Stop/Zuruecksetzen) und die Statusanzeige
anbindet; der Export selbst laeuft synchron im GUI-Thread (recording_export.py)
-- fuer die hier erwarteten Datenmengen (Laborsitzung, Sekunden-Polling) im
Millisekundenbereich, ein Fortschrittsdialog waere unnoetiger Aufwand.

MainWindow ruft _on_export_csv/_on_export_mf4 fuer die eigentliche Arbeit auf
(Recorder haelt die Daten) und meldet Erfolg/Fehler ueber show_export_success/
show_export_error zurueck -- ein Fehler unterbricht per Modal-Dialog, ein
Erfolg blendet nur kurz die Statuszeile um (kein Modal, siehe
show_export_success).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from i18n import Translator, tr
from icons import IconButton
from paths import app_dir
from recording import Recorder
from theme import Palette, ThemeManager
from theme import current as current_palette

DEFAULT_DIR = app_dir() / "recordings"

DURATION_REFRESH_MS = 500


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class RecordingTab(QWidget):
    start_requested = Signal()
    stop_requested = Signal()
    clear_requested = Signal()
    export_csv_to = Signal(object)  # Path
    export_mf4_to = Signal(object)  # Path

    def __init__(self) -> None:
        super().__init__()
        self._sample_count = 0
        self._elapsed_s = 0.0
        self._is_recording = False
        self._known_device_count = 0

        layout = QVBoxLayout(self)

        self._status_label = QLabel()
        self._status_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._status_label)

        self._hint_label = QLabel()
        self._hint_label.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._hint_label)

        button_row = QHBoxLayout()
        self._start_button = IconButton("mdi.record-circle-outline", "", text=tr("Aufnahme starten"))
        self._stop_button = IconButton("mdi.stop-circle-outline", "", text=tr("Aufnahme stoppen"))
        self._clear_button = IconButton("mdi.delete-sweep-outline", "", text=tr("Zurücksetzen"))
        self._start_button.clicked.connect(self.start_requested.emit)
        self._stop_button.clicked.connect(self.stop_requested.emit)
        self._clear_button.clicked.connect(self._on_clear_clicked)
        button_row.addWidget(self._start_button)
        button_row.addWidget(self._stop_button)
        button_row.addWidget(self._clear_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        export_row = QHBoxLayout()
        self._export_csv_button = IconButton("mdi.file-delimited-outline", "", text=tr("Als CSV exportieren…"))
        self._export_mf4_button = IconButton("mdi.file-chart-outline", "", text=tr("Als MF4 exportieren…"))
        self._export_csv_button.clicked.connect(self._on_export_csv_clicked)
        self._export_mf4_button.clicked.connect(self._on_export_mf4_clicked)
        export_row.addWidget(self._export_csv_button)
        export_row.addWidget(self._export_mf4_button)
        export_row.addStretch()
        layout.addLayout(export_row)

        layout.addStretch()

        self._duration_timer = QTimer(self)
        self._duration_timer.timeout.connect(self._refresh_status_text)

        ThemeManager.instance().changed.connect(self._on_theme_changed)
        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()
        self._update_button_states()

    def _retranslate(self) -> None:
        self._hint_label.setText(
            tr(
                "Zeichnet Zeitstempel, Gerät und Messwert für alle bekannten Geräte auf, solange die\n"
                "Aufnahme läuft. Export ist auch bei laufender Aufnahme möglich (Zwischenstand)."
            )
        )
        self._start_button.setToolTip(tr("Aufnahme starten"))
        self._start_button.setText(tr("Aufnahme starten"))
        self._stop_button.setToolTip(tr("Aufnahme stoppen"))
        self._stop_button.setText(tr("Aufnahme stoppen"))
        self._clear_button.setToolTip(tr("Zurücksetzen"))
        self._clear_button.setText(tr("Zurücksetzen"))
        self._export_csv_button.setToolTip(tr("Als CSV exportieren…"))
        self._export_csv_button.setText(tr("Als CSV exportieren…"))
        self._export_mf4_button.setToolTip(tr("Als MF4 exportieren…"))
        self._export_mf4_button.setText(tr("Als MF4 exportieren…"))
        self._refresh_status_text()

    def _on_theme_changed(self, palette: Palette) -> None:
        self._hint_label.setStyleSheet(f"color: {palette.text_muted};")

    # -- Geraeteregistrierung (fuer den Hinweistext) --------------------------

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        self._known_device_count += 1
        self._refresh_status_text()

    # -- an Recorder-Signale gebunden (siehe main_window.py) -------------------

    def on_recording_changed(self, active: bool) -> None:
        self._is_recording = active
        if active:
            self._duration_timer.start(DURATION_REFRESH_MS)
        else:
            self._duration_timer.stop()
        self._update_button_states()
        self._refresh_status_text()

    def on_stats_changed(self, sample_count: int, elapsed_s: float) -> None:
        self._sample_count = sample_count
        self._elapsed_s = elapsed_s
        self._update_button_states()
        self._refresh_status_text()

    def _refresh_status_text(self) -> None:
        if self._is_recording:
            text = tr(
                "● Aufnahme läuft seit {duration} · {count} Werte ({devices} Geräte)",
                duration=_format_duration(self._elapsed_s),
                count=self._sample_count,
                devices=self._known_device_count,
            )
            self._status_label.setStyleSheet(f"color: {current_palette().danger}; font-weight: bold;")
        elif self._sample_count:
            text = tr(
                "Aufnahme gestoppt · {count} Werte über {duration} aufgezeichnet",
                count=self._sample_count,
                duration=_format_duration(self._elapsed_s),
            )
            self._status_label.setStyleSheet(f"color: {current_palette().text}; font-weight: bold;")
        else:
            text = tr("Keine Aufnahme aktiv")
            self._status_label.setStyleSheet(f"color: {current_palette().text_muted}; font-weight: bold;")
        self._status_label.setText(text)

    def _update_button_states(self) -> None:
        self._start_button.setEnabled(not self._is_recording)
        self._stop_button.setEnabled(self._is_recording)
        self._clear_button.setEnabled(not self._is_recording and self._sample_count > 0)
        has_samples = self._sample_count > 0
        self._export_csv_button.setEnabled(has_samples)
        self._export_mf4_button.setEnabled(has_samples)

    def _on_clear_clicked(self) -> None:
        if self._sample_count and QMessageBox.question(
            self,
            tr("Zurücksetzen"),
            tr("Aufgezeichnete Werte wirklich verwerfen?"),
        ) != QMessageBox.StandardButton.Yes:
            return
        self.clear_requested.emit()

    # -- Export ------------------------------------------------------------

    def _on_export_csv_clicked(self) -> None:
        DEFAULT_DIR.mkdir(exist_ok=True)
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("Als CSV exportieren"), str(DEFAULT_DIR), tr("CSV-Datei (*.csv)")
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
        self.export_csv_to.emit(path)

    def _on_export_mf4_clicked(self) -> None:
        DEFAULT_DIR.mkdir(exist_ok=True)
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("Als MF4 exportieren"), str(DEFAULT_DIR), tr("MF4-Datei (*.mf4)")
        )
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != ".mf4":
            path = path.with_suffix(".mf4")
        self.export_mf4_to.emit(path)

    def show_export_error(self, message: str) -> None:
        QMessageBox.critical(self, tr("Fehler beim Export"), message)

    def show_export_success(self, path: Path) -> None:
        # Bewusst kein Modal-Dialog (anders als der Fehlerfall) -- analog zum
        # Testablauf-Tab, der Speichern/Laden ebenfalls nur bei einem Fehler
        # unterbricht. Blendet die Statuszeile kurz um, dann zurueck zum
        # normalen Aufnahmestatus.
        self._status_label.setText(tr("Exportiert nach {name}", name=path.name))
        self._status_label.setStyleSheet(f"color: {current_palette().success}; font-weight: bold;")
        QTimer.singleShot(4000, self._refresh_status_text)
