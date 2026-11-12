"""Reiter "Verbund" (Verbund Pi + PC, Stufe V1, PI-23 a): die Geraete der Partner-Instanz.

Jede Partnerkachel wird GENERISCH aus der Antwort der Netzwerk-Freigabe des Partners gebaut:
Werte aus "fields", Bedienelemente aus "actions" (Code, Wertebereich, Kanaele). Dadurch braucht
keine Geraeteart eine eigene Partner-Kachel, und die Wertebereiche kommen vom Besitzer des
Geraets (inkl. Nennwerten wie GMAX beim Netzteil) statt aus einer Kopie hier.

Bedient wird ueber partner_link.PartnerLink.send_action; der Partner prueft alles und antwortet.
Das Ergebnis steht unten in der Kachel ("✓ ok" / "✕ Fernsteuerung dort aus").
"""
from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from i18n import Translator, tr
from partner_client import PartnerSnapshot
from partner_link import PartnerLink, error_text
from theme import ThemeManager, set_tone
from theme import current as current_palette

# So viele Werte zeigt eine Kachel, bevor "Alle Werte" aufklappt (Batterie-Simulator: ~50 Felder).
FIELDS_COLLAPSED = 8
TILE_MIN_WIDTH = 300

DECIMALS_BY_UNIT = {"V": 3, "A": 3, "W": 2, "Ohm": 2, "Hz": 1, "%": 1, "°": 1, "mV": 0}


def format_value(value, unit: str) -> str:
    if isinstance(value, bool):
        return tr("ja") if value else tr("nein")
    if not isinstance(value, (int, float)):
        return str(value)
    magnitude = abs(value)
    decimals = DECIMALS_BY_UNIT.get(unit, 3 if magnitude < 10 else 2 if magnitude < 100 else 1)
    text = f"{value:.{decimals}f}".replace(".", ",")
    return f"{text} {unit}".strip()


def _is_switch(action: dict) -> bool:
    """Bedienung ohne Wert -> eigener Knopf (Ausgang EIN/AUS, Fernsteuerung, Wellenform, ...). Hat sie
    Kanaele, gilt der Kanal aus dem gemeinsamen Kanalfeld der Kachel."""
    return "value" not in action


