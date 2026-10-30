"""Batterie-Simulator (Longboard, 14 Zellkanaele) ueber CAN.

Der Simulator ist ein eigenes Geraet, oeffnet aber keinen eigenen Bus: er haengt
als CanNode (can_bus/node.py) an einem in LabControl konfigurierten CAN-Interface.
Das Protokoll beschreibt die mitgelieferte BattSimV2.dbc (Kopie aus BattSimFW,
projects/battsim_master/BattSimV2.dbc; der Inhalt von BattSimV1 bleibt unveraendert
enthalten, dazu kommen die Knoten, die Statusbotschaft 0x121 und der Kanalstatus 4);
kodiert und dekodiert wird mit cantools.

Die Statusbotschaft 0x121 (STATUS_BATTSIM_EXT) gibt es nur bei der neuen Firmware
(BattSimFW ab Stufe B/C): FAIL-Grund 1..6, Versorgungsstrom, LIMIT, Ueberlast, Regler ein/aus
und Firmware-Version. Die alte Firmware sendet sie nicht; die Felder bleiben dann None.

Verhalten der Firmware, wie es aus dem Simulink-Modell gelesen wurde (Workspace
BatterieSimulator, doc/can-verhalten.md) -- am Geraet noch zu bestaetigen:

- Fernsteuerung: 0x130 CONTROL_BATTSIM mit REMOTE=2, MODE=2 bringt das Geraet
  aus STANDBY nach REMOTE/STOP, OUTPUT=2 nach REMOTE/RUN (Stellglieder an).
  REMOTE=1 fuehrt zurueck nach STANDBY.
- Ausfall: kommt 0x130 1,5-3 s nicht, wird die Botschaft ungueltig; in RUN
  folgt nach weiteren 3 s FAIL (alle Ausgaenge aus). Zurueck: MODE=1, dann 2.
- Zellfreigabe CELLn_ENABLE wird in der Firmware nach boolean gewandelt:
  0 = aus, 1 UND 2 = ein (die DBC nennt 1 "disable"). Dieser Treiber sendet
  deshalb 0 fuer aus und 2 fuer ein -- das wirkt bei alter und neuer Firmware.
- Zellstatus in 0x110-0x115: die Firmware sendet 1 = aus, 0 = ein (DBC: 2 = ein).
  Gelesen wird deshalb 1 = aus, 0 und 2 = ein, 3 = Sollwertfehler.
- Bestueckt sind 14 Kanaele; Kanal 15/16 der DBC sind auch in der Firmware aus.

Der Treiber sendet NUR, solange die Fernsteuerung ueber LabControl
eingeschaltet ist. Sonst wuerde ein zyklisches REMOTE=1 das Geraet aus einer am
Geraet selbst gewaehlten Fernsteuerung sofort wieder herausholen.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import cantools

from can_bus.driver import CanFrame
from can_bus.node import CanNode, TxFrame

DBC_PATH = Path(__file__).resolve().parent / "BattSimV2.dbc"

KIND = "battsim"

CELL_COUNT = 14          # bestueckte Kanaele (Nutzer 2026-10-21, Firmware: Enable15/16 auskommentiert)
DBC_CELL_COUNT = 16      # Signale in der DBC
CELLS_PER_MESSAGE = 3

# Geraetegrenze laut Konzept (0-5 V je Kanal) und Firmware (Kennlinie endet bei 5 V).
MAX_CELL_VOLTAGE_V = 5.0
# Voreinstellung fuer den Sicherheits-Grenzwert (Board bs-grenzwert, 2026-10-21).
DEFAULT_VOLTAGE_LIMIT_V = 4.30

CONTROL_ID = 0x130
STATUS_ID = 0x120
STATUS_EXT_ID = 0x121
CELL_CONTROL_IDS = (0x100, 0x101, 0x102, 0x103, 0x104, 0x105)
CELL_STATUS_IDS = (0x110, 0x111, 0x112, 0x113, 0x114, 0x115)

# Zyklen laut parameter.m (can_comm.id130h_cycletime = 100 ms, id100h..105h = 1000 ms).
CONTROL_CYCLE_S = 0.1
CELL_CYCLE_S = 1.0
# 0x120 kommt alle 100 ms (CAN_TX_TASK1_TIME). Nach 1 s ohne Status gilt das
# Geraet als getrennt -- grosszuegig gegen Lastspitzen am Bus, aber schnell genug
# fuer die Anzeige.
STATUS_TIMEOUT_S = 1.0
# Nach "Fernsteuerung aus" noch so lange REMOTE=1 senden, bevor das Senden
# endet -- ein einzelner Frame koennte verloren gehen.
REMOTE_RELEASE_S = 0.5
# Dauer von MODE=1 beim Zuruecksetzen aus FAIL, bevor wieder MODE=2 kommt
# (mehrere 0x130-Zyklen, damit die Firmware den Wechsel sicher sieht).
FAIL_RESET_S = 0.5

# Werte der DBC (VAL_ 304 / VAL_ 288).
REMOTE_STANDALONE, REMOTE_REMOTE = 1, 2
OUTPUT_DISABLE, OUTPUT_ENABLE = 1, 2
MODE_STANDBY, MODE_ON = 1, 2
ENABLE_OFF, ENABLE_ON = 0, 2   # siehe Modul-Docstring: 0 statt der 1 aus der DBC

# Anzeigenamen der BATTSIM_STATUS-Werte (DBC: STANDBY, ON_Standalone,
# ON_RemoteControl, ON_DynamicCellSim, FAIL).
STATUS_NAMES = {
    0: "kein Signal", 1: "Standby", 2: "Handbetrieb", 3: "Fernsteuerung",
    4: "Zellmodell", 5: "FAIL", 6: "reserviert", 7: "ungültig",
}
STATUS_FAIL = 5
STATUS_REMOTE = 3

# BATTSIM_FAIL_REASON in 0x121 (DBC VAL_ 289; die Firmware haelt den Grund auch in STANDBY nach
# einem Watchdog-Reset, bis zum ersten Zustandswechsel).
FAIL_REASON_NAMES = {
    0: "keiner", 1: "Timeout 0x130", 2: "Alive-Fehler 0x130", 3: "Überlast 48 V",
    4: "Watchdog-/Safety-Reset", 5: "Strommessung ungültig", 6: "Überspannung Zelle",
    15: "unbekannt",
}
# CELLnn_STATUS (DBC VAL_ 272..277). Die alte Firmware sendet 0 fuer "ein" (siehe Docstring);
# die neue 2 = ein, 1 = aus, 3 = Sollwertfehler, 4 = Sollwert unter dem Pegelboden der Endstufe
# (kein FAIL; ohne aeussere Last liegt der Kanal bei seinem Boden von etwa 0,7 V, mit Last folgt er dem Sollwert; Status 4 ist ein Hinweis).
CELL_STATUS_NAMES = {
    0: "kein Wert (alte Firmware: ein)", 1: "aus", 2: "ein", 3: "Sollwertfehler",
    4: "Sollwert unter Pegelboden", 5: "reserviert", 6: "reserviert", 7: "ungültig",
}
CELL_STATUS_SETPOINT_ERROR = 3
CELL_STATUS_BELOW_FLOOR = 4


class BattSimError(RuntimeError):
    """Fehler im Batterie-Simulator-Treiber."""


class BattSimValueError(BattSimError):
    """Wert abgelehnt (Bereich, Kanal, Zustand) -- kein Verbindungsproblem."""


@dataclass
class CellState:
    index: int                    # 1-basiert
    setpoint_v: float             # zuletzt vorgegebener Sollwert
    enable_requested: bool        # zuletzt vorgegebene Freigabe
    voltage_v: float | None       # vom Geraet gemeldete Istspannung
    enabled: bool | None          # vom Geraet gemeldet (None = noch nichts empfangen)
    setpoint_error: bool = False
    status: int | None = None     # CELLnn_STATUS roh (None = nichts empfangen)
    status_name: str = ""
    below_floor: bool = False     # Status 4: Sollwert unter dem Pegelboden der Endstufe


@dataclass
class BattSimState:
    online: bool
    status: int | None
    status_name: str
    output_on: bool | None
    remote_requested: bool
    output_requested: bool
    voltage_limit_v: float
    cells: list[CellState] = field(default_factory=list)
    stack_voltage_v: float | None = None
    last_tx_error: str = ""
    # Statusbotschaft 0x121 (nur neue Firmware, sonst None)
    fail_reason: int | None = None
    fail_reason_name: str = ""
    controller_on: bool | None = None      # Spannungsregler der Kanaele arbeitet
    overload: bool | None = None
    limit_active: bool | None = None       # LIMIT-Leitung der 48-V-Versorgung
    supply_current_a: float | None = None
    fw_version: str | None = None

    @property
    def failed(self) -> bool:
        return self.status == STATUS_FAIL


def load_database(path: Path | str = DBC_PATH):
    try:
        return cantools.database.load_file(str(path))
    except Exception as exc:  # noqa: BLE001 -- cantools wirft je nach Ursache verschiedene Typen
        raise BattSimError(f"DBC des Batterie-Simulators nicht lesbar ({path}): {exc}") from exc


class BattSim(CanNode):
    rx_ids = frozenset((STATUS_ID, STATUS_EXT_ID, *CELL_STATUS_IDS))

    def __init__(self, cell_count: int = CELL_COUNT, voltage_limit_v: float = DEFAULT_VOLTAGE_LIMIT_V,
                 dbc_path: Path | str = DBC_PATH) -> None:
        super().__init__()
        self._db = load_database(dbc_path)
        self.cell_count = cell_count
        self._limit = DEFAULT_VOLTAGE_LIMIT_V
        self.set_voltage_limit(voltage_limit_v)
        # Sollzustand (was LabControl sendet)
        self._active = False
        self._remote = False
        self._output = False
        self._mode = MODE_STANDBY
        self._release_until = 0.0
        self._fail_reset_until = 0.0
        self._setpoints = [0.0] * DBC_CELL_COUNT
        self._enables = [False] * DBC_CELL_COUNT
        self._alive: dict[int, int] = {}
        self._next_due: dict[int, float] = {}
        # Istzustand (was das Geraet meldet)
        self._status: int | None = None
        self._output_status: int | None = None
        self._last_status_at: float | None = None
        self._voltages: list[float | None] = [None] * DBC_CELL_COUNT
        self._cell_status: list[int | None] = [None] * DBC_CELL_COUNT
        self._ext: dict[str, float | int] = {}
        self._last_ext_at: float | None = None

    # -- Grenzwert ---------------------------------------------------------

    def set_voltage_limit(self, volts: float) -> None:
        if not 0.0 < volts <= MAX_CELL_VOLTAGE_V:
            raise BattSimValueError(
                f"Grenzwert {volts:.3f} V ausserhalb 0 < U <= {MAX_CELL_VOLTAGE_V:.1f} V")
        with self._lock:
            self._limit = float(volts)

    @property
    def voltage_limit_v(self) -> float:
        return self._limit

    # -- Steuerung (DeviceWorker-Thread) -------------------------------------

    def set_remote(self, on: bool) -> None:
        """Fernsteuerung ein: REMOTE=2, MODE=2, Ausgang gesperrt (sicherer
        Einstieg in REMOTE/STOP). Aus: REMOTE=1 fuer REMOTE_RELEASE_S, dann
        endet das Senden."""
        with self._lock:
            if on:
                self._active = True
                self._remote = True
                self._mode = MODE_ON
                self._output = False
                self._release_until = 0.0
            else:
                if not self._active:
                    return
                self._remote = False
                self._output = False
                self._mode = MODE_STANDBY
                self._release_until = time.monotonic() + REMOTE_RELEASE_S
            self._mark_all_due()

    def set_output(self, on: bool) -> None:
        with self._lock:
            if on and not self._remote:
                raise BattSimValueError("Ausgang nur bei eingeschalteter Fernsteuerung")
            self._output = bool(on)
            self._next_due[CONTROL_ID] = 0.0

    def reset_fail(self) -> None:
        """Aus FAIL heraus: MODE=1 (-> STANDBY), nach FAIL_RESET_S wieder MODE=2
        (-> REMOTE/STOP). Ausgang bleibt gesperrt."""
        with self._lock:
            if not self._remote:
                raise BattSimValueError("Zuruecksetzen nur bei eingeschalteter Fernsteuerung")
            self._output = False
            self._fail_reset_until = time.monotonic() + FAIL_RESET_S
            self._next_due[CONTROL_ID] = 0.0

    def set_cell_voltage(self, cell: int, volts: float) -> None:
        self._check_cell(cell)
        self._check_voltage(volts)
        with self._lock:
            self._setpoints[cell - 1] = float(volts)
            self._next_due[self._cell_msg_id(cell)] = 0.0

    def set_all_voltages(self, volts: float) -> None:
        self._check_voltage(volts)
        with self._lock:
            for i in range(self.cell_count):
                self._setpoints[i] = float(volts)
            self._mark_cells_due()

    def set_cell_enabled(self, cell: int, on: bool) -> None:
        self._check_cell(cell)
        with self._lock:
            self._enables[cell - 1] = bool(on)
            self._next_due[self._cell_msg_id(cell)] = 0.0

    def set_all_enabled(self, on: bool) -> None:
        with self._lock:
            for i in range(self.cell_count):
                self._enables[i] = bool(on)
            self._mark_cells_due()

    # -- Zustand -----------------------------------------------------------

    def online(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return self._last_status_at is not None and now - self._last_status_at <= STATUS_TIMEOUT_S

    def get_state(self) -> BattSimState:
        now = time.monotonic()
        online = self.online(now)
        with self._lock:
            cells = []
            for i in range(self.cell_count):
                st = self._cell_status[i] if online else None
                cells.append(CellState(
                    index=i + 1,
                    setpoint_v=self._setpoints[i],
                    enable_requested=self._enables[i],
                    voltage_v=self._voltages[i] if online else None,
                    # Status 4 (Sollwert unter Pegelboden): der Kanal ist freigegeben und laeuft
                    enabled=None if st is None else st in (0, 2, CELL_STATUS_BELOW_FLOOR),
                    setpoint_error=st == CELL_STATUS_SETPOINT_ERROR,
                    status=st,
                    status_name="" if st is None else CELL_STATUS_NAMES.get(st, f"unbekannt ({st})"),
                    below_floor=st == CELL_STATUS_BELOW_FLOOR,
                ))
            voltages = [c.voltage_v for c in cells]
            stack = sum(voltages) if online and all(v is not None for v in voltages) else None
            status = self._status if online else None
            ext = (self._ext if online and self._last_ext_at is not None
                   and now - self._last_ext_at <= STATUS_TIMEOUT_S else {})
            reason = int(ext["fail_reason"]) if "fail_reason" in ext else None
            return BattSimState(
                online=online,
                status=status,
                status_name=STATUS_NAMES.get(status, "") if status is not None else "getrennt",
                output_on=None if not online or self._output_status is None else self._output_status == 2,
                remote_requested=self._remote,
                output_requested=self._output,
                voltage_limit_v=self._limit,
                cells=cells,
                stack_voltage_v=stack,
                last_tx_error=self.last_tx_error,
                fail_reason=reason,
                fail_reason_name="" if reason is None else FAIL_REASON_NAMES.get(reason, f"unbekannt ({reason})"),
                controller_on=None if "controller_on" not in ext else bool(ext["controller_on"]),
                overload=None if "overload" not in ext else bool(ext["overload"]),
                limit_active=None if "limit" not in ext else bool(ext["limit"]),
                supply_current_a=ext.get("supply_current"),
                fw_version=(None if "fw_major" not in ext
                            else f"{int(ext['fw_major'])}.{int(ext['fw_minor'])}.{int(ext['fw_patch'])}"),
            )

    # -- CanNode -----------------------------------------------------------

    def on_frame(self, frame: CanFrame) -> None:
        if frame.extended or frame.arbitration_id not in self.rx_ids:
            return
        try:
            values = self._db.decode_message(frame.arbitration_id, frame.data,
                                             decode_choices=False, allow_truncated=True)
        except Exception:  # noqa: BLE001 -- kaputter Frame: ignorieren wie ein unbekannter
            return
        now = time.monotonic()
        with self._lock:
            if frame.arbitration_id == STATUS_ID:
                self._status = int(values.get("BATTSIM_STATUS", 0))
                self._output_status = int(values.get("BATTSIM_OUTPUT_STATUS", 0))
                self._last_status_at = now
                return
            if frame.arbitration_id == STATUS_EXT_ID:
                self._ext = {
                    "fail_reason": int(values.get("BATTSIM_FAIL_REASON", 0)),
                    "controller_on": int(values.get("BATTSIM_CONTROLLER_ON", 0)),
                    "overload": int(values.get("BATTSIM_OVERLOAD", 0)),
                    "limit": int(values.get("BATTSIM_LIMIT", 0)),
                    "supply_current": float(values.get("BATTSIM_SUPPLY_CURRENT", 0.0)),
                    "fw_major": int(values.get("FW_VERSION_MAJOR", 0)),
                    "fw_minor": int(values.get("FW_VERSION_MINOR", 0)),
                    "fw_patch": int(values.get("FW_VERSION_PATCH", 0)),
                }
                self._last_ext_at = now
                return
            base = CELL_STATUS_IDS.index(frame.arbitration_id) * CELLS_PER_MESSAGE
            for k in range(CELLS_PER_MESSAGE):
                n = base + k + 1
                if n > DBC_CELL_COUNT:
                    break
                v = values.get(f"CELL{n:02d}_VOLTAGE_ACT")
                s = values.get(f"CELL{n:02d}_STATUS")
                if v is not None:
                    self._voltages[n - 1] = float(v)
                if s is not None:
                    self._cell_status[n - 1] = int(s)

    def due_frames(self, now: float) -> list[TxFrame]:
        with self._lock:
            if not self._active:
                return []
            if not self._remote and self._release_until and now >= self._release_until:
                # Fernsteuerung abgegeben: Senden endet, Zaehler fangen beim
                # naechsten Einschalten neu an.
                self._active = False
                self._release_until = 0.0
                self._next_due.clear()
                return []
            frames = []
            if now >= self._next_due.get(CONTROL_ID, 0.0):
                frames.append(self._control_frame(now))
                self._next_due[CONTROL_ID] = now + CONTROL_CYCLE_S
            for msg_id in CELL_CONTROL_IDS:
                if now >= self._next_due.get(msg_id, 0.0):
                    frames.append(self._cell_frame(msg_id))
                    self._next_due[msg_id] = now + CELL_CYCLE_S
            return frames

    def shutdown_frames(self) -> list[TxFrame]:
        """Geordnetes Ende (App schliesst, Interface wird entfernt): Ausgang
        sperren und Fernsteuerung abgeben, dreimal fuer den Fall eines
        verlorenen Frames."""
        with self._lock:
            if not self._active:
                return []
            self._remote = False
            self._output = False
            self._mode = MODE_STANDBY
            frames = [self._control_frame(time.monotonic()) for _ in range(3)]
            self._active = False
            return frames

    # -- intern --------------------------------------------------------------

    def _check_cell(self, cell: int) -> None:
        if not 1 <= int(cell) <= self.cell_count:
            raise BattSimValueError(f"Zelle {cell} gibt es nicht (1..{self.cell_count})")

    def _check_voltage(self, volts: float) -> None:
        if not 0.0 <= volts <= self._limit:
            raise BattSimValueError(
                f"Zellspannung {volts:.4f} V ausserhalb 0..{self._limit:.3f} V (Grenzwert)")

    @staticmethod
    def _cell_msg_id(cell: int) -> int:
        return CELL_CONTROL_IDS[(cell - 1) // CELLS_PER_MESSAGE]

    def _mark_cells_due(self) -> None:
        for msg_id in CELL_CONTROL_IDS:
            self._next_due[msg_id] = 0.0

    def _mark_all_due(self) -> None:
        self._next_due[CONTROL_ID] = 0.0
        self._mark_cells_due()

    def _next_alive(self, msg_id: int) -> int:
        # 1..14 wie die alte Firmware beim Senden; die neue Firmware prueft genau diesen Bereich (0 und 15 = Fehler)
        value = self._alive.get(msg_id, 0) % 14 + 1
        self._alive[msg_id] = value
        return value

    def _control_frame(self, now: float) -> TxFrame:
        mode = self._mode
        if self._fail_reset_until:
            if now < self._fail_reset_until:
                mode = MODE_STANDBY
            else:
                self._fail_reset_until = 0.0
        data = self._db.get_message_by_frame_id(CONTROL_ID).encode({
            "ALIVE_CONTROL_BATTSIM": self._next_alive(CONTROL_ID),
            "BATTSIM_REMOTE_CONTROL": REMOTE_REMOTE if self._remote else REMOTE_STANDALONE,
            "BATTSIM_OUTPUT_CONTROL": OUTPUT_ENABLE if self._output else OUTPUT_DISABLE,
            "BATTSIM_MODE_CONTROL": mode,
        })
        return TxFrame(CONTROL_ID, bytes(data))

    def _cell_frame(self, msg_id: int) -> TxFrame:
        message = self._db.get_message_by_frame_id(msg_id)
        values: dict[str, float] = {}
        for sig in message.signals:
            name = sig.name
            if name.startswith("ALIVE_"):
                values[name] = self._next_alive(msg_id)
                continue
            n = int(name[4:6]) - 1   # "CELLnn_..."
            if name.endswith("_VOLTAGE_SET"):
                values[name] = self._setpoints[n] if n < self.cell_count else 0.0
            elif name.endswith("_ENABLE"):
                on = self._enables[n] if n < self.cell_count else False
                values[name] = ENABLE_ON if on else ENABLE_OFF
        return TxFrame(msg_id, bytes(message.encode(values)))
