"""Nachgebildetes XCP-Steuergeraet am simulierten CAN-Bus (0.64.0, FT-10): Gegenstelle fuer MockCanBus.

Eigene A2L (sim_a2l_text, im Format von neXtBSW tools/a2lgen), Speicher ab 0x20000000, IDs 0x7F0/0x7F1 wie das
neXtBSW-Demo. Messgroessen aendern sich mit der Zeit und sind schreibgeschuetzt (ERR_WRITE_PROTECTED), Parameter
sind schreibbar. Kein DAQ. Qt-frei.
"""
from __future__ import annotations

import collections
import struct
import threading
import time

MASTER_ID, SLAVE_ID = 0x7F0, 0x7F1
BASE, SIZE = 0x20000000, 0x200
ADDR_COUNTER, ADDR_SUPPLY, ADDR_TEMP, ADDR_STATE = 0x20000000, 0x20000004, 0x20000006, 0x20000008
ADDR_GAIN, ADDR_TABLE, ADDR_EPK = 0x20000040, 0x20000044, 0x20000100
EPK = "labcontrol_sim XCP 1.0"
WRITABLE = (ADDR_GAIN, ADDR_TABLE + 8)     # [Beginn, Ende) der Parameter


def sim_a2l_text() -> str:
    return f'''ASAP2_VERSION 1 61
/* Nachbildung LabControl (xcp_sim.py) */
/begin PROJECT labcontrol_sim "LabControl XCP-Nachbildung"
  /begin MODULE labcontrol_sim "XCP on CAN"
    /begin MOD_PAR "sim"
      EPK "{EPK}"
      ADDR_EPK 0x{ADDR_EPK:08X}
    /end MOD_PAR
    /begin MOD_COMMON ""
      BYTE_ORDER MSB_LAST
    /end MOD_COMMON
    /begin IF_DATA XCP
      /begin PROTOCOL_LAYER
        0x0100
        200 200 200 200 200 200 200
        8 8
        BYTE_ORDER_MSB_LAST
        ADDRESS_GRANULARITY_BYTE
      /end PROTOCOL_LAYER
      /begin XCP_ON_CAN
        0x0100
        CAN_ID_MASTER 0x{MASTER_ID:X}
        CAN_ID_SLAVE 0x{SLAVE_ID:X}
        BAUDRATE 500000
        MAX_DLC_REQUIRED
      /end XCP_ON_CAN
    /end IF_DATA
    /begin COMPU_METHOD CM_IDENTITY "phys = stored"
      RAT_FUNC "%12.6" ""
      COEFFS 0 1 0 0 0 1
    /end COMPU_METHOD
    /begin COMPU_METHOD CM_DECI "phys = stored / 10"
      RAT_FUNC "%6.1" "degC"
      COEFFS 0 10 0 0 0 1
    /end COMPU_METHOD
    /begin COMPU_METHOD CM_STATE "Zustand"
      TAB_VERB "%.0" ""
      COMPU_TAB_REF VTAB_STATE
    /end COMPU_METHOD
    /begin COMPU_VTAB VTAB_STATE "Zustand" TAB_VERB 3
      0 "INIT"
      1 "RUN"
      2 "SAFE"
    /end COMPU_VTAB
    /begin RECORD_LAYOUT RL_FLOAT32_IEEE
      FNC_VALUES 1 FLOAT32_IEEE ROW_DIR DIRECT
    /end RECORD_LAYOUT
    /begin RECORD_LAYOUT RL_UWORD
      FNC_VALUES 1 UWORD ROW_DIR DIRECT
    /end RECORD_LAYOUT
    /begin MEASUREMENT Sim.Counter "laeuft alle 10 ms"
      ULONG CM_IDENTITY 0 0 0 4294967295
      ECU_ADDRESS 0x{ADDR_COUNTER:08X}
    /end MEASUREMENT
    /begin MEASUREMENT Sim.Supply_mV "Versorgung"
      UWORD CM_IDENTITY 0 0 0 65535
      ECU_ADDRESS 0x{ADDR_SUPPLY:08X}
      PHYS_UNIT "mV"
    /end MEASUREMENT
    /begin MEASUREMENT Sim.Temperature "Temperatur"
      SWORD CM_DECI 0 0 -40 150
      ECU_ADDRESS 0x{ADDR_TEMP:08X}
    /end MEASUREMENT
    /begin MEASUREMENT Sim.State "Zustand"
      UBYTE CM_STATE 0 0 0 2
      ECU_ADDRESS 0x{ADDR_STATE:08X}
    /end MEASUREMENT
    /begin CHARACTERISTIC Sim.Gain "Verstaerkung"
      VALUE 0x{ADDR_GAIN:08X} RL_FLOAT32_IEEE 0 CM_IDENTITY 0 10
    /end CHARACTERISTIC
    /begin CHARACTERISTIC Sim.Table "Wertblock"
      VAL_BLK 0x{ADDR_TABLE:08X} RL_UWORD 0 CM_IDENTITY 0 1000
      NUMBER 4
    /end CHARACTERISTIC
  /end MODULE
/end PROJECT
'''


