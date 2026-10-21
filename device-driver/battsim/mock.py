"""Nachgebildeter Batterie-Simulator fuer Simulationsmodus und Pruefskripte.

Anders als die uebrigen mock.py-Dateien ersetzt diese NICHT den Treiber, sondern
das Geraet: ``SimulatedBattSim`` haengt als Gegenstelle an einem MockCanBus
(can_bus/mock.py, attach_peer) und antwortet auf CAN-Ebene. Der echte
Treiber (battsim/driver.py) laeuft damit unveraendert mit -- wie beim
SCPI-Oszilloskop (unit_mso1000hd/mock.py).

Nachgebildet ist das Verhalten der bestehenden Firmware, wie es aus dem
Simulink-Modell gelesen wurde (Workspace BatterieSimulator,
doc/can-verhalten.md), einschliesslich ihrer Abweichungen von der DBC:

- Zustaende STANDBY (1), REMOTE (3) mit STOP/RUN/DEBOUNCE_FAIL, FAIL (5).
- 0x130 gilt als ausgefallen, wenn es TIMEOUT_S lang fehlt (Firmware: 1,5-3 s);
  dann REMOTE=3, MODE=7, OUTPUT=3. In RUN folgt nach DEBOUNCE_FAIL_S FAIL.
- Zellfreigabe: jeder Wert != 0 gilt als ein (Cast nach boolean).
- Zellstatus: 1 = aus, 0 = ein.
- Fehlende Zellbotschaften: letzte Werte bleiben stehen.
"""
from __future__ import annotations

import random
import threading
import time

from battsim.driver import (
    CELL_CONTROL_IDS, CELL_COUNT, CELL_STATUS_IDS, CELLS_PER_MESSAGE, CONTROL_ID, DBC_CELL_COUNT,
    STATUS_ID, load_database,
)

TIMEOUT_S = 2.0            # Mitte des Firmware-Bereichs 1,5-3 s
DEBOUNCE_FAIL_S = 3.0      # chart_318: DEBOUNCE_FAIL -> FAIL after(3, sec)
STATUS_CYCLE_S = 0.1       # CAN_TX_TASK1_TIME
NOISE_V = 0.0005           # Messrauschen der Istwerte

ST_STANDBY, ST_REMOTE, ST_FAIL = 1, 3, 5


