"""Immer sichtbares Dashboard mit den aktuellen Messwerten beider Geraete."""
from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel

VALUE_STYLE = "font-size: 22px; font-weight: bold;"
CAPTION_STYLE = "color: gray;"


def _value_cell(caption: str) -> tuple[QFrame, QLabel]:
    frame = QFrame()
    layout = QGridLayout(frame)
    layout.setContentsMargins(4, 4, 4, 4)
    caption_label = QLabel(caption)
    caption_label.setStyleSheet(CAPTION_STYLE)
    value_label = QLabel("--")
    value_label.setStyleSheet(VALUE_STYLE)
    layout.addWidget(caption_label, 0, 0)
    layout.addWidget(value_label, 1, 0)
    return frame, value_label


class DashboardWidget(QGroupBox):
    def __init__(self) -> None:
        super().__init__("Dashboard")
        layout = QHBoxLayout(self)

        load_box = QGroupBox("Elektronische Last (KEL102)")
        load_layout = QHBoxLayout(load_box)
        v_frame, self._load_voltage = _value_cell("Spannung (V)")
        a_frame, self._load_current = _value_cell("Strom (A)")
        w_frame, self._load_power = _value_cell("Leistung (W)")
        load_layout.addWidget(v_frame)
        load_layout.addWidget(a_frame)
        load_layout.addWidget(w_frame)

        psu_box = QGroupBox("Labornetzteil (HCS-34xx)")
        psu_layout = QHBoxLayout(psu_box)
        pv_frame, self._psu_voltage = _value_cell("Spannung (V)")
        pa_frame, self._psu_current = _value_cell("Strom (A)")
        pm_frame, self._psu_mode = _value_cell("Modus")
        psu_layout.addWidget(pv_frame)
        psu_layout.addWidget(pa_frame)
        psu_layout.addWidget(pm_frame)

        layout.addWidget(load_box, 1)
        layout.addWidget(psu_box, 1)

    @Slot(float, float, float)
    def update_load(self, voltage: float, current: float, power: float) -> None:
        self._load_voltage.setText(f"{voltage:.3f}")
        self._load_current.setText(f"{current:.3f}")
        self._load_power.setText(f"{power:.3f}")

    @Slot(float, float, bool)
    def update_psu(self, voltage: float, current: float, constant_current: bool) -> None:
        self._psu_voltage.setText(f"{voltage:.2f}")
        self._psu_current.setText(f"{current:.2f}")
        self._psu_mode.setText("CC" if constant_current else "CV")

    @Slot(bool)
    def set_load_online(self, online: bool) -> None:
        if not online:
            self._load_voltage.setText("--")
            self._load_current.setText("--")
            self._load_power.setText("--")

    @Slot(bool)
    def set_psu_online(self, online: bool) -> None:
        if not online:
            self._psu_voltage.setText("--")
            self._psu_current.setText("--")
            self._psu_mode.setText("--")
