"""UDS-Client ueber ISO-TP auf einem LabControl-CAN-Interface (FT-1, 0.60.0, FP-9 a: can-isotp + udsoncan).

Der Bus bleibt beim DeviceWorker: empfangen wird ueber einen Abgriff (device_worker.CanTap, Frames direkt aus
dem CanReader-Thread), gesendet ueber DeviceWorker.can_send_threadsafe. Darauf ein isotp.TransportLayer mit
eigenen rx/tx-Funktionen und der udsoncan-Client. Jeder Dienst liefert ein UdsResult statt einer Ausnahme:
positiv (ok), negativ mit NRC (fuer Ablaufschritte mit Erwartung "NRC 0x.."), oder Fehler (Zeitlimit, Bus).
TesterPresent haelt eine Nicht-Default-Session am Leben (eigener Thread, alle 2 s, ohne Antwort).

Qt-frei; ein UdsClient gehoert genau einem Thread (UdsService) -- udsoncan ist nicht thread-sicher.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

import isotp
import udsoncan
from udsoncan.client import Client
from udsoncan.connections import PythonIsoTpConnection
from udsoncan.exceptions import NegativeResponseException, TimeoutException, UnexpectedResponseException

from uds_keys import app_key, device_key

logger = logging.getLogger(__name__)
# udsoncan protokolliert jede negative Antwort als Fehler -- fuer Ablaufschritte mit Erwartung "NRC" ist das
# ein normales Ergebnis; LabControl protokolliert die Ergebnisse selbst (UdsService)
logging.getLogger("UdsClient").setLevel(logging.CRITICAL)
logging.getLogger("isotp").setLevel(logging.WARNING)

TESTER_PRESENT_S = 2.0
UID_DID = 0xF18C

NRC_NAMES = {
    0x10: "generalReject", 0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat", 0x14: "responseTooLong", 0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError", 0x31: "requestOutOfRange", 0x33: "securityAccessDenied",
    0x35: "invalidKey", 0x36: "exceededNumberOfAttempts", 0x37: "requiredTimeDelayNotExpired",
    0x72: "generalProgrammingFailure", 0x78: "requestCorrectlyReceived-ResponsePending",
    0x7E: "subFunctionNotSupportedInActiveSession", 0x7F: "serviceNotSupportedInActiveSession",
}


@dataclass
class UdsResult:
    ok: bool                     # positive Antwort
    nrc: int | None = None       # negative Antwort: NRC
    data: bytes = b""            # Nutzdaten der positiven Antwort (ohne SID/Kennung)
    message: str = ""            # Fehlertext (Zeitlimit, Bus, Dekodierung)
    values: dict = field(default_factory=dict)   # dekodierte Werte (DID, DTC-Liste ...)

    @property
    def negative(self) -> bool:
        return self.nrc is not None

    def describe(self) -> str:
        if self.ok:
            return "positiv"
        if self.nrc is not None:
            return f"NRC 0x{self.nrc:02X} ({NRC_NAMES.get(self.nrc, '?')})"
        return self.message or "Fehler"


class _RawCodec(udsoncan.DidCodec):
    """DID-Daten unveraendert durchreichen; die Laenge nimmt udsoncan aus dem Rest der Antwort."""

    def encode(self, value) -> bytes:  # noqa: D401
        return bytes(value)

    def decode(self, payload: bytes):
        return bytes(payload)

    def __len__(self) -> int:
        raise udsoncan.DidCodec.ReadAllRemainingData


class UdsClient:
    def __init__(self, worker, can_id: str, request_id: int, response_id: int, filler: int = 0xCC,
                 p2_s: float = 1.0, p2_star_s: float = 6.0, extended_ids: bool = False) -> None:
        from device_worker import CanTap

        self._worker = worker
        self._can_id = can_id
        self._extended = extended_ids
        self._tap = CanTap({response_id}, extended=extended_ids)
        worker.add_can_tap(can_id, self._tap)
        mode = isotp.AddressingMode.Normal_29bits if extended_ids else isotp.AddressingMode.Normal_11bits
        address = isotp.Address(mode, txid=request_id, rxid=response_id)
        params = {"tx_padding": filler, "tx_data_min_length": 8, "stmin": 0, "blocksize": 0,
                  "rx_flowcontrol_timeout": 1000, "rx_consecutive_frame_timeout": 1000, "blocking_send": False}
        self._layer = isotp.TransportLayer(rxfn=self._rx, txfn=self._tx, address=address, params=params,
                                           error_handler=self._isotp_error)
        self._conn = PythonIsoTpConnection(self._layer, name=f"uds-{can_id}")
        config = dict(udsoncan.configs.default_client_config)
        config.update({"use_server_timing": False, "p2_timeout": p2_s, "p2_star_timeout": p2_star_s,
                       "request_timeout": max(10.0, p2_star_s + 2.0), "data_identifiers": {"default": _RawCodec},
                       "exception_on_unexpected_response": True, "tolerate_zero_padding": True})
        self._client = Client(self._conn, config=config)
        self._client.open()
        self._session = 0x01
        self._tp_stop = threading.Event()
        self._tp_thread: threading.Thread | None = None
        self._lock = threading.Lock()   # TesterPresent-Thread und Dienste teilen die Verbindung

    # -- ISO-TP-Anbindung -------------------------------------------------------------

    def _rx(self, timeout: float):
        try:
            frame = self._tap.queue.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        return isotp.CanMessage(arbitration_id=frame.arbitration_id, dlc=len(frame.data), data=bytes(frame.data),
                                extended_id=frame.extended)

    def _tx(self, msg) -> None:
        self._worker.can_send_threadsafe(self._can_id, msg.arbitration_id, bytes(msg.data), msg.is_extended_id)

    @staticmethod
    def _isotp_error(exc: Exception) -> None:
        logger.warning("ISO-TP: %s", exc)

    def close(self) -> None:
        self._stop_tester_present()
        try:
            self._client.close()
        finally:
            self._worker.remove_can_tap(self._can_id, self._tap)

    # -- Dienste ----------------------------------------------------------------------

    def _call(self, fn, *args, **kwargs) -> UdsResult:
        with self._lock:
            try:
                response = fn(*args, **kwargs)
            except NegativeResponseException as exc:
                return UdsResult(False, nrc=exc.response.code)
            except TimeoutException as exc:
                return UdsResult(False, message=f"Keine Antwort ({exc})")
            except UnexpectedResponseException as exc:
                return UdsResult(False, message=f"Unerwartete Antwort ({exc})")
            except Exception as exc:  # noqa: BLE001 -- Bus/ISO-TP-Fehler als Ergebnis, nicht als Absturz
                return UdsResult(False, message=str(exc) or exc.__class__.__name__)
        data = bytes(getattr(response, "data", b"") or b"")
        return UdsResult(True, data=data, values={"response": response})

    def session(self, session_id: int) -> UdsResult:
        result = self._call(self._client.change_session, session_id)
        if result.ok:
            self._session = session_id
            if session_id != 0x01:
                self._start_tester_present()
            else:
                self._stop_tester_present()
        return result

    def unlock(self, level: int, master_key: bytes, key_bytes: int) -> UdsResult:
        """Anwendungs-Entsperrung wie neXtBSW: UID lesen, Seed holen (0-Seed = schon offen), Schluessel senden."""
        uid = self.read_did(UID_DID)
        if not uid.ok:
            return uid
        seed_result = self._call(self._client.request_seed, level)
        if not seed_result.ok:
            return seed_result
        seed = bytes(seed_result.values["response"].service_data.seed)
        if not any(seed):
            return UdsResult(True, message="bereits entsperrt")
        key = app_key(device_key(master_key, uid.data), level, seed, key_bytes)
        return self._call(self._client.send_key, level + 1, key)

    def read_did(self, did: int) -> UdsResult:
        result = self._call(self._client.read_data_by_identifier, did)
        if result.ok:
            values = result.values["response"].service_data.values
            result.data = bytes(values.get(did, b""))
        return result

    def write_did(self, did: int, data: bytes) -> UdsResult:
        return self._call(self._client.write_data_by_identifier, did, bytes(data))

    def read_dtcs(self, status_mask: int = 0xFF) -> UdsResult:
        result = self._call(self._client.get_dtc_by_status_mask, status_mask)
        if result.ok:
            dtcs = result.values["response"].service_data.dtcs
            result.values = {"dtcs": [(dtc.id, dtc.status.get_byte_as_int()) for dtc in dtcs]}
        return result

    def clear_dtcs(self, group: int = 0xFFFFFF) -> UdsResult:
        return self._call(self._client.clear_dtc, group)

    def routine(self, rid: int, control: int, data: bytes = b"") -> UdsResult:
        result = self._call(self._client.routine_control, rid, control, bytes(data) or None)
        if result.ok:
            status = result.values["response"].service_data.routine_status_record
            result.data = bytes(status or b"")
        return result

    def reset(self, reset_type: int = 0x01) -> UdsResult:
        result = self._call(self._client.ecu_reset, reset_type)
        if result.ok:
            self._session = 0x01
            self._stop_tester_present()
        return result

    def raw(self, request: bytes, timeout_s: float = 2.0) -> UdsResult:
        """Beliebige Anfrage; data = ganze positive Antwort (mit SID). Antwort "pending" (0x78) wird abgewartet."""
        with self._lock:
            try:
                self._conn.empty_rxqueue()
                self._conn.send(bytes(request))
                deadline = time.monotonic() + timeout_s
                while True:
                    payload = self._conn.wait_frame(timeout=max(0.05, deadline - time.monotonic()),
                                                    exception=True)
                    if payload[:1] == b"\x7f" and len(payload) >= 3:
                        if payload[2] == 0x78 and time.monotonic() < deadline + 5.0:
                            deadline = time.monotonic() + 5.0
                            continue
                        return UdsResult(False, nrc=payload[2], data=bytes(payload))
                    return UdsResult(True, data=bytes(payload))
            except TimeoutException as exc:
                return UdsResult(False, message=f"Keine Antwort ({exc})")
            except Exception as exc:  # noqa: BLE001
                return UdsResult(False, message=str(exc) or exc.__class__.__name__)

    # -- TesterPresent ----------------------------------------------------------------

    def _start_tester_present(self) -> None:
        if self._tp_thread is not None and self._tp_thread.is_alive():
            return
        self._tp_stop.clear()
        self._tp_thread = threading.Thread(target=self._tester_present_loop, name="uds-tester-present", daemon=True)
        self._tp_thread.start()

    def _stop_tester_present(self) -> None:
        self._tp_stop.set()

    def _tester_present_loop(self) -> None:
        while not self._tp_stop.wait(TESTER_PRESENT_S):
            with self._lock:
                try:
                    self._conn.send(b"\x3e\x80")   # suppressPosRspMsgIndicationBit: keine Antwort
                except Exception as exc:  # noqa: BLE001
                    logger.debug("TesterPresent: %s", exc)
