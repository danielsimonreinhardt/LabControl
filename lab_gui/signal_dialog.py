"""Popup-Dialog zur Definition eines Arbiträrsignal-Testschritts.

Zeigt eine kleine "Oszilloskop"-Vorschau: da HCS34xx/KEL102 keine echte
Signalgenerator-Hardware sind, wird das Signal in Wirklichkeit als Folge
einzelner Sollwert-Kommandos im Abstand von `arb_interval_ms` gesendet. Die
Vorschau zeichnet deshalb bewusst eine Treppenkurve aus genau diesen
Stützstellen statt einer glatten Kurve, damit sichtbar bleibt, wie grob die
Annäherung bei der gewählten Frequenz/Intervall-Kombination tatsächlich ist.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from testcase_model import ACTION_VALUE_RANGE, ARB_TARGETS, action_label, arb_value
from theme import current as current_palette

SHAPES = {"Sinus": "sine", "Rechteck": "square"}
PREVIEW_PERIODS = 3.0
MIN_SAMPLES_PER_PERIOD_WARN = 8


class _ScopePreview(QWidget):
    """Minimalistische Oszilloskop-artige Vorschau (Stützstellen, kein Zeichnen-Toolkit)."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(140)
        self._params: dict = {}

    def set_params(self, shape: str, target: str, amplitude: float, offset: float, frequency: float, interval_ms: int) -> None:
        self._params = dict(
            shape=shape, target=target, amplitude=amplitude,
            offset=offset, frequency=frequency, interval_ms=interval_ms,
        )
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(6, 6, -6, -6)
        pal = current_palette()
        painter.fillRect(self.rect(), QColor(pal.plot_bg))

        if not self._params or rect.width() <= 0 or rect.height() <= 0:
            return

        shape = self._params["shape"]
        target = self._params["target"]
        amplitude = self._params["amplitude"]
        offset = self._params["offset"]
        frequency = max(self._params["frequency"], 1e-6)
        interval_ms = max(self._params["interval_ms"], 1)

        _, lo, hi = ACTION_VALUE_RANGE.get(target, ("", 0.0, 0.0))
        span_lo = min(lo, offset - amplitude)
        span_hi = max(hi, offset + amplitude)
        if span_hi <= span_lo:
            span_hi = span_lo + 1.0

        def y_of(value: float) -> float:
            frac = (value - span_lo) / (span_hi - span_lo)
            return rect.bottom() - frac * rect.height()

        # Gitter
        grid_pen = QPen(QColor(pal.plot_grid))
        painter.setPen(grid_pen)
        for i in range(5):
            y = rect.top() + i * rect.height() / 4
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))

        # Zulaessiger Geraete-Wertebereich als gestrichelte Referenzlinien
        if lo < hi:
            ref_pen = QPen(QColor(pal.plot_ref))
            ref_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(ref_pen)
            painter.drawLine(rect.left(), int(y_of(lo)), rect.right(), int(y_of(lo)))
            painter.drawLine(rect.left(), int(y_of(hi)), rect.right(), int(y_of(hi)))

        window_s = PREVIEW_PERIODS / frequency
        step_s = interval_ms / 1000.0
        sample_count = max(2, int(window_s / step_s) + 1)

        class _Step:
            arb_shape = shape
            arb_target = target
            arb_amplitude = amplitude
            arb_offset = offset
            arb_frequency = frequency

        points = []
        for i in range(sample_count):
            t = i * step_s
            if t > window_s:
                break
            value = arb_value(_Step(), t)
            x = rect.left() + (t / window_s) * rect.width()
            points.append((x, y_of(value)))

        signal_pen = QPen(QColor(pal.plot_signal))
        signal_pen.setWidth(2)
        painter.setPen(signal_pen)
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            # Halte-Kurve (Treppe): erst waagerecht, dann senkrecht -- so wie
            # ein DAC/Sollwert-Kommando den Wert bis zum naechsten Update haelt.
            painter.drawLine(int(x0), int(y0), int(x1), int(y0))
            painter.drawLine(int(x1), int(y0), int(x1), int(y1))
        dot_pen = QPen(QColor(pal.plot_signal))
        dot_pen.setWidth(5)
        painter.setPen(dot_pen)
        for x, y in points:
            painter.drawPoint(int(x), int(y))