class SimXcpEcu:
    """Antwortet auf CONNECT, DISCONNECT, GET_STATUS, SHORT_UPLOAD, SET_MTA, UPLOAD, DOWNLOAD."""

    def __init__(self) -> None:
        self.memory = bytearray(SIZE)
        self.connected = False
        self.responding = True
        self._mta = BASE
        self._start = time.monotonic()
        self._tx: collections.deque = collections.deque()
        self._lock = threading.Lock()
        struct.pack_into("<H", self.memory, ADDR_SUPPLY - BASE, 13800)
        struct.pack_into("<h", self.memory, ADDR_TEMP - BASE, 235)
        self.memory[ADDR_STATE - BASE] = 1
        struct.pack_into("<f", self.memory, ADDR_GAIN - BASE, 1.0)
        struct.pack_into("<4H", self.memory, ADDR_TABLE - BASE, 10, 20, 30, 40)
        self.memory[ADDR_EPK - BASE:ADDR_EPK - BASE + len(EPK)] = EPK.encode("ascii")

    # -- MockCanBus-Gegenstelle --------------------------------------------------------------

    def on_frame(self, frame) -> None:
        if not self.responding or frame.extended or frame.arbitration_id != MASTER_ID or not frame.data:
            return
        response = self.handle(bytes(frame.data))
        if response is not None:
            with self._lock:
                self._tx.append((SLAVE_ID, response + b"\x00" * (8 - len(response))))

    def poll(self, _now: float):
        out = []
        with self._lock:
            while self._tx:
                out.append(self._tx.popleft())
        return out

    def close(self) -> None:
        self.responding = False

    # -- Protokoll --------------------------------------------------------------------------

    def _tick(self) -> None:
        ticks = int((time.monotonic() - self._start) * 100) & 0xFFFFFFFF
        struct.pack_into("<I", self.memory, ADDR_COUNTER - BASE, ticks)

    def _span_ok(self, address: int, n: int) -> bool:
        return BASE <= address and address + n <= BASE + SIZE

    def handle(self, req: bytes) -> bytes | None:
        cmd = req[0]
        if cmd == 0xFF:
            self.connected = True
            return bytes([0xFF, 0x01, 0x00, 8, 8, 0, 1, 1])
        if not self.connected:
            return None                       # getrennt: ein XCP-Slave antwortet nur auf CONNECT
        self._tick()
        if cmd == 0xFE:
            self.connected = False
            return b"\xFF"
        if cmd == 0xFD:
            return bytes([0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
        if cmd == 0xF6:
            self._mta = struct.unpack_from("<I", req, 4)[0]
            return b"\xFF"
        if cmd in (0xF4, 0xF5):
            n = req[1]
            address = struct.unpack_from("<I", req, 4)[0] if cmd == 0xF4 else self._mta
            if n > 7 or not self._span_ok(address, n):
                return bytes([0xFE, 0x22])
            data = bytes(self.memory[address - BASE:address - BASE + n])
            if cmd == 0xF5:
                self._mta = address + n
            return b"\xFF" + data
        if cmd == 0xF0:
            n = req[1]
            if n > 6 or not self._span_ok(self._mta, n):
                return bytes([0xFE, 0x22])
            if not (WRITABLE[0] <= self._mta and self._mta + n <= WRITABLE[1]):
                return bytes([0xFE, 0x23])
            self.memory[self._mta - BASE:self._mta - BASE + n] = req[2:2 + n]
            self._mta += n
            return b"\xFF"
        return bytes([0xFE, 0x20])