class PartnerTile(QGroupBox):
    def __init__(self, link: PartnerLink, device_id: str) -> None:
        super().__init__()
        self._link = link
        self.device_id = device_id
        self._tile: dict = {}
        self._actions_key: tuple | None = None
        self._expanded = False
        self.setMinimumWidth(TILE_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        self._state = QLabel()
        self._state.setWordWrap(True)
        layout.addWidget(self._state)

        self._fields = QGridLayout()
        self._fields.setHorizontalSpacing(12)
        self._fields.setVerticalSpacing(2)
        layout.addLayout(self._fields)
        self._more = QPushButton()
        self._more.setFlat(True)
        self._more.clicked.connect(self._toggle_more)
        layout.addWidget(self._more, 0, Qt.AlignmentFlag.AlignLeft)

        # Kanal (Funktionsgenerator 1/2, Batterie-Simulator Zelle 1..14, microHIL-Ausgang): gilt fuer alle
        # Bedienungen der Kachel, die einen Kanal brauchen
        self._channel_row = QWidget()
        channel_layout = QHBoxLayout(self._channel_row)
        channel_layout.setContentsMargins(0, 0, 0, 0)
        self._channel_label = QLabel()
        self._channel_spin = QSpinBox()
        channel_layout.addWidget(self._channel_label)
        channel_layout.addWidget(self._channel_spin)
        channel_layout.addStretch(1)
        layout.addWidget(self._channel_row)

        self._switches = QGridLayout()
        self._switches.setSpacing(6)
        layout.addLayout(self._switches)

        # Eine Zeile fuer alles mit Wert: Aktion | Wert | Senden
        self._value_row = QWidget()
        row = QHBoxLayout(self._value_row)
        row.setContentsMargins(0, 0, 0, 0)
        self._action_combo = QComboBox()
        self._action_combo.currentIndexChanged.connect(self._on_action_selected)
        self._value_spin = QDoubleSpinBox()
        self._value_spin.setKeyboardTracking(False)
        self._send_button = QPushButton()
        self._send_button.clicked.connect(self._send_value_action)
        row.addWidget(self._action_combo, 1)
        row.addWidget(self._value_spin)
        row.addWidget(self._send_button)
        layout.addWidget(self._value_row)

        self._result = QLabel()
        self._result.setWordWrap(True)
        layout.addWidget(self._result)
        self._switch_buttons: list[QPushButton] = []
        self._value_actions: list[dict] = []
        self.retranslate()

    # -- Daten -------------------------------------------------------------------

    def update_tile(self, tile: dict, partner: str, remote_effective: bool) -> None:
        self._tile = tile
        self.setTitle(f"{partner} · {tile.get('label') or self.device_id}")
        online = bool(tile.get("online", True))
        control = bool(tile.get("control"))
        available = bool(tile.get("control_available")) and remote_effective
        blocked = tile.get("control_blocked") or ("remote_control_inactive" if not remote_effective else "")
        if not online:
            state, tone = tr("getrennt"), "danger"
        elif not control:
            state, tone = tr("verbunden · nur Anzeige"), "muted"
        elif not available:
            state, tone = tr("verbunden · Bedienen gesperrt: {reason}", reason=tr(error_text(blocked))), "warning"
        else:
            state, tone = tr("verbunden · bedienbar"), "success"
        if tile.get("stale"):
            state += " · " + tr("Werte veraltet")
        leased = tile.get("leased_by")
        if isinstance(leased, dict):
            state += " · " + tr("belegt durch {holder} · {run}", holder=leased.get("holder", "?"),
                                run=leased.get("run") or tr("Testablauf"))
        self._state.setText(state)
        set_tone(self._state, tone)
        self._render_fields(tile.get("fields") or [])
        self._render_actions((tile.get("actions") or []) if control else [])
        enabled = online and available
        for button in self._switch_buttons:
            button.setEnabled(enabled)
        self._value_row.setEnabled(enabled)
        self._channel_row.setEnabled(enabled)

    def _render_fields(self, fields: list[dict]) -> None:
        while self._fields.count():
            item = self._fields.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        shown = fields if self._expanded else fields[:FIELDS_COLLAPSED]
        pal = current_palette()
        for row, field in enumerate(shown):
            name = QLabel(str(field.get("name") or field.get("key")))
            name.setStyleSheet(f"color: {pal.text_muted};")
            value = field.get("text") if "text" in field else format_value(field.get("value"), field.get("unit", ""))
            value_label = QLabel(str(value))
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value_label.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace;")
            self._fields.addWidget(name, row, 0)
            self._fields.addWidget(value_label, row, 1)
        hidden = len(fields) - FIELDS_COLLAPSED
        self._more.setVisible(hidden > 0)
        self._more.setText(tr("Weniger Werte") if self._expanded else tr("Alle Werte ({n})", n=len(fields)))

    def _toggle_more(self) -> None:
        self._expanded = not self._expanded
        self._render_fields(self._tile.get("fields") or [])

    def _render_actions(self, actions: list[dict]) -> None:
        key = tuple((a.get("action"), str(a.get("value")), a.get("channels")) for a in actions)
        if key == self._actions_key:
            return
        self._actions_key = key
        for button in self._switch_buttons:
            self._switches.removeWidget(button)
            button.deleteLater()
        self._switch_buttons = []
        channels = max((int(a.get("channels") or 0) for a in actions), default=0)
        self._channel_row.setVisible(channels > 0)
        if channels:
            self._channel_spin.setRange(1, channels)
        for index, action in enumerate(a for a in actions if _is_switch(a)):
            button = QPushButton(tr(action.get("label") or action.get("action")))
            button.setToolTip(action.get("note") or action.get("action"))
            code, has_channel = action.get("action"), bool(action.get("channels"))
            button.clicked.connect(lambda _c=False, code=code, has_channel=has_channel: self._send(
                code, None, self._channel_spin.value() if has_channel else None))
            self._switches.addWidget(button, index // 2, index % 2)
            self._switch_buttons.append(button)
        self._value_actions = [a for a in actions if not _is_switch(a)]
        self._action_combo.blockSignals(True)
        self._action_combo.clear()
        for action in self._value_actions:
            self._action_combo.addItem(tr(action.get("label") or action.get("action")), action.get("action"))
        self._action_combo.blockSignals(False)
        self._value_row.setVisible(bool(self._value_actions))
        self._on_action_selected(self._action_combo.currentIndex())

    def _on_action_selected(self, index: int) -> None:
        action = self._value_actions[index] if 0 <= index < len(self._value_actions) else {}
        value = action.get("value")
        self._value_spin.setVisible(isinstance(value, dict))
        if isinstance(value, dict):
            unit = str(value.get("unit") or "")
            self._value_spin.setDecimals(DECIMALS_BY_UNIT.get(unit, 3))
            self._value_spin.setRange(float(value.get("min", 0.0)), float(value.get("max", 0.0)))
            self._value_spin.setSuffix(f" {unit}" if unit else "")
        self._action_combo.setToolTip(action.get("note") or "")

    def _send_value_action(self) -> None:
        index = self._action_combo.currentIndex()
        if not 0 <= index < len(self._value_actions):
            return
        action = self._value_actions[index]
        value = self._value_spin.value() if isinstance(action.get("value"), dict) else None
        channel = self._channel_spin.value() if action.get("channels") else None
        self._send(action.get("action"), value, channel)

    def _send(self, code: str, value: float | None, channel: int | None) -> None:
        label = f"{self._link.name()} {self._tile.get('label') or self.device_id}"
        if self._link.send_action(self.device_id, code, value, channel, label):
            self._result.setText(tr("{action} gesendet …", action=code))
            set_tone(self._result, "muted")

    def show_result(self, action: str, ok: bool, message: str) -> None:
        if ok:
            self._result.setText(f"✓ {action}" + (f" – {message}" if message else ""))
            set_tone(self._result, "success")
        else:
            self._result.setText(f"✕ {action}: {tr(message)}")
            set_tone(self._result, "danger")

    def retranslate(self) -> None:
        self._send_button.setText(tr("Senden"))
        self._channel_label.setText(tr("Kanal"))
        if self._tile:
            self._render_fields(self._tile.get("fields") or [])


class PartnerTab(QWidget):
    def __init__(self, link: PartnerLink) -> None:
        super().__init__()
        self._link = link
        self._tiles: dict[str, PartnerTile] = {}
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self._icon = QLabel()
        header.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setStyleSheet("font-weight: bold;")
        self._detail = QLabel()
        self._detail.setWordWrap(True)
        texts.addWidget(self._status)
        texts.addWidget(self._detail)
        header.addLayout(texts, 1)
        layout.addLayout(header)

        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        self._grid = QGridLayout(holder)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(10)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._area.setWidget(holder)
        layout.addWidget(self._area, 1)
        self._columns = 0

        link.snapshot_changed.connect(self._on_snapshot)
        link.connection_changed.connect(lambda *_: self._render_header())
        link.action_finished.connect(self._on_action_finished)
        Translator.instance().language_changed.connect(lambda *_: self._retranslate())
        ThemeManager.instance().changed.connect(lambda _pal: self._render_header())
        self._render_header()

    def tile(self, device_id: str) -> PartnerTile | None:
        return self._tiles.get(device_id)

    def _retranslate(self) -> None:
        for tile in self._tiles.values():
            tile.retranslate()
        self._render_header()

    def _render_header(self) -> None:
        link = self._link
        pal = current_palette()
        snap = link.snapshot()
        if not link.enabled:
            icon, color = "mdi.lan-disconnect", pal.text_muted
            self._status.setText(tr("Kein Partner eingetragen"))
            self._detail.setText(tr("Einstellungen → Netzwerk → „Verbund mit einem zweiten LabControl“: "
                                    "Adresse und Token des anderen Rechners eintragen."))
            set_tone(self._detail, "muted")
        elif not link.is_connected() or snap is None:
            icon, color = "mdi.lan-disconnect", pal.danger
            self._status.setText(tr("{name}: nicht verbunden", name=link.name()))
            self._detail.setText(f"{link.url} – {tr(link.reason())}")
            set_tone(self._detail, "danger")
        else:
            icon, color = "mdi.lan-connect", pal.success
            self._status.setText(tr("Verbunden mit {name}", name=link.name()))
            parts = [link.url, f"LabControl {snap.version}"]
            if snap.simulation:
                parts.append(tr("Simulation"))
            if snap.remote_effective:
                total = int(snap.remote_remaining_s)
                parts.append(tr("Fernsteuerung dort aktiv, noch {time}",
                                time=f"{total // 60}:{total % 60:02d}"))
                tone = "muted"
            else:
                parts.append(tr("Fernsteuerung dort aus – bedienen erst nach dem Einschalten am {name}",
                                name=link.name()))
                tone = "warning"
            if snap.lock != "free":
                parts.append(tr("dort gesperrt (Testlauf oder Sicherheitsabschaltung)"))
                tone = "warning"
            if not snap.tiles:
                parts.append(tr("keine Geräte freigegeben"))
            self._detail.setText(" · ".join(parts))
            set_tone(self._detail, tone)
        self._icon.setPixmap(qta.icon(icon, color=color).pixmap(32, 32))

    def _on_snapshot(self, snap: PartnerSnapshot | None) -> None:
        tiles = {t.get("id"): t for t in (snap.tiles if snap is not None else []) if t.get("id")}
        for device_id in [d for d in self._tiles if d not in tiles]:
            widget = self._tiles.pop(device_id)
            self._grid.removeWidget(widget)
            widget.deleteLater()
        added = False
        for device_id, tile in tiles.items():
            widget = self._tiles.get(device_id)
            if widget is None:
                widget = PartnerTile(self._link, device_id)
                self._tiles[device_id] = widget
                added = True
            widget.update_tile(tile, self._link.name(), snap.remote_effective and snap.lock == "free")
        if added or len(tiles) != self._grid.count():
            self._relayout(force=True)
        self._render_header()

    def _on_action_finished(self, _request_id: int, device_id: str, action: str, ok: bool, message: str) -> None:
        tile = self._tiles.get(device_id)
        if tile is not None:
            tile.show_result(action, ok, message)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt-Override)
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self, force: bool = False) -> None:
        columns = max(1, (self._area.viewport().width() - 10) // (TILE_MIN_WIDTH + 10))
        if columns == self._columns and not force:
            return
        self._columns = columns
        for widget in self._tiles.values():
            self._grid.removeWidget(widget)
        for column in range(self._grid.columnCount()):
            self._grid.setColumnStretch(column, 0)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)   # gleich breite Spalten
        for index, device_id in enumerate(sorted(self._tiles)):
            self._grid.addWidget(self._tiles[device_id], index // columns, index % columns,
                                 Qt.AlignmentFlag.AlignTop)
