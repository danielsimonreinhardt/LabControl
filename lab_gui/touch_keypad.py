"""Zahlen- und Hex-Tastenfeld fuer das Touch-Profil (Pi-Plan Phase 4 Schritt 2, Entscheidung PI-6 a).

Im Touch-Profil oeffnet ein Tipp auf ein Zahlenfeld (nicht auf dessen Pfeile) dieses Tastenfeld statt
der Bildschirmtastatur: oben Bezeichnung, Bereich und Einheit, darunter grosse Tasten. Ein Wert
ausserhalb des Bereichs laesst sich gar nicht erst setzen ("Setzen" gesperrt, Bereich rot).
"Setzen" uebernimmt den Wert ins Feld und loest -- wo es einen gibt -- den Uebernehmen-Knopf (✓)
derselben Zeile aus, sendet also wie bisher der ✓-Knopf.

Fuer Hex-Felder (CAN-ID mit Anzeige zur Basis 16, CAN-Nutzdaten) gibt es die Tasten A-F;
attach_hex_keypad() haengt das Hex-Feld an ein Textfeld (Nutzdaten "01 A2 FF").
Texte (Namen, Dateinamen) bleiben bei der Bildschirmtastatur des Systems.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from theme import ThemeManager
from theme import current as current_palette

KEY_SIZE = (84, 56)
APPLY_PARTNER = "_touch_apply_partner"   # Attribut am Zahlenfeld: Uebernehmen-Knopf derselben Zeile


def touch_active() -> bool:
    return ThemeManager.instance().touch


def set_apply_partner(spin: QAbstractSpinBox, button: QAbstractButton) -> None:
    """Merkt sich den Uebernehmen-Knopf, den "Setzen" im Tastenfeld ausloest (control_tab._row)."""
    setattr(spin, APPLY_PARTNER, button)


def field_title(widget: QWidget) -> str:
    """Bezeichnung des Felds fuer die Kopfzeile: Beschriftung im Formular, sonst Tooltip/Name."""
    node = widget
    while node is not None and node.parentWidget() is not None:
        layout = node.parentWidget().layout()
        if isinstance(layout, QFormLayout):
            label = layout.labelForField(node)
            if isinstance(label, QLabel) and label.text():
                return label.text().rstrip(":").strip()
            break
        node = node.parentWidget()
    return widget.accessibleName() or widget.toolTip() or tr("Wert")


class KeypadDialog(QDialog):
    """Tastenfeld. Zahl: 0-9, Komma (bei Nachkommastellen), ± (bei negativem Bereich).
    Hex: 0-9 und A-F; mit allow_space zusaetzlich Leerzeichen (Nutzdaten)."""

    def __init__(self, title: str, value_text: str, *, minimum: float | None = None, maximum: float | None = None,
                 decimals: int = 0, unit: str = "", hex_mode: bool = False, allow_space: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        # Ohne Fensterleiste (Nutzerwunsch 11.11.2026): Titel steht oben im Feld, "Abbruch" schliesst.
        # Stattdessen ein Rahmen, damit sich das Feld vom Fenster dahinter abhebt.
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("touchKeypad")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._min, self._max = minimum, maximum
        self._decimals = decimals
        self._unit = unit.strip()
        self._hex = hex_mode
        self._space = allow_space
        self._text = value_text
        self._fresh = True          # erster Tastendruck ersetzt den alten Wert
        pal = current_palette()
        self.setStyleSheet(f"QDialog#touchKeypad {{ background-color: {pal.bg}; border: 2px solid {pal.border};"
                           f" border-radius: 8px; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        head = QLabel(title)
        head.setStyleSheet("font-weight: bold;")
        outer.addWidget(head)
        self._range_label = QLabel(self._range_text())
        outer.addWidget(self._range_label)
        self._display = QLabel()
        self._display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._display.setMinimumHeight(64)
        self._display.setStyleSheet(
            f"font-size: 30px; font-weight: bold; padding: 4px 12px; background: {pal.surface};"
            f" border: 1px solid {pal.border}; border-radius: 6px;")
        outer.addWidget(self._display)

        grid = QGridLayout()
        grid.setSpacing(6)
        rows = [["7", "8", "9"], ["4", "5", "6"], ["1", "2", "3"]]
        last = ["0"]
        if hex_mode:
            rows = [["C", "D", "E", "F"], ["8", "9", "A", "B"], ["4", "5", "6", "7"], ["0", "1", "2", "3"]]
            last = [" "] if allow_space else []
        else:
            last += ["," if decimals > 0 else "", "±" if (minimum is not None and minimum < 0) else ""]
        for r, keys in enumerate(rows + [last]):
            for c, key in enumerate(keys):
                if key:
                    grid.addWidget(self._key(tr("Leerzeichen") if key == " " else key, lambda _=False, k=key: self._press(k)),
                                   r, c, 1, 2 if key == " " else 1)
        side = len(rows[0])
        self._clear_button = self._key(tr("Löschen"), self._clear)
        self._back_button = self._key("⌫", self._backspace)
        cancel = self._key(tr("Abbruch"), self.reject)
        self._set_button = self._key(tr("Setzen"), self.accept)
        self._set_button.setStyleSheet(
            f"QPushButton {{ background-color: {pal.accent}; color: {pal.surface}; font-weight: bold; }}"
            f"QPushButton:disabled {{ background-color: {pal.surface_alt}; color: {pal.text_muted}; }}")
        self._set_button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        grid.addWidget(self._clear_button, 0, side)
        grid.addWidget(self._back_button, 1, side)
        grid.addWidget(cancel, 2, side)
        grid.addWidget(self._set_button, 3, side, max(1, len(rows) + 1 - 3), 1)
        outer.addLayout(grid)
        self._refresh()

    def _key(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setMinimumSize(*KEY_SIZE)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(slot)
        return button

    # -- Eingabe ---------------------------------------------------------------

    def _press(self, key: str) -> None:
        if self._fresh and key != "±":
            self._text = ""
        self._fresh = False
        if key == "±":
            self._text = self._text[1:] if self._text.startswith("-") else "-" + self._text
        elif key == ",":
            if "," not in self._text:
                self._text = (self._text or "0") + ","
        elif self._hex:
            self._text += key
        else:
            if "," in self._text and len(self._text.split(",", 1)[1]) >= self._decimals:
                return   # mehr Nachkommastellen, als das Feld kann
            self._text += key
        self._refresh()

    def _backspace(self) -> None:
        self._fresh = False
        self._text = self._text[:-1]
        self._refresh()

    def _clear(self) -> None:
        self._fresh = False
        self._text = ""
        self._refresh()

    # -- Pruefen und Anzeige -----------------------------------------------------

    def text(self) -> str:
        return self._text

    def number(self) -> float | int | None:
        """Eingegebener Wert, None wenn leer/ungueltig."""
        text = self._text.strip()
        if not text or text in ("-", ","):
            return None
        try:
            if self._hex:
                return int(text.replace(" ", ""), 16)
            value = float(text.replace(",", "."))
        except ValueError:
            return None
        return value if self._decimals > 0 else int(round(value))

    def _valid(self) -> bool:
        if self._hex and self._space:
            parts = self._text.split()
            return all(len(p) <= 2 for p in parts) and len(parts) <= 8
        value = self.number()
        if value is None:
            return False
        return (self._min is None or value >= self._min - 1e-12) and (self._max is None or value <= self._max + 1e-12)

    def _range_text(self) -> str:
        if self._min is None or self._max is None:
            return tr("bis 8 Bytes, z. B. 01 A2 FF") if self._space else ""
        if self._hex:
            return tr("Bereich {low} – {high}", low=f"0x{int(self._min):X}", high=f"0x{int(self._max):X}")
        fmt = lambda v: f"{v:.{self._decimals}f}".replace(".", ",")  # noqa: E731
        unit = f" {self._unit}" if self._unit else ""
        return tr("Bereich {low} – {high}", low=fmt(self._min), high=fmt(self._max)) + unit

    def _refresh(self) -> None:
        unit = f" {self._unit}" if self._unit and not self._hex else ""
        self._display.setText((self._text or "0") + unit)
        ok = self._valid()
        self._set_button.setEnabled(ok)
        pal = current_palette()
        self._range_label.setStyleSheet(f"color: {pal.text_muted if ok or not self._text else pal.danger};")


def open_for_spinbox(spin: QAbstractSpinBox) -> bool:
    """Tastenfeld fuer ein Zahlenfeld; bei "Setzen" Wert uebernehmen und den Uebernehmen-Knopf ausloesen.
    True, wenn gesetzt wurde."""
    hex_mode = isinstance(spin, QSpinBox) and spin.displayIntegerBase() == 16
    if isinstance(spin, QDoubleSpinBox):
        value_text = f"{spin.value():.{spin.decimals()}f}".replace(".", ",")
        decimals = spin.decimals()
    else:
        value_text = f"{spin.value():X}" if hex_mode else str(spin.value())
        decimals = 0
    dialog = KeypadDialog(field_title(spin), value_text, minimum=spin.minimum(), maximum=spin.maximum(),
                          decimals=decimals, unit=spin.suffix(), hex_mode=hex_mode, parent=spin.window())
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    spin.clearFocus()   # der Tipp gab dem Textfeld den Fokus -> kein Textcursor danach
    if not accepted:
        return False
    value = dialog.number()
    if value is None:
        return False
    spin.setValue(value)
    partner = getattr(spin, APPLY_PARTNER, None)
    if isinstance(partner, QAbstractButton) and partner.isEnabled() and not partner.isCheckable():
        partner.click()
    return True


def deny_keyboard(event) -> bool:
    """Im Touch-Profil fuer Felder mit eigenem Tastenfeld: die Frage "Texteingabe?" (ImEnabled) mit Nein
    beantworten. Ein echter Fingertipp gibt dem Feld den Fokus, bevor das Tastenfeld aufgeht; Qt fragt das
    Fokus-Objekt dann, und QLineEdit sagt von sich aus Ja (unabhaengig von WA_InputMethodEnabled) -- die
    Bildschirmtastatur blitzte auf (am Pi, 11.11.2026). True = Ereignis beantwortet."""
    if (event.type() == QEvent.Type.InputMethodQuery and touch_active()
            and event.queries() & Qt.InputMethodQuery.ImEnabled):
        event.setValue(Qt.InputMethodQuery.ImEnabled, False)
        return True
    return False


class _HexLineFilter(QObject):
    """Tipp auf ein Hex-Textfeld (CAN-Nutzdaten) oeffnet im Touch-Profil das Hex-Tastenfeld."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if deny_keyboard(event):
            return True
        if (event.type() == QEvent.Type.MouseButtonPress and touch_active() and isinstance(obj, QLineEdit)
                and obj.isEnabled() and not obj.isReadOnly()):
            QTimer.singleShot(0, lambda: _open_for_hex_line(obj))
            return True
        return False


def _open_for_hex_line(edit: QLineEdit) -> None:
    dialog = KeypadDialog(field_title(edit), edit.text(), hex_mode=True, allow_space=True, parent=edit.window())
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    edit.clearFocus()
    if accepted:
        edit.setText(" ".join(dialog.text().split()).upper())
        edit.editingFinished.emit()


_HEX_FILTER: _HexLineFilter | None = None


def attach_hex_keypad(edit: QLineEdit) -> None:
    global _HEX_FILTER
    if _HEX_FILTER is None:
        _HEX_FILTER = _HexLineFilter()
    edit.installEventFilter(_HEX_FILTER)
