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

from i18n import Translator, tr
from testcase_model import ACTION_VALUE_RANGE, ARB_TARGETS, action_label, arb_value
from theme import current as current_palette

# Interner Signalform-Code -> deutscher Basis-Anzeigename (Uebersetzungsschluessel).
SHAPE_BASE_LABELS = {"sine": "Sinus", "square": "Rechteck"}
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
    def __init__(
        self,
        device_kind: str,
        step_duration: float,
        params: dict,
        parent=None,
        limits: tuple[float, float] | None = None,
    ) -> None:
        """limits: bekannte (OVP, OCP) des Ziel-Netzteils, falls bekannt und
        device_kind == "psu" -- siehe testcase_tab._psu_limits. Werte, deren
        Spitzenwert (Offset+Amplitude) die jeweilige Schwelle ueberschreitet,
        werden vom Geraet kommentarlos ignoriert (siehe hcs34xx/driver.py);
        die Vorschau warnt in dem Fall statt den Nutzer erst beim Ausfuehren
        des Testablaufs scheitern zu lassen.
        """
        super().__init__(parent)
        self._device_kind = device_kind
        self._step_duration = step_duration
        self._limits = limits

        layout = QVBoxLayout(self)
        self._form = QFormLayout()

        self._shape_combo = QComboBox()
        self._populate_shape_combo()

        self._target_combo = QComboBox()
        self._populate_target_combo()

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

        self._form.addRow(" ", self._shape_combo)
        self._form.addRow(" ", self._target_combo)
        self._form.addRow(" ", self._amplitude_spin)
        self._form.addRow(" ", self._offset_spin)
        self._form.addRow(" ", self._frequency_spin)
        self._form.addRow(" ", self._interval_spin)
        layout.addLayout(self._form)

        self._duration_label = QLabel()
        self._duration_label.setStyleSheet(f"color: {current_palette().text_muted};")
        layout.addWidget(self._duration_label)

        self._preview_label = QLabel()
        layout.addWidget(self._preview_label)
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

        self._shape_combo.currentIndexChanged.connect(self._update_preview)
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        self._amplitude_spin.valueChanged.connect(self._update_preview)
        self._offset_spin.valueChanged.connect(self._update_preview)
        self._frequency_spin.valueChanged.connect(self._update_preview)
        self._interval_spin.valueChanged.connect(self._update_preview)

        self._load_params(params)
        self._on_target_changed()

        Translator.instance().language_changed.connect(self._retranslate)
        self._retranslate()

    def _populate_shape_combo(self) -> None:
        current_code = self._shape_combo.currentData() if self._shape_combo.count() else None
        self._shape_combo.blockSignals(True)
        self._shape_combo.clear()
        for code, base_label in SHAPE_BASE_LABELS.items():
            self._shape_combo.addItem(tr(base_label), code)
        index = self._shape_combo.findData(current_code) if current_code else 0
        self._shape_combo.setCurrentIndex(max(index, 0))
        self._shape_combo.blockSignals(False)

    def _populate_target_combo(self) -> None:
        current_code = self._target_combo.currentData() if self._target_combo.count() else None
        self._target_combo.blockSignals(True)
        self._target_combo.clear()
        for code in ARB_TARGETS[self._device_kind]:
            self._target_combo.addItem(action_label(self._device_kind, code), code)
        index = self._target_combo.findData(current_code) if current_code else 0
        self._target_combo.setCurrentIndex(max(index, 0))
        self._target_combo.blockSignals(False)

    def _retranslate(self) -> None:
        self.setWindowTitle(tr("Arbiträrsignal definieren"))
        self._populate_shape_combo()
        self._populate_target_combo()
        self._form.labelForField(self._shape_combo).setText(tr("Signalform:"))
        self._form.labelForField(self._target_combo).setText(tr("Zielgröße:"))
        self._form.labelForField(self._amplitude_spin).setText(tr("Amplitude (±):"))
        self._form.labelForField(self._offset_spin).setText(tr("Offset (Mitte):"))
        self._form.labelForField(self._frequency_spin).setText(tr("Frequenz:"))
        self._form.labelForField(self._interval_spin).setText(tr("Update-Intervall:"))
        self._interval_spin.setToolTip(
            tr(
                "Abstand zwischen zwei Sollwert-Updates. Die Geräte sind keine echten\n"
                "Signalgeneratoren -- das Signal wird als Folge einzelner Kommandos über\n"
                "die serielle Schnittstelle angenähert. Werte unter ~100 ms können bei\n"
                "manchen Geräten (v.a. dem Netzteil) nicht zuverlässig eingehalten werden."
            )
        )
        self._duration_label.setText(
            tr(
                "Signal-Dauer: {duration:g} s (siehe Spalte „Dauer (s)“ in der Testschritt-Zeile)",
                duration=self._step_duration,
            )
        )
        self._preview_label.setText(tr("Vorschau (Oszilloskop):"))
        self._update_preview()

    def _load_params(self, params: dict) -> None:
        self._pending_amplitude: float | None = params.get("amplitude", 0.0)
        self._pending_offset: float | None = params.get("offset", 0.0)
        shape_index = self._shape_combo.findData(params.get("shape", "sine"))
        self._shape_combo.setCurrentIndex(max(shape_index, 0))
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
        shape = self._shape_combo.currentData()
        target = self._target_combo.currentData()
        amplitude = self._amplitude_spin.value()
        offset = self._offset_spin.value()
        frequency = self._frequency_spin.value()
        interval_ms = self._interval_spin.value()
        self._preview.set_params(shape, target, amplitude, offset, frequency, interval_ms)

        warnings = []
        samples_per_period = (1000.0 / interval_ms) / frequency
        if samples_per_period < MIN_SAMPLES_PER_PERIOD_WARN:
            warnings.append(
                tr(
                    "Nur ~{samples:.1f} Stützstellen pro Periode -- das Signal wird stufig/kantig "
                    "ausgegeben. Für eine glattere Annäherung Frequenz verringern oder Update-Intervall verkleinern.",
                    samples=samples_per_period,
                )
            )

        if self._limits is not None and target in ("PSU_VOLT", "PSU_CURR"):
            ovp, ocp = self._limits
            threshold = ovp if target == "PSU_VOLT" else ocp
            label = "OVP" if target == "PSU_VOLT" else "OCP"
            unit = "V" if target == "PSU_VOLT" else "A"
            peak = offset + amplitude
            if peak > threshold:
                warnings.append(
                    tr(
                        "Spitzenwert ({peak:g}{unit}) liegt über der aktuellen {label}-Schwelle "
                        "({threshold:g}{unit}) -- diese Samples werden vom Netzteil kommentarlos "
                        "abgelehnt und brechen den Testschritt ab.",
                        peak=peak, unit=unit, label=label, threshold=threshold,
                    )
                )

        self._warning_label.setText(" ".join(warnings))

    def params(self) -> dict:
        return dict(
            shape=self._shape_combo.currentData(),
            target=self._target_combo.currentData(),
            amplitude=self._amplitude_spin.value(),
            offset=self._offset_spin.value(),
            frequency=self._frequency_spin.value(),
            interval_ms=self._interval_spin.value(),
        )
