"""Texttastatur fuer das Touch-Profil (Entscheidung PI-37 a, 12.11.2026).

Im Kiosk am Pi blendet niemand die Bildschirmtastatur des Systems (squeekboard) ein -- Textfelder
liessen sich gar nicht ausfuellen. Deshalb oeffnet im Touch-Profil ein Tipp auf ein Textfeld diese
Tastatur, im Stil des Zahlenfelds (touch_keypad.py): oben der Feldname, darunter der Text, dann die
Tasten (QWERTZ mit Umlauten, Ziffern, Zeichenebene). "Übernehmen" setzt den Text ins Feld und meldet
ihn wie eine Eingabe von Hand (textEdited, editingFinished).

Ausgenommen: Felder im Inneren von Zahlenfeldern und nicht editierbaren Auswahllisten, Hex-Felder
(eigenes Tastenfeld), schreibgeschuetzte und gesperrte Felder. Eingehaengt wird ueber den
App-weiten Filter von touch_ui.TouchSizer (handle_event).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from theme import METRICS
from theme import current as current_palette
from touch_keypad import field_title, touch_active

NO_KEYBOARD = "_touch_no_text_keyboard"   # Attribut am Textfeld: hat eigenes Tastenfeld (Hex)
KEY_W, KEY_H = 76, 54

LETTERS = [
    list("1234567890ß"),
    list("qwertzuiopü"),
    list("asdfghjklöä"),
    list("yxcvbnm"),
]
SYMBOLS = [
    list("1234567890ß"),
    list("!\"#$%&()=?+"),
    list("*'<>;[]{}~|"),
    list("\\^€§°µ²"),
]


class TextKeyboardDialog(QDialog):
    """Tastatur ohne Fensterleiste; Text wird nur bei "Übernehmen" ins Feld geschrieben."""

    def __init__(self, title: str, text: str, *, max_length: int = 32767, validator: QValidator | None = None,
                 password: bool = False, placeholder: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("touchKeyboard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._text = text
        self._max = max_length
        self._validator = validator
        self._password = password
        self._placeholder = placeholder
        self._shift = False
        self._symbols = False
        pal = current_palette()
        self.setStyleSheet(f"QDialog#touchKeyboard {{ background-color: {pal.bg}; border: 2px solid {pal.border};"
                           f" border-radius: 8px; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(6)
        head = QLabel(title)
        head.setStyleSheet("font-weight: bold;")
        outer.addWidget(head)
        self._display = QLabel()
        self._display.setMinimumHeight(52)
        self._display.setTextFormat(Qt.TextFormat.PlainText)
        self._display_style = (f"font-size: 24px; padding: 4px 12px; background: {pal.surface};"
                               f" border: 1px solid {pal.border}; border-radius: {METRICS.radius_md}px;")
        outer.addWidget(self._display)

        self._grid = QGridLayout()
        self._grid.setSpacing(5)
        self._char_keys: list[list[QPushButton]] = []
        for r, row in enumerate(LETTERS):
            keys = []
            for c, _ch in enumerate(row):
                button = self._key("", None)
                button.clicked.connect(lambda _=False, rr=r, cc=c: self._press_at(rr, cc))
                self._grid.addWidget(button, r, c + (1 if r == 3 else 0))
                keys.append(button)
            self._char_keys.append(keys)
        self._shift_button = self._key("⇧", self._toggle_shift)
        self._shift_button.setCheckable(True)
        self._grid.addWidget(self._shift_button, 3, 0)
        # Zeile 3: Umschalt, 7 Buchstaben, dann . - _ ; Zeile 4: Zeichenebene, : / @, Leerzeichen, Löschen, Rücktaste
        for col, ch in ((8, "."), (9, "-"), (10, "_"), (1, ":"), (2, "/"), (3, "@")):
            self._grid.addWidget(self._key(ch, lambda _=False, k=ch: self._insert(k)), 3 if col >= 8 else 4, col)
        self._sym_button = self._key("#+=", self._toggle_symbols)
        self._sym_button.setCheckable(True)
        self._grid.addWidget(self._sym_button, 4, 0)
        self._grid.addWidget(self._key(tr("Leerzeichen"), lambda: self._insert(" ")), 4, 4, 1, 4)
        self._grid.addWidget(self._key(tr("Löschen"), self._clear), 4, 8, 1, 2)
        self._grid.addWidget(self._key("⌫", self._backspace), 4, 10)
        outer.addLayout(self._grid)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.addStretch(1)
        cancel = self._key(tr("Abbruch"), self.reject)
        cancel.setMinimumWidth(2 * KEY_W)
        self._ok_button = self._key(tr("Übernehmen"), self.accept)
        self._ok_button.setMinimumWidth(3 * KEY_W)
        self._ok_button.setStyleSheet(
            f"QPushButton {{ background-color: {pal.accent}; color: {pal.on_accent}; font-weight: bold; }}"
            f"QPushButton:disabled {{ background-color: {pal.surface_alt}; color: {pal.text_muted}; }}")
        bottom.addWidget(cancel)
        bottom.addWidget(self._ok_button)
        outer.addLayout(bottom)
        self._relabel()
        self._refresh()

    def _key(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setMinimumSize(KEY_W, KEY_H)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if slot is not None:
            button.clicked.connect(slot)
        return button

    # -- Ebenen -------------------------------------------------------------------

    def _layer(self) -> list[list[str]]:
        return SYMBOLS if self._symbols else LETTERS

    def _relabel(self) -> None:
        for r, keys in enumerate(self._char_keys):
            row = self._layer()[r]
            for c, button in enumerate(keys):
                ch = row[c] if c < len(row) else ""
                if self._shift and not self._symbols and ch != "ß":
                    ch = ch.upper()
                button.setText(ch.replace("&", "&&"))
                button.setVisible(bool(ch))
        self._shift_button.setChecked(self._shift)
        self._shift_button.setEnabled(not self._symbols)
        self._sym_button.setChecked(self._symbols)

    def _toggle_shift(self) -> None:
        self._shift = not self._shift
        self._relabel()

    def _toggle_symbols(self) -> None:
        self._symbols = not self._symbols
        self._relabel()

    # -- Eingabe ------------------------------------------------------------------

    def _press_at(self, row: int, col: int) -> None:
        ch = self._layer()[row][col]
        if self._shift and not self._symbols and ch != "ß":
            ch = ch.upper()
            self._shift = False   # Umschalten gilt fuer einen Buchstaben
            self._relabel()
        self._insert(ch)

    def _insert(self, text: str) -> None:
        if len(self._text) + len(text) > self._max:
            return
        self._text += text
        self._refresh()

    def _backspace(self) -> None:
        self._text = self._text[:-1]
        self._refresh()

    def _clear(self) -> None:
        self._text = ""
        self._refresh()

    # -- Pruefen und Anzeige --------------------------------------------------------

    def text(self) -> str:
        return self._text

    def _valid(self) -> bool:
        if self._validator is None:
            return True
        state = self._validator.validate(self._text, len(self._text))
        state = state[0] if isinstance(state, tuple) else state
        return state == QValidator.State.Acceptable

    def _refresh(self) -> None:
        pal = current_palette()
        if self._text:
            shown = "•" * len(self._text) if self._password else self._text
            self._display.setText(shown + "▏")
        else:
            self._display.setText(self._placeholder or "▏")
        ok = self._valid()
        self._ok_button.setEnabled(ok)
        color = (pal.text if ok else pal.danger) if self._text else pal.text_muted
        self._display.setStyleSheet(f"{self._display_style} color: {color};")


def eligible(edit: QLineEdit) -> bool:
    if getattr(edit, NO_KEYBOARD, False) or not edit.isEnabled() or edit.isReadOnly():
        return False
    parent = edit.parentWidget()
    if isinstance(parent, QAbstractSpinBox):
        return False
    if isinstance(parent, QComboBox) and not parent.isEditable():
        return False
    window = edit.window()
    return not isinstance(window, TextKeyboardDialog)


def open_for_line(edit: QLineEdit) -> bool:
    """Tastatur fuer ein Textfeld; True, wenn uebernommen wurde."""
    combo = edit.parentWidget() if isinstance(edit.parentWidget(), QComboBox) else None
    title = field_title(combo or edit)
    if title == tr("Wert"):
        title = edit.placeholderText() or tr("Text")
    dialog = TextKeyboardDialog(
        title, edit.text(), max_length=edit.maxLength(), validator=edit.validator(),
        password=edit.echoMode() != QLineEdit.EchoMode.Normal, placeholder=edit.placeholderText(),
        parent=edit.window())
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    edit.clearFocus()
    if not accepted or dialog.text() == edit.text():
        return False
    if combo is not None:
        combo.setEditText(dialog.text())
    else:
        edit.setText(dialog.text())
    edit.textEdited.emit(edit.text())
    edit.editingFinished.emit()
    return True


def handle_event(obj, event) -> bool:
    """Aus dem App-weiten Filter (touch_ui.TouchSizer): True = Ereignis erledigt."""
    if not isinstance(obj, QLineEdit) or not touch_active():
        return False
    etype = event.type()
    if etype == QEvent.Type.InputMethodQuery:
        if eligible(obj) and event.queries() & Qt.InputMethodQuery.ImEnabled:
            event.setValue(Qt.InputMethodQuery.ImEnabled, False)
            return True
        return False
    if etype in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick) and eligible(obj):
        if event.button() == Qt.MouseButton.LeftButton:
            QTimer.singleShot(0, lambda: open_for_line(obj))
        return True
    return False
