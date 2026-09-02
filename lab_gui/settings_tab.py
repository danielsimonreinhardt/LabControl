"""Settings-Reiter: Simulationsmodus fuer Debugging ohne Hardware, Dark Mode,
Sprache, geraete-individuelle Sicherheits-Grenzwerte (Watchdog, siehe
safety.py). Jedes verbundene/bekannte Geraet bekommt eine eigene
Grenzwert-Sektion (analog zu control_tab.py: eine Sektion pro Geraete-ID),
statt einer gemeinsamen Einstellung je Geraeteart."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from help_dialog import HelpDialog
from i18n import AVAILABLE_LANGUAGES, Translator, tr
from icons import IconButton
from paths import IS_FROZEN
from safety import SAFETY_LIMIT_FIELDS
from step_spinbox import SteppedDoubleSpinBox
from theme import current as current_palette

# field -> deutscher Basis-Anzeigename (Uebersetzungsschluessel), analog zu
# testcase_model.COND_FIELD_LABELS.
_FIELD_LABELS = {
    "max_voltage": "max. Spannung",
    "max_current": "max. Strom",
    "max_power": "max. Leistung",
}


def _separator() -> QFrame:
    """Duenne horizontale Trennlinie zwischen den thematischen Gruppen im
    Einstellungen-Tab (Darstellung / Hilfe / Geraeteverwaltung / Sicherheit,
    siehe SettingsTab.__init__). Fixe Farbe aus der beim Erzeugen aktuellen
    Palette statt eines nativen QFrame-Rahmens -- analog zu _hint/
    _safety_hint (siehe dort) faerbt SettingsTab bislang nichts bei einem
    spaeteren Theme-Wechsel nach, das gilt fuer diese Linien ebenso."""
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {current_palette().border};")
    return line


def _button_row(button: QPushButton) -> QHBoxLayout:
    """Haelt einen QPushButton auf seiner natuerlichen Inhaltsbreite statt
    ihn (QPushButton-Standardverhalten in einem QVBoxLayout, horizontale
    SizePolicy "Minimum" laesst ihn wachsen) auf die volle Tab-Breite zu
    strecken -- analog zur bereits bestehenden language_row."""
    row = QHBoxLayout()
    row.addWidget(button)
    row.addStretch()
    return row


class _DeviceSafetyGroup(QGroupBox):
    """Grenzwert-Sektion fuer EIN Geraet (siehe SettingsTab.on_device_known)."""

    limit_changed = Signal(str, bool, float)  # field, enabled, value

    def __init__(self, kind: str, label: str) -> None:
        super().__init__()
        self._kind = kind
        self.setTitle(label)
        form = QFormLayout(self)
        # Default-Policy (AllNonFixedFieldsGrow) laesst die Feld-Spalte
        # (Checkbox+Spinbox) auf die volle verfuegbare Breite wachsen --
        # dadurch spannte sich das ganze Panel unnoetig ueber die komplette
        # Tab-Breite auf (BUGS.md #11), obwohl der Inhalt viel schmaler waere.
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        # field -> (Checkbox, Spinbox, Zeilen-Label) fuer
        # set_limits()/_on_field_changed().
        self._widgets: dict[str, tuple[QCheckBox, QDoubleSpinBox]] = {}
        self._row_labels: dict[str, QLabel] = {}
        for field, unit, lo, hi, _default in SAFETY_LIMIT_FIELDS.get(kind, []):
            checkbox = QCheckBox()
            spin = SteppedDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setDecimals(2)
            spin.setSuffix(f" {unit}" if unit else "")
            spin.setEnabled(False)
            checkbox.toggled.connect(spin.setEnabled)
            checkbox.toggled.connect(lambda _enabled, f=field: self._on_field_changed(f))
            spin.valueChanged.connect(lambda _value, f=field: self._on_field_changed(f))
            row = QHBoxLayout()
            row.addWidget(checkbox)
            row.addWidget(spin)
            row_label = QLabel()
            form.addRow(row_label, row)
            self._widgets[field] = (checkbox, spin)
            self._row_labels[field] = row_label

        self.retranslate()

    def retranslate(self) -> None:
        for field, row_label in self._row_labels.items():
            row_label.setText(tr(_FIELD_LABELS.get(field, field)))

    def set_label(self, label: str) -> None:
        self.setTitle(label)

    def set_limits(self, limits: dict) -> None:
        for field, (checkbox, spin) in self._widgets.items():
            entry = limits.get(field, {"enabled": False, "value": spin.value()})
            checkbox.blockSignals(True)
            spin.blockSignals(True)
            checkbox.setChecked(entry["enabled"])
            spin.setValue(entry["value"])
            spin.setEnabled(entry["enabled"])
            checkbox.blockSignals(False)
            spin.blockSignals(False)

    def _on_field_changed(self, field: str) -> None:
        checkbox, spin = self._widgets[field]
        self.limit_changed.emit(field, checkbox.isChecked(), spin.value())


class SettingsTab(QWidget):
    simulation_mode_toggled = Signal(bool)
    dark_mode_toggled = Signal(bool)
    language_selected = Signal(str)
    safety_limit_changed = Signal(str, str, bool, float)  # device_id, field, enabled, value
    notifications_toggled = Signal(bool)
    panel_colors_toggled = Signal(bool)
    # Nutzer hat die Rueckfrage in _on_reset_devices_clicked bereits mit Ja
    # bestaetigt -- main_window._on_reset_devices_requested fuehrt den
    # eigentlichen Reset aus (DeviceRegistry/Settings kennt dieses Widget
    # nicht direkt).
    reset_devices_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        self._sim_checkbox = QCheckBox()
        self._sim_checkbox.toggled.connect(self.simulation_mode_toggled)
        layout.addWidget(self._sim_checkbox)

        self._hint = QLabel()
        self._hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._hint)

        # Simulationsmodus nur im Dev-Betrieb anbieten, in Release-.exe
        # komplett ausgeblendet (nicht nur deaktiviert) -- siehe FEATURES.md
        # Punkt 4. Der eigentliche Schutz liegt in Settings.simulation_mode
        # (paths.IS_FROZEN); das Ausblenden hier verhindert nur, dass die
        # nicht wirksame Option im Release-Build ueberhaupt sichtbar ist.
        # Die Trennlinie darunter wird aus demselben Grund mit ausgeblendet,
        # sonst stuende in der Release-.exe eine Linie ohne jeden Inhalt
        # darueber ganz oben im Tab.
        self._sim_separator = _separator()
        if IS_FROZEN:
            self._sim_checkbox.setVisible(False)
            self._hint.setVisible(False)
            self._sim_separator.setVisible(False)
        layout.addWidget(self._sim_separator)

        # -- Darstellung/Verhalten -------------------------------------------
        self._dark_checkbox = QCheckBox()
        self._dark_checkbox.toggled.connect(self.dark_mode_toggled)
        layout.addWidget(self._dark_checkbox)

        self._notify_checkbox = QCheckBox()
        self._notify_checkbox.toggled.connect(self.notifications_toggled)
        layout.addWidget(self._notify_checkbox)

        self._panel_colors_checkbox = QCheckBox()
        self._panel_colors_checkbox.toggled.connect(self.panel_colors_toggled)
        layout.addWidget(self._panel_colors_checkbox)

        language_row = QHBoxLayout()
        self._language_label = QLabel()
        language_row.addWidget(self._language_label)
        self._language_combo = QComboBox()
        for code, native_name in AVAILABLE_LANGUAGES.items():
            self._language_combo.addItem(native_name, code)
        self._language_combo.currentIndexChanged.connect(
            lambda index: self.language_selected.emit(self._language_combo.itemData(index))
        )
        language_row.addWidget(self._language_combo)
        language_row.addStretch()
        layout.addLayout(language_row)

        layout.addWidget(_separator())

        # -- Hilfe -------------------------------------------------------------
        self._help_button = IconButton("mdi.help-circle-outline", "", text=tr("Hilfe"))
        self._help_button.clicked.connect(self._on_help_clicked)
        layout.addLayout(_button_row(self._help_button))

        layout.addWidget(_separator())

        # -- Geraeteverwaltung ---------------------------------------------
        self._reset_devices_button = QPushButton()
        self._reset_devices_button.clicked.connect(self._on_reset_devices_clicked)
        layout.addLayout(_button_row(self._reset_devices_button))

        layout.addWidget(_separator())

        # -- Sicherheit (geraete-individuelle Grenzwerte) ---------------------
        self._safety_hint = QLabel()
        self._safety_hint.setWordWrap(True)
        self._safety_hint.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._safety_hint)

        # Ein eigenes Layout fuer die dynamisch je Geraet erzeugten
        # _DeviceSafetyGroup-Sektionen (siehe on_device_known), damit sie sich
        # gemeinsam vor dem abschliessenden addStretch() einreihen.
        self._safety_sections_layout = QVBoxLayout()
        layout.addLayout(self._safety_sections_layout)
        self._safety_sections: dict[str, _DeviceSafetyGroup] = {}
        # Umschliessendes Zeilen-Widget je Sektion (siehe on_device_known) --
        # ermoeglicht forget_device(), die komplette Zeile (Sektion + den
        # Stretch daneben) mit einem einzigen deleteLater() zu entfernen,
        # statt zusaetzlich das QHBoxLayout-Zeilenobjekt selbst verwalten zu
        # muessen.
        self._safety_section_rows: dict[str, QWidget] = {}

        layout.addStretch()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self._sim_checkbox.setText(tr("Simulationsmodus (simulierte Geräte statt Hardware)"))
        self._hint.setText(
            tr(
                "Im Simulationsmodus stehen ein virtuelles Labornetzteil und eine virtuelle\n"
                "elektronische Last im Dashboard/Control-Tab zur Verfuegung, um die GUI ohne\n"
                "angeschlossene Hardware zu testen."
            )
        )
        self._dark_checkbox.setText(tr("Dark Mode (Amber Industrial statt Modern Light)"))
        self._notify_checkbox.setText(tr("Desktop-Benachrichtigung bei Lauf-Ende/Fehler"))
        self._panel_colors_checkbox.setText(tr("Individuelle Panel-Hintergrundfarben (Dashboard/Control)"))
        self._language_label.setText(tr("Sprache:"))
        self._help_button.setText(tr("Hilfe"))
        self._help_button.setToolTip(tr("Öffnet das Benutzerhandbuch"))
        self._reset_devices_button.setText(tr("Gerätezuordnung löschen"))
        self._reset_devices_button.setToolTip(
            tr(
                "Löscht alle gespeicherten Geräte-Namen, Sicherheits-Grenzwerte und "
                "Panel-Farben und setzt sie auf die Standardwerte zurück."
            )
        )
        self._safety_hint.setText(
            tr(
                "Grenzwerte (Sicherheitsabschaltung) je Gerät -- bei Überschreitung werden "
                "alle Ausgänge sofort abgeschaltet (Netzteil: Strom auf 0 A)."
            )
        )
        for section in self._safety_sections.values():
            section.retranslate()

    def set_simulation_mode(self, enabled: bool) -> None:
        self._sim_checkbox.blockSignals(True)
        self._sim_checkbox.setChecked(enabled)
        self._sim_checkbox.blockSignals(False)

    def set_dark_mode(self, enabled: bool) -> None:
        self._dark_checkbox.blockSignals(True)
        self._dark_checkbox.setChecked(enabled)
        self._dark_checkbox.blockSignals(False)

    def set_notifications_enabled(self, enabled: bool) -> None:
        self._notify_checkbox.blockSignals(True)
        self._notify_checkbox.setChecked(enabled)
        self._notify_checkbox.blockSignals(False)

    def set_panel_colors_enabled(self, enabled: bool) -> None:
        self._panel_colors_checkbox.blockSignals(True)
        self._panel_colors_checkbox.setChecked(enabled)
        self._panel_colors_checkbox.blockSignals(False)

    def set_language(self, language: str) -> None:
        index = self._language_combo.findData(language)
        if index < 0:
            return
        self._language_combo.blockSignals(True)
        self._language_combo.setCurrentIndex(index)
        self._language_combo.blockSignals(False)

    # -- geraete-individuelle Sicherheits-Grenzwerte -------------------------

    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_label(label)
            return
        section = _DeviceSafetyGroup(kind, label)
        section.limit_changed.connect(
            lambda field, enabled, value, d=device_id: self.safety_limit_changed.emit(d, field, enabled, value)
        )
        # Zeile mit Stretch statt direktem addWidget() (siehe BUGS.md #11):
        # ein QGroupBox-Kind einer QVBoxLayout wird sonst auf die volle
        # verfuegbare Breite gestreckt, auch wenn form.setFieldGrowthPolicy
        # oben die Feld-Spalte selbst schon kompakt haelt. Als Wrapper-WIDGET
        # (nicht nur -Layout) angelegt, siehe _safety_section_rows.
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(section)
        row.addStretch(1)
        self._safety_sections_layout.addWidget(row_widget)
        self._safety_sections[device_id] = section
        self._safety_section_rows[device_id] = row_widget

    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_label(label)

    def forget_device(self, device_id: str) -> None:
        """Entfernt die Sicherheits-Grenzwert-Sektion eines Geraets
        vollstaendig -- nur fuer den "Geraetezuordnung loeschen"-Button
        (main_window._on_reset_devices_requested) gedacht, siehe
        dashboard.DashboardWidget.forget_device fuer die Begruendung."""
        self._safety_sections.pop(device_id, None)
        row_widget = self._safety_section_rows.pop(device_id, None)
        if row_widget is not None:
            row_widget.deleteLater()

    def set_device_safety_limits(self, device_id: str, limits: dict) -> None:
        section = self._safety_sections.get(device_id)
        if section is not None:
            section.set_limits(limits)

    def _on_help_clicked(self) -> None:
        HelpDialog(self).exec()

    def _on_reset_devices_clicked(self) -> None:
        if QMessageBox.question(
            self,
            tr("Gerätezuordnung löschen"),
            tr(
                "Alle gespeicherten Geräte-Namen, Sicherheits-Grenzwerte und Panel-Farben "
                "wirklich löschen und auf die Standardwerte zurücksetzen? Das lässt sich "
                "nicht rückgängig machen."
            ),
        ) != QMessageBox.StandardButton.Yes:
            return
        self.reset_devices_requested.emit()
