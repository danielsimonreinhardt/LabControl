"""Dashboard mit aktuellen Messwerten aller verbundenen Geraete.

Zeigt pro verbundener Geraete-Instanz ein Panel fester Breite. Ein Panel
erscheint erst, wenn das zugehoerige Geraet tatsaechlich verbunden ist, und
verschwindet wieder, sobald es getrennt wird -- bleibt aber (versteckt) im
Speicher, damit ein Wiederverbinden ohne Zustandsverlust/Flackern moeglich
ist. Bei mehreren baugleichen Geraeten (z.B. zwei Netzteilen) bekommt jedes
sein eigenes Panel mit eindeutigem, umbenennbarem Label.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

VALUE_STYLE = "font-size: 20px; font-weight: bold;"
PANEL_WIDTH = 220
PANEL_MAX_HEIGHT = 170

LOAD_FIELDS = ["Spannung (V)", "Strom (A)", "Leistung (W)"]
PSU_FIELDS = ["Spannung (V)", "Strom (A)", "Modus"]
KIND_TITLE = {"load": "Elektronische Last", "psu": "Labornetzteil"}


class _DevicePanel(QGroupBox):
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label

    def __init__(self, kind: str, device_id: str, label: str, fields: list[str]) -> None:
        super().__init__()
        self._kind = kind
        self._device_id = device_id
        self.setFixedWidth(PANEL_WIDTH)
        self.setMaximumHeight(PANEL_MAX_HEIGHT)

        outer = QVBoxLayout(self)

        header = QHBoxLayout()
        self._title_label = QLabel(label)
        self._title_label.setStyleSheet("font-weight: bold;")
        rename_button = QPushButton("✎")
        rename_button.setFixedWidth(28)
        rename_button.setToolTip("Gerät umbenennen")
        rename_button.clicked.connect(self._on_rename_clicked)
        header.addWidget(self._title_label, 1)
        header.addWidget(rename_button)
        outer.addLayout(header)

        subtitle = QLabel(KIND_TITLE.get(kind, kind))
        subtitle.setStyleSheet("color: gray;")
        outer.addWidget(subtitle)

        form = QFormLayout()
        self._value_labels: dict[str, QLabel] = {}
        for field_name in fields:
            value_label = QLabel("--")
            value_label.setStyleSheet(VALUE_STYLE)
            self._value_labels[field_name] = value_label
            form.addRow(field_name + ":", value_label)
        outer.addLayout(form)

    def set_label(self, label: str) -> None:
        self._title_label.setText(label)

    def set_value(self, field: str, text: str) -> None:
        self._value_labels[field].setText(text)

    def clear_values(self) -> None:
        for value_label in self._value_labels.values():
            value_label.setText("--")

    def _on_rename_clicked(self) -> None:
        new_label, ok = QInputDialog.getText(
            self, "Gerät umbenennen", "Name:", text=self._title_label.text()
        )
        if ok and new_label.strip():
            self.rename_requested.emit(self._kind, self._device_id, new_label.strip())


class DashboardWidget(QGroupBox):
    rename_requested = Signal(str, str, str)  # kind, device_id, new_label

    def __init__(self) -> None:
        super().__init__("Dashboard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        container = QWidget()
        self._panel_layout = QHBoxLayout(container)
        self._panel_layout.addStretch()

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(container)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        scroll_area.setMaximumHeight(PANEL_MAX_HEIGHT + 12)
        outer.addWidget(scroll_area)

        self._panels: dict[str, _DevicePanel] = {}

    # -- Geraete-Lebenszyklus --------------------------------------------------

    @Slot(str, str, str)
    def on_device_known(self, kind: str, device_id: str, label: str) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            fields = LOAD_FIELDS if kind == "load" else PSU_FIELDS
            panel = _DevicePanel(kind, device_id, label, fields)
            panel.rename_requested.connect(self.rename_requested)
            panel.hide()
            self._panel_layout.insertWidget(self._panel_layout.count() - 1, panel)
            self._panels[device_id] = panel
        else:
            panel.set_label(label)

    @Slot(str, str, str)
    def on_label_changed(self, kind: str, device_id: str, label: str) -> None:
        panel = self._panels.get(device_id)
        if panel is not None:
            panel.set_label(label)

    @Slot(str, bool)
    def set_load_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    @Slot(str, bool)
    def set_psu_online(self, device_id: str, online: bool) -> None:
        self._set_online(device_id, online)

    def _set_online(self, device_id: str, online: bool) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.setVisible(online)
        if not online:
            panel.clear_values()

    # -- Messwerte -----------------------------------------------------------

    @Slot(str, float, float, float)
    def update_load(self, device_id: str, voltage: float, current: float, power: float) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.set_value("Spannung (V)", f"{voltage:.3f}")
        panel.set_value("Strom (A)", f"{current:.3f}")
        panel.set_value("Leistung (W)", f"{power:.3f}")

    @Slot(str, float, float, bool)
    def update_psu(self, device_id: str, voltage: float, current: float, constant_current: bool) -> None:
        panel = self._panels.get(device_id)
        if panel is None:
            return
        panel.set_value("Spannung (V)", f"{voltage:.2f}")
        panel.set_value("Strom (A)", f"{current:.2f}")
        panel.set_value("Modus", "CC" if constant_current else "CV")
