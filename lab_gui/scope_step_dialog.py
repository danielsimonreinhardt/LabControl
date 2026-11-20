"""Dialog der Oszilloskop-Ablaufschritte (1.0.1, PI-72 a, scope_steps.py): "Scharf schalten" mit Kanal (= Quelle),
Bereich, Tastkopf, Kopplung, Flanke, Pegel, Fenster und Vortrigger; "Auf Erfassung warten" mit Hoechstzeit,
Kennwert und Kanal."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout

import scope_steps
from i18n import tr
from scope_api.analysis import MEASUREMENTS
from step_spinbox import SteppedDoubleSpinBox


class ScopeStepDialog(QDialog):
    def __init__(self, action: str, kind: str, params: dict, parent=None) -> None:
        super().__init__(parent)
        self._action = action
        self._kind = kind
        self._extra = {k: v for k, v in (params or {}).items() if k == "refs"}
        p = scope_steps.clean_params(action, params, kind)
        self.setWindowTitle(tr("Scharf schalten") if action == scope_steps.SCOPE_ARM else tr("Auf Erfassung warten"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        channels = scope_steps.channels_for(kind)
        if action == scope_steps.SCOPE_ARM:
            self.channel = QComboBox()
            for name in channels:
                self.channel.addItem(name, name)
            self.channel.setCurrentIndex(max(0, self.channel.findData(p["channel"])))
            self.channel.setToolTip(tr("Erfasster Kanal, zugleich Trigger-Quelle"))
            self.range_v = _spin(0.001, 400.0, 3, p["range_v"], " V", 0.1, 1.0)
            self.range_v.setPrefix("± ")
            self.range_v.setToolTip(tr("Vollausschlag an der Tastkopfspitze; das Oszilloskop nimmt den nächstgrößeren "
                                       "Bereich"))
            self.probe = QComboBox()
            for factor in (1.0, 10.0, 100.0):
                self.probe.addItem(f"{factor:g}:1", factor)
            index = self.probe.findData(p["probe"])
            if index < 0:
                self.probe.addItem(f"{p['probe']:g}:1", p["probe"])
                index = self.probe.count() - 1
            self.probe.setCurrentIndex(index)
            self.coupling = QComboBox()
            for name in scope_steps.COUPLINGS:
                self.coupling.addItem(name, name)
            self.coupling.setCurrentIndex(max(0, self.coupling.findData(p["coupling"])))
            self.edge = QComboBox()
            for name in scope_steps.EDGES:
                self.edge.addItem(f"{scope_steps.EDGE_SYMBOLS[name]} {tr(scope_steps.EDGE_LABELS[name])}", name)
            self.edge.setCurrentIndex(max(0, self.edge.findData(p["edge"])))
            self.level_v = _spin(-400.0, 400.0, 3, p["level_v"], " V", 0.1, 1.0)
            self.duration_ms = _spin(0.000_01, 100_000.0, 5, p["duration_s"] * 1000.0, " ms", 0.1, 10.0)
            self.duration_ms.setToolTip(tr("Länge der Erfassung (Zeitbasis wählt das Oszilloskop passend)"))
            self.pre_pct = _spin(0.0, 100.0, 0, p["pre_trigger_pct"], " %", 5.0, 25.0)
            self.pre_pct.setToolTip(tr("Anteil der Erfassung vor dem Trigger"))
            form.addRow(tr("Kanal (Quelle):"), self.channel)
            form.addRow(tr("Bereich:"), self.range_v)
            form.addRow(tr("Tastkopf:"), self.probe)
            form.addRow(tr("Kopplung:"), self.coupling)
            form.addRow(tr("Flanke:"), self.edge)
            form.addRow(tr("Pegel:"), self.level_v)
            form.addRow(tr("Fenster:"), self.duration_ms)
            form.addRow(tr("Vortrigger:"), self.pre_pct)
        else:
            self.timeout_s = _spin(0.1, scope_steps.MAX_WAIT_S, 1, p["timeout_s"], " s", 1.0, 10.0)
            self.timeout_s.setToolTip(tr("Kein Trigger bis dahin: Schritt fehlgeschlagen"))
            self.measurement = QComboBox()
            for name in MEASUREMENTS:
                self.measurement.addItem(scope_steps.measurement_label(name), name)
            self.measurement.setCurrentIndex(max(0, self.measurement.findData(p["measurement"])))
            self.measurement.setToolTip(tr("Wert für Prüfung und Variable"))
            self.channel = QComboBox()
            self.channel.addItem(tr("wie scharf geschaltet"), "")
            for name in channels:
                self.channel.addItem(name, name)
            self.channel.setCurrentIndex(max(0, self.channel.findData(p["channel"])))
            form.addRow(tr("Höchstzeit:"), self.timeout_s)
            form.addRow(tr("Kennwert:"), self.measurement)
            form.addRow(tr("Kanal:"), self.channel)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def params(self) -> dict:
        if self._action == scope_steps.SCOPE_ARM:
            raw = {"channel": self.channel.currentData(), "range_v": self.range_v.value(),
                   "probe": float(self.probe.currentData()), "coupling": self.coupling.currentData(),
                   "edge": self.edge.currentData(), "level_v": self.level_v.value(),
                   "duration_s": self.duration_ms.value() / 1000.0, "pre_trigger_pct": self.pre_pct.value()}
        else:
            raw = {"timeout_s": self.timeout_s.value(), "measurement": self.measurement.currentData(),
                   "channel": self.channel.currentData()}
        return scope_steps.clean_params(self._action, {**raw, **self._extra}, self._kind)


def _spin(lo: float, hi: float, decimals: int, value: float, suffix: str, small: float, large: float):
    spin = SteppedDoubleSpinBox(small_step=small, large_step=large)
    spin.setDecimals(decimals)
    spin.setRange(lo, hi)
    spin.setSuffix(suffix)
    spin.setValue(value)
    return spin