class SimulatedBattSim:
    def __init__(self, cell_count: int = CELL_COUNT, seed: int | None = None) -> None:
        self._db = load_database()
        self._lock = threading.Lock()
        self._rng = random.Random(seed)
        self.cell_count = cell_count
        self.state = ST_STANDBY
        self.sub = "STOP"              # in REMOTE: STOP / RUN / DEBOUNCE_FAIL
        self._debounce_since = 0.0
        self._remote = self._mode = self._output = 0
        self._last_ctrl_at: float | None = None
        self._setpoints = [0.0] * DBC_CELL_COUNT
        self._enable_raw = [0] * DBC_CELL_COUNT
        self._next_status_at = 0.0
        self._alive = 0
        self.responding = True         # False = Geraet aus / Kabel ab (fuer Tests)
        self.overload = False          # Ueberlast der Versorgung (fuer Tests)

    # -- Gegenstelle am MockCanBus ------------------------------------------

    def on_frame(self, frame) -> None:
        if not self.responding or frame.extended:
            return
        try:
            values = self._db.decode_message(frame.arbitration_id, frame.data, decode_choices=False)
        except Exception:  # noqa: BLE001 -- fremde/kaputte Frames ignorieren
            return
        with self._lock:
            if frame.arbitration_id == CONTROL_ID:
                self._remote = int(values["BATTSIM_REMOTE_CONTROL"])
                self._mode = int(values["BATTSIM_MODE_CONTROL"])
                self._output = int(values["BATTSIM_OUTPUT_CONTROL"])
                self._last_ctrl_at = time.monotonic()
            elif frame.arbitration_id in CELL_CONTROL_IDS:
                base = CELL_CONTROL_IDS.index(frame.arbitration_id) * CELLS_PER_MESSAGE
                for k in range(CELLS_PER_MESSAGE):
                    n = base + k + 1
                    if n > DBC_CELL_COUNT:
                        break
                    if f"CELL{n:02d}_VOLTAGE_SET" in values:
                        self._setpoints[n - 1] = float(values[f"CELL{n:02d}_VOLTAGE_SET"])
                        self._enable_raw[n - 1] = int(values[f"CELL{n:02d}_ENABLE"])

    def poll(self, now: float) -> list[tuple[int, bytes]]:
        if not self.responding:
            return []
        with self._lock:
            self._step(now)
            if now < self._next_status_at:
                return []
            self._next_status_at = now + STATUS_CYCLE_S
            return self._status_frames()

    # -- Auswertung fuer Tests ----------------------------------------------

    @property
    def pwm_enabled(self) -> bool:
        return self.state == ST_REMOTE and self.sub in ("RUN", "DEBOUNCE_FAIL")

    def cell_output_v(self, cell: int) -> float:
        with self._lock:
            return self._cell_voltage(cell - 1, noise=False)

    # -- Firmware-Nachbildung -------------------------------------------------

    def _qualified(self, now: float) -> tuple[int, int, int]:
        valid = self._last_ctrl_at is not None and now - self._last_ctrl_at <= TIMEOUT_S
        if not valid:
            return 3, 7, 3      # EvalRemoteControl / EvalModeControl / EvalRemoteOutputControl
        return self._remote, self._mode, self._output

    def _step(self, now: float) -> None:
        rc, mc, oc = self._qualified(now)
        valid = (rc, mc, oc) != (3, 7, 3)
        if self.state == ST_STANDBY:
            # MODE=3 (dynamisches Zellmodell) bildet die Nachbildung nicht ab.
            if rc == 2 and mc == 2 and not self.overload:
                self.state, self.sub = ST_REMOTE, "STOP"
        elif self.state == ST_REMOTE:
            if (rc == 2 and mc == 1) or rc == 1:
                self.state = ST_STANDBY
                return
            if self.overload:
                self.state = ST_FAIL
                return
            if self.sub == "STOP" and oc == 2:
                self.sub = "RUN"
            elif self.sub == "RUN":
                if not valid:
                    self.sub, self._debounce_since = "DEBOUNCE_FAIL", now
                elif oc == 1:
                    self.sub = "STOP"
            elif self.sub == "DEBOUNCE_FAIL":
                if valid:
                    self.sub = "RUN"
                elif now - self._debounce_since >= DEBOUNCE_FAIL_S:
                    self.state = ST_FAIL
        elif self.state == ST_FAIL:
            if rc == 2 and mc == 1:
                self.state = ST_STANDBY

    def _cell_on(self, i: int) -> bool:
        # chart_318 Enable<n>: Status 3/4, Freigabe (boolean) und OUTPUT == 2;
        # PWM muss laufen, sonst liegt am Kanal ohnehin nichts an.
        return (self.pwm_enabled and self._enable_raw[i] != 0 and self._output == 2
                and i < self.cell_count)

    def _cell_voltage(self, i: int, noise: bool = True) -> float:
        if not self._cell_on(i):
            return 0.0
        v = min(max(self._setpoints[i], 0.0), 5.0)
        if noise:
            v += self._rng.uniform(-NOISE_V, NOISE_V)
        return round(max(v, 0.0), 3)    # RoundToMillivolts

    def _status_frames(self) -> list[tuple[int, bytes]]:
        self._alive = (self._alive + 1) % 16
        frames = [(STATUS_ID, bytes(self._db.get_message_by_frame_id(STATUS_ID).encode({
            "ALIVE_STATUS_BATTSIM": self._alive,
            "BATTSIM_STATUS": self.state,
            "BATTSIM_OUTPUT_STATUS": 2 if self.pwm_enabled else 1,
        })))]
        for msg_id in CELL_STATUS_IDS:
            message = self._db.get_message_by_frame_id(msg_id)
            values: dict[str, float] = {}
            for sig in message.signals:
                if sig.name.startswith("ALIVE_"):
                    values[sig.name] = self._alive
                    continue
                n = int(sig.name[4:6]) - 1
                if sig.name.endswith("_VOLTAGE_ACT"):
                    values[sig.name] = self._cell_voltage(n)
                else:
                    values[sig.name] = 0 if self._cell_on(n) else 1
            frames.append((msg_id, bytes(message.encode(values))))
        return frames