class SignalDialog(QDialog):
    def __init__(self, device_kind: str, step_duration: float, params: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Arbiträrsignal definieren")
        self._device_kind = device_kind

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._shape_combo = QComboBox()
        self._shape_combo.addItems(SHAPES.keys())

        self._target_combo = QComboBox()
        for code in ARB_TARGETS[device_kind]:
            self._target_combo.addItem(action_label(device_kind, code), code)

        self._amplitude_spin = QDoubleSpinBox()
        self._amplitude_spin.setDecimals(3)

        self._offset_spin = QDoubleSpinBox()
        self._offset_spin.setDecimals(3)

        self._frequency_spin = QDoubleSpinBox()
        self._frequency_spin.setDecimals(3)
        self._frequency_spin.setRange(0.001, 50.0)
        self._frequency_spin.setSuffix(" Hz")
        self._frequency_spin.setValue(1.0)

        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(50, 5000)
        self._interval_spin.setSingleStep(50)
        self._interval_spin.setSuffix(" ms")
        self._interval_spin.setValue(200)
        self._interval_spin.setToolTip(
            "Abstand zwischen zwei Sollwert-Updates. Die Geräte sind keine echten\n"
            "Signalgeneratoren -- das Signal wird als Folge einzelner Kommandos über\n"
            "die serielle Schnittstelle angenähert. Werte unter ~100 ms können bei\n"
            "manchen Geräten (v.a. dem Netzteil) nicht zuverlässig eingehalten werden."
        )

        form.addRow("Signalform:", self._shape_combo)
        form.addRow("Zielgröße:", self._target_combo)
        form.addRow("Amplitude (±):", self._amplitude_spin)
        form.addRow("Offset (Mitte):", self._offset_spin)
        form.addRow("Frequenz:", self._frequency_spin)
        form.addRow("Update-Intervall:", self._interval_spin)
        layout.addLayout(form)

        duration_label = QLabel(
            f"Signal-Dauer: {step_duration:g} s (siehe Spalte „Dauer (s)“ in der Testschritt-Zeile)"
        )
        duration_label.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(duration_label)

        layout.addWidget(QLabel("Vorschau (Oszilloskop):"))
        self._preview = _ScopePreview()
        layout.addWidget(self._preview)

        self._warning_label = QLabel("")
        self._warning_label.setStyleSheet(f"color: {current_palette().warning};")
        self._warning_label.setWordWrap(True)
        layout.addWidget(self._warning_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._shape_combo.currentTextChanged.connect(self._update_preview)
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        self._amplitude_spin.valueChanged.connect(self._update_preview)
        self._offset_spin.valueChanged.connect(self._update_preview)
        self._frequency_spin.valueChanged.connect(self._update_preview)
        self._interval_spin.valueChanged.connect(self._update_preview)

        self._load_params(params)
        self._on_target_changed()

    def _load_params(self, params: dict) -> None:
        self._pending_amplitude: float | None = params.get("amplitude", 0.0)
        self._pending_offset: float | None = params.get("offset", 0.0)
        shape_label = next((label for label, code in SHAPES.items() if code == params.get("shape")), "Sinus")
        self._shape_combo.setCurrentText(shape_label)
        target_index = self._target_combo.findData(params.get("target", ARB_TARGETS[self._device_kind][0]))
        self._target_combo.setCurrentIndex(max(target_index, 0))
        self._frequency_spin.setValue(params.get("frequency", 1.0))
        self._interval_spin.setValue(int(params.get("interval_ms", 200)))

    def _on_target_changed(self) -> None:
        code = self._target_combo.currentData()
        unit, lo, hi = ACTION_VALUE_RANGE.get(code, ("", 0.0, 100.0))
        suffix = f" {unit}" if unit else ""
        self._offset_spin.setSuffix(suffix)
        self._offset_spin.setRange(lo, hi)
        self._amplitude_spin.setSuffix(suffix)
        self._amplitude_spin.setRange(0, max(hi - lo, 0.0))

        # Beim ersten Aufbau (aus _load_params) die uebergebenen Werte
        # uebernehmen, statt sie durch das Setzen der Range zu verlieren.
        # Bei jedem weiteren Zielgroessen-Wechsel (_pending_* bereits
        # konsumiert/None) stattdessen die aktuell angezeigten Werte behalten.
        offset = self._pending_offset if self._pending_offset is not None else self._offset_spin.value()
        amplitude = self._pending_amplitude if self._pending_amplitude is not None else self._amplitude_spin.value()
        self._pending_offset = None
        self._pending_amplitude = None
        self._offset_spin.setValue(min(max(offset, lo), hi))
        self._amplitude_spin.setValue(min(max(amplitude, 0.0), max(hi - lo, 0.0)))
        self._update_preview()

    def _update_preview(self) -> None:
        shape = SHAPES[self._shape_combo.currentText()]
        target = self._target_combo.currentData()
        amplitude = self._amplitude_spin.value()
        offset = self._offset_spin.value()
        frequency = self._frequency_spin.value()
        interval_ms = self._interval_spin.value()
        self._preview.set_params(shape, target, amplitude, offset, frequency, interval_ms)

        samples_per_period = (1000.0 / interval_ms) / frequency
        if samples_per_period < MIN_SAMPLES_PER_PERIOD_WARN:
            self._warning_label.setText(
                f"Nur ~{samples_per_period:.1f} Stützstellen pro Periode -- das Signal wird stufig/kantig "
                "ausgegeben. Für eine glattere Annäherung Frequenz verringern oder Update-Intervall verkleinern."
            )
        else:
            self._warning_label.setText("")

    def params(self) -> dict:
        return dict(
            shape=SHAPES[self._shape_combo.currentText()],
            target=self._target_combo.currentData(),
            amplitude=self._amplitude_spin.value(),
            offset=self._offset_spin.value(),
            frequency=self._frequency_spin.value(),
            interval_ms=self._interval_spin.value(),
        )
