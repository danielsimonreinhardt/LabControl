"""XCP-on-CAN-Master (0.64.0, FT-10, FP-28 a): eigene Umsetzung fuer das A2L-Subset von neXtBSW tools/a2lgen.

Polling-Messen und Verstellen: CONNECT, DISCONNECT, GET_STATUS, SHORT_UPLOAD, SET_MTA + UPLOAD, DOWNLOAD (bei
mehr als einem Frame mehrere DOWNLOAD hintereinander -- nicht atomar, das Steuergeraet uebernimmt jeden sofort).
Kein DAQ: die Werte haben keine Zeitstempel des Steuergeraets, und Polling belastet es.

Der Bus bleibt beim DeviceWorker (wie UDS): Empfang ueber device_worker.CanTap, Senden ueber
DeviceWorker.can_send_threadsafe. Qt-frei; ein XcpClient gehoert genau einem Thread (XcpService).
"""
from __future__ import annotations

import logging
import queue
import struct

logger = logging.getLogger(__name__)

CONNECT, DISCONNECT, GET_STATUS = 0xFF, 0xFE, 0xFD
SET_MTA, UPLOAD, SHORT_UPLOAD, DOWNLOAD = 0xF6, 0xF5, 0xF4, 0xF0
ERRORS = {
    0x00: "ERR_CMD_SYNCH", 0x10: "ERR_CMD_BUSY", 0x20: "ERR_CMD_UNKNOWN", 0x21: "ERR_CMD_SYNTAX",
    0x22: "ERR_OUT_OF_RANGE", 0x23: "ERR_WRITE_PROTECTED", 0x24: "ERR_ACCESS_DENIED", 0x25: "ERR_ACCESS_LOCKED",
    0x26: "ERR_PAGE_NOT_VALID", 0x27: "ERR_MODE_NOT_VALID", 0x28: "ERR_SEGMENT_NOT_VALID", 0x29: "ERR_SEQUENCE",
    0x31: "ERR_GENERIC",
}


class XcpError(Exception):
    """Fehlerantwort des Steuergeraets (code = XCP-Fehlercode) oder keine Antwort (code = None)."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class XcpClient:
    def __init__(self, worker, can_id: str, master_id: int, slave_id: int, extended_ids: bool = False,
                 timeout_s: float = 0.2, pad: bool = True) -> None:
        from device_worker import CanTap
        self._worker = worker
        self._can_id = can_id
        self._master_id = master_id
        self._extended = extended_ids
        self._timeout = max(0.05, timeout_s)
        self._pad = pad
        self._tap = CanTap({slave_id}, extended=extended_ids)
        worker.add_can_tap(can_id, self._tap)
        self.connected = False
        self.max_cto = 8
        self.little_endian_protocol = True

    def close(self) -> None:
        try:
            if self.connected:
                self.disconnect()
        except XcpError:
            pass
        finally:
            self._worker.remove_can_tap(self._can_id, self._tap)

    # -- Kommandos ------------------------------------------------------------------------------

    def command(self, data: bytes) -> bytes:
        while True:   # alte Antworten (Zeitlimit eines frueheren Kommandos) verwerfen
            try:
                self._tap.queue.get_nowait()
            except queue.Empty:
                break
        frame = bytes(data) + (b"\x00" * (8 - len(data)) if self._pad else b"")
        try:
            self._worker.can_send_threadsafe(self._can_id, self._master_id, frame, self._extended)
        except Exception as exc:  # noqa: BLE001 -- Bus weg
            raise XcpError(f"Senden fehlgeschlagen: {exc}") from None
        try:
            response = bytes(self._tap.queue.get(timeout=self._timeout).data)
        except queue.Empty:
            raise XcpError(f"keine Antwort auf 0x{data[0]:02X} innerhalb {self._timeout * 1000:.0f} ms") from None
        if not response:
            raise XcpError("leere Antwort")
        if response[0] == 0xFE:
            code = response[1] if len(response) > 1 else 0x31
            raise XcpError(f"{ERRORS.get(code, 'Fehler')} (0x{code:02X})", code)
        if response[0] != 0xFF:
            raise XcpError(f"unerwartete Antwort 0x{response[0]:02X}")
        return response

    def _dword(self, value: int) -> bytes:
        return struct.pack("<I" if self.little_endian_protocol else ">I", value & 0xFFFFFFFF)

    def connect(self) -> dict:
        response = self.command(bytes([CONNECT, 0x00]))
        comm_mode = response[2] if len(response) > 2 else 0
        self.little_endian_protocol = not bool(comm_mode & 0x01)
        self.max_cto = response[3] if len(response) > 3 and response[3] else 8
        self.connected = True
        return {"resource": response[1] if len(response) > 1 else 0, "max_cto": self.max_cto,
                "byte_order": "intel" if self.little_endian_protocol else "motorola"}

    def disconnect(self) -> None:
        self.connected = False
        self.command(bytes([DISCONNECT]))

    def get_status(self) -> bytes:
        return self.command(bytes([GET_STATUS]))

    def read(self, address: int, size: int) -> bytes:
        if size <= self.max_cto - 1:
            response = self.command(bytes([SHORT_UPLOAD, size, 0, 0]) + self._dword(address))
            return response[1:1 + size]
        self.command(bytes([SET_MTA, 0, 0, 0]) + self._dword(address))
        data = b""
        while len(data) < size:
            n = min(self.max_cto - 1, size - len(data))
            data += self.command(bytes([UPLOAD, n]))[1:1 + n]
        return data

    def write(self, address: int, data: bytes) -> None:
        self.command(bytes([SET_MTA, 0, 0, 0]) + self._dword(address))
        chunk = self.max_cto - 2
        for start in range(0, len(data), chunk):
            part = data[start:start + chunk]
            self.command(bytes([DOWNLOAD, len(part)]) + part)
