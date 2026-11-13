"""Nachgebildetes UDS-Steuergeraet am simulierten CAN-Bus (FT-1, 0.60.0) -- "LabControl SimECU".

Haengt wie der nachgebildete Batterie-Simulator als Gegenstelle am MockCanBus (on_frame/poll) und spricht
ISO-TP ueber can-isotp. Dienste wie ein neXtBSW-Steuergeraet (Verhalten, keine Kopie): Sessions Default/
Extended (S3 5 s, TesterPresent haelt sie), SecurityAccess Stufe 1 mit HMAC-Verfahren (uds_keys.py) und einem
festen, nur fuer die Simulation gedachten Schluessel, DIDs (UID, aktive Session, Zustand mit Versorgung,
Statistik mit Startzaehler, beschreibbarer Testwert), Fehlerspeicher 19 02 / 14, Routine 0x0301, ECU-Reset.
sim_odx_text() liefert die passende ODX-Beschreibung (eigene, keine aus neXtBSW).
"""
from __future__ import annotations

import collections
import os
import queue
import threading
import time

import isotp

from uds_keys import app_key, device_key

REQUEST_ID, RESPONSE_ID, FUNCTIONAL_ID = 0x7E0, 0x7E8, 0x7DF
SIM_KEY_NAME = "labcontrol-sim"
# NUR Simulation: dieser Schluessel steht absichtlich im Code; er schuetzt kein echtes Geraet.
SIM_MASTER_KEY = bytes.fromhex("5349 4d2d 4f4e 4c59 2d6c 6162 636f 6e74 726f 6c2d 7369 6d2d 6b65 792d 3031 2d2d"
                               .replace(" ", ""))
SIM_UID = bytes.fromhex("4C43 5349 4D45 4355 0001 0002 0003 0004".replace(" ", ""))
S3_S = 5.0
SIM_DTCS = {0x904001: 0x09, 0xC10016: 0x08}   # Code -> Status (testFailed|confirmed bzw. confirmed)
SESSIONS = {0x01: "Default", 0x03: "Extended"}


class SimEcu:
    """Gegenstelle fuer MockCanBus. Die Dienste laufen in einem eigenen Thread."""

    def __init__(self) -> None:
        self.responding = True
        self.session = 0x01
        self.unlocked = False
        self.starts = 1
        self.reset_reason = 1        # 1 = Einschalten
        self.supply_mv = 13800
        self.ram_test = 0
        self.dtcs = dict(SIM_DTCS)
        self.routine_state = 0
        self._seed = b""
        self._attempts = 0
        self._last_request = time.monotonic()
        self._booted = time.monotonic()
        self._rx: "queue.SimpleQueue" = queue.SimpleQueue()
        self._tx: collections.deque = collections.deque()
        self._lock = threading.Lock()
        address = isotp.Address(isotp.AddressingMode.Normal_11bits, txid=RESPONSE_ID, rxid=REQUEST_ID)
        self._layer = isotp.TransportLayer(rxfn=self._rxfn, txfn=self._txfn, address=address,
                                           params={"tx_padding": 0xCC, "tx_data_min_length": 8,
                                                   "blocking_send": False})
        self._layer.start()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="sim-ecu", daemon=True)
        self._thread.start()

    # -- MockCanBus-Gegenstelle --------------------------------------------------------

    def on_frame(self, frame) -> None:
        if self.responding and not frame.extended and frame.arbitration_id in (REQUEST_ID, FUNCTIONAL_ID):
            self._rx.put(frame)

    def poll(self, _now: float):
        out = []
        with self._lock:
            while self._tx:
                out.append(self._tx.popleft())
        return out

    def close(self) -> None:
        self._stop.set()
        self._layer.stop()

    def _rxfn(self, timeout: float):
        try:
            frame = self._rx.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        return isotp.CanMessage(arbitration_id=REQUEST_ID, dlc=len(frame.data), data=bytes(frame.data))

    def _txfn(self, msg) -> None:
        with self._lock:
            self._tx.append((msg.arbitration_id, bytes(msg.data)))

    # -- Dienste -----------------------------------------------------------------------

    def _serve(self) -> None:
        while not self._stop.is_set():
            if self.session != 0x01 and time.monotonic() - self._last_request > S3_S:
                self.session, self.unlocked = 0x01, False
            request = self._layer.recv(block=True, timeout=0.1)
            if not request:
                continue
            self._last_request = time.monotonic()
            response = self.handle(bytes(request))
            if response:
                self._layer.send(response)

    @staticmethod
    def _nrc(sid: int, code: int) -> bytes:
        return bytes([0x7F, sid, code])

    def handle(self, req: bytes) -> bytes:
        sid = req[0]
        if sid == 0x3E:
            return b"" if len(req) > 1 and req[1] & 0x80 else b"\x7e\x00"
        if sid == 0x10 and len(req) == 2:
            if req[1] not in SESSIONS:
                return self._nrc(sid, 0x12)
            self.session = req[1]
            self.unlocked = False
            return bytes([0x50, req[1], 0x00, 0x32, 0x01, 0xF4])
        if sid == 0x11 and len(req) == 2:
            self.starts += 1
            self.reset_reason = 3      # Reset auf Anfrage
            self.session, self.unlocked = 0x01, False
            self._booted = time.monotonic()
            return bytes([0x51, req[1]])
        if sid == 0x22 and len(req) == 3:
            data = self._read(int.from_bytes(req[1:3], "big"))
            return self._nrc(sid, 0x31) if data is None else bytes([0x62]) + req[1:3] + data
        if sid == 0x2E and len(req) >= 3:
            did = int.from_bytes(req[1:3], "big")
            if did != 0xFD00:
                return self._nrc(sid, 0x31)
            if self.session != 0x03:
                return self._nrc(sid, 0x7F)
            if not self.unlocked:
                return self._nrc(sid, 0x33)
            if len(req) != 7:
                return self._nrc(sid, 0x13)
            self.ram_test = int.from_bytes(req[3:7], "big")
            return bytes([0x6E]) + req[1:3]
        if sid == 0x27 and len(req) >= 2:
            return self._security(req)
        if sid == 0x19 and len(req) == 3 and req[1] == 0x02:
            mask = req[2]
            body = b"".join(code.to_bytes(3, "big") + bytes([status]) for code, status in self.dtcs.items()
                            if status & mask)
            return bytes([0x59, 0x02, 0xFF]) + body
        if sid == 0x14 and len(req) == 4:
            group = int.from_bytes(req[1:4], "big")
            if group == 0xFFFFFF:
                self.dtcs.clear()
            elif group in self.dtcs:
                del self.dtcs[group]
            else:
                return self._nrc(sid, 0x31)
            return b"\x54"
        if sid == 0x31 and len(req) >= 4:
            return self._routine(req)
        return self._nrc(sid, 0x11)

    def _read(self, did: int) -> bytes | None:
        uptime_ms = int((time.monotonic() - self._booted) * 1000) & 0xFFFFFFFF
        if did == 0xF18C:
            return SIM_UID
        if did == 0xF186:
            return bytes([self.session])
        if did == 0x0120:
            return (bytes([2]) + self.supply_mv.to_bytes(2, "big") + bytes([0, self.reset_reason])
                    + uptime_ms.to_bytes(4, "big") + (0).to_bytes(2, "big"))
        if did == 0x0130:
            return (int(uptime_ms / 1000).to_bytes(4, "big") + self.starts.to_bytes(4, "big") + bytes([1])
                    + bytes(34))
        if did == 0xFD00:
            return self.ram_test.to_bytes(4, "big")
        return None

    def _security(self, req: bytes) -> bytes:
        sub = req[1]
        if self.session != 0x03:
            return self._nrc(0x27, 0x7F)
        if sub == 0x01:
            self._seed = bytes(4) if self.unlocked else os.urandom(4)
            return bytes([0x67, 0x01]) + self._seed
        if sub == 0x02:
            if not self._seed:
                return self._nrc(0x27, 0x24)
            expected = app_key(device_key(SIM_MASTER_KEY, SIM_UID), 0x01, self._seed, 4)
            self._seed = b""
            if req[2:] != expected:
                self._attempts += 1
                return self._nrc(0x27, 0x36 if self._attempts >= 3 else 0x35)
            self._attempts = 0
            self.unlocked = True
            return b"\x67\x02"
        return self._nrc(0x27, 0x12)

    def _routine(self, req: bytes) -> bytes:
        control, rid = req[1], int.from_bytes(req[2:4], "big")
        if rid != 0x0301:
            return self._nrc(0x31, 0x31)
        if self.session != 0x03:
            return self._nrc(0x31, 0x7F)
        if not self.unlocked:
            return self._nrc(0x31, 0x33)
        if control == 0x01:
            self.routine_state = 1
        elif control == 0x02:
            self.routine_state = 0
        elif control != 0x03:
            return self._nrc(0x31, 0x12)
        return bytes([0x71, control]) + req[2:4] + bytes([self.routine_state, 0x00])


# -- ODX der Nachbildung -----------------------------------------------------------------

def _state_id(name: str) -> str:
    """Zustands-ID wie im diaggen-Export: Sessions unter "Session.", Security-Stufen unter "Security."."""
    return f"Session.{name}" if name in SESSIONS.values() else f"Security.{name}"

def _dop(ident: str, name: str, bits: int, unit: str = "", texttable: dict | None = None,
         base: str = "A_UINT32") -> str:
    compu = "<COMPU-METHOD><CATEGORY>IDENTICAL</CATEGORY></COMPU-METHOD>"
    if texttable:
        scales = "".join(f"<COMPU-SCALE><LOWER-LIMIT>{k}</LOWER-LIMIT><UPPER-LIMIT>{k}</UPPER-LIMIT>"
                         f"<COMPU-CONST><VT>{v}</VT></COMPU-CONST></COMPU-SCALE>" for k, v in texttable.items())
        compu = (f"<COMPU-METHOD><CATEGORY>TEXTTABLE</CATEGORY><COMPU-INTERNAL-TO-PHYS><COMPU-SCALES>{scales}"
                 f"</COMPU-SCALES></COMPU-INTERNAL-TO-PHYS></COMPU-METHOD>")
    unit_ref = f'<UNIT-REF ID-REF="SIM.UNIT.{unit}"/>' if unit else ""
    return (f'<DATA-OBJECT-PROP ID="SIM.DOP.{ident}"><SHORT-NAME>{name}</SHORT-NAME>{compu}'
            f'<DIAG-CODED-TYPE BASE-DATA-TYPE="{base}" xsi:type="STANDARD-LENGTH-TYPE"><BIT-LENGTH>{bits}'
            f'</BIT-LENGTH></DIAG-CODED-TYPE><PHYSICAL-TYPE BASE-DATA-TYPE="A_UINT32"/>{unit_ref}</DATA-OBJECT-PROP>')


SIM_DIDS = [  # (DID, Name, [(Element, Byte, Bits, Einheit, Wertetabelle, Basistyp)], schreibbar)
    (0xF18C, "EcuSerialNumber", [("chipUid", 0, 128, "", None, "A_BYTEFIELD")], False),
    (0xF186, "ActiveDiagnosticSession", [("activeSession", 0, 8, "", SESSIONS, "A_UINT32")], False),
    (0x0120, "EcuConditions", [("ecuState", 0, 8, "", None, "A_UINT32"), ("supply", 1, 16, "mV", None, "A_UINT32"),
                               ("supplyQlf", 3, 8, "", None, "A_UINT32"), ("resetReason", 4, 8, "", None, "A_UINT32"),
                               ("uptime", 5, 32, "ms", None, "A_UINT32"), ("tsVoltage", 9, 16, "mV", None, "A_UINT32")],
     False),
    (0x0130, "EcuStatistics", [("operatingTime", 0, 32, "s", None, "A_UINT32"), ("starts", 4, 32, "", None, "A_UINT32"),
                               ("persistent", 8, 8, "", {0: "THIS_START_ONLY", 1: "STORED"}, "A_UINT32")], False),
    (0xFD00, "RamTestValue", [("ramTestValue", 0, 32, "", None, "A_UINT32")], True),
]
SIM_DID_SIZES = {0xF18C: 16, 0xF186: 1, 0x0120: 11, 0x0130: 43, 0xFD00: 4}


def sim_odx_text() -> str:
    parts: list[str] = []
    dops: list[str] = []
    structs: list[str] = []
    requests: list[str] = []
    responses: list[str] = []

    def coded(name: str, pos: int, value: int, bits: int, semantic: str) -> str:
        return (f'<PARAM SEMANTIC="{semantic}" xsi:type="CODED-CONST"><SHORT-NAME>{name}</SHORT-NAME>'
                f'<BYTE-POSITION>{pos}</BYTE-POSITION><CODED-VALUE>{value}</CODED-VALUE>'
                f'<DIAG-CODED-TYPE BASE-DATA-TYPE="A_UINT32" xsi:type="STANDARD-LENGTH-TYPE"><BIT-LENGTH>{bits}'
                f'</BIT-LENGTH></DIAG-CODED-TYPE></PARAM>')

    def service(name: str, semantic: str, req_params: str, resp_params: str, pre: list[str], sds: str = "") -> None:
        refs = "".join(f'<PRE-CONDITION-STATE-REF ID-REF="SIM.STATE.{_state_id(p)}"/>' for p in pre)
        parts.append(f'<DIAG-SERVICE ID="SIM.DS.{name}" SEMANTIC="{semantic}"><SHORT-NAME>{name}</SHORT-NAME>{sds}'
                     f'<PRE-CONDITION-STATE-REFS>{refs}</PRE-CONDITION-STATE-REFS>'
                     f'<REQUEST-REF ID-REF="SIM.RQ.{name}"/><POS-RESPONSE-REFS><POS-RESPONSE-REF ID-REF="SIM.PR.{name}"/>'
                     f'</POS-RESPONSE-REFS></DIAG-SERVICE>')
        requests.append(f'<REQUEST ID="SIM.RQ.{name}"><SHORT-NAME>{name}</SHORT-NAME><PARAMS>{req_params}</PARAMS></REQUEST>')
        responses.append(f'<POS-RESPONSE ID="SIM.PR.{name}"><SHORT-NAME>{name}</SHORT-NAME><PARAMS>{resp_params}'
                         f'</PARAMS></POS-RESPONSE>')

    for sid, sname in SESSIONS.items():
        service(f"Session_{sname}", "SESSION", coded("SID_RQ", 0, 0x10, 8, "SERVICE-ID") +
                coded("Session", 1, sid, 8, "SUBFUNCTION"), coded("SID_PR", 0, 0x50, 8, "SERVICE-ID"), [])
    dops.append(_dop("Seed", "Seed", 32, base="A_BYTEFIELD"))
    dops.append(_dop("Key", "Key", 32, base="A_BYTEFIELD"))
    value = '<PARAM SEMANTIC="DATA" xsi:type="VALUE"><SHORT-NAME>{n}</SHORT-NAME><BYTE-POSITION>{p}</BYTE-POSITION>' \
            '<DOP-REF ID-REF="SIM.{ref}"/></PARAM>'
    service("RequestSeed_Application", "SECURITY", coded("SID_RQ", 0, 0x27, 8, "SERVICE-ID") +
            coded("SecurityAccessType", 1, 1, 8, "SUBFUNCTION"),
            coded("SID_PR", 0, 0x67, 8, "SERVICE-ID") + value.format(n="Seed", p=2, ref="DOP.Seed"), ["Extended"],
            "<SDGS><SDG><SD>hmac-sha256</SD><SD>3</SD><SD>10000</SD></SDG></SDGS>")
    service("SendKey_Application", "SECURITY", coded("SID_RQ", 0, 0x27, 8, "SERVICE-ID") +
            coded("SecurityAccessType", 1, 2, 8, "SUBFUNCTION") + value.format(n="Key", p=2, ref="DOP.Key"),
            coded("SID_PR", 0, 0x67, 8, "SERVICE-ID"), ["Extended"])
    for did, name, elements, writable in SIM_DIDS:
        subs = ""
        for el_name, pos, bits, unit, table, base in elements:
            dops.append(_dop(f"{name}.{el_name}", f"{name}_{el_name}", bits, unit, table, base))
            subs += (f'<PARAM xsi:type="VALUE"><SHORT-NAME>{el_name}</SHORT-NAME><BYTE-POSITION>{pos}</BYTE-POSITION>'
                     f'<DOP-REF ID-REF="SIM.DOP.{name}.{el_name}"/></PARAM>')
        structs.append(f'<STRUCTURE ID="SIM.STRUCT.{name}"><SHORT-NAME>{name}</SHORT-NAME>'
                       f'<BYTE-SIZE>{SIM_DID_SIZES[did]}</BYTE-SIZE><PARAMS>{subs}</PARAMS></STRUCTURE>')
        service(f"Read_{name}", "DATA-READ", coded("SID_RQ", 0, 0x22, 8, "SERVICE-ID") +
                coded("DataIdentifier", 1, did, 16, "ID"), coded("SID_PR", 0, 0x62, 8, "SERVICE-ID") +
                coded("DataIdentifier", 1, did, 16, "ID") + value.format(n=name, p=3, ref=f"STRUCT.{name}"),
                ["Default", "Extended"])
        if writable:
            service(f"Write_{name}", "DATA-WRITE", coded("SID_RQ", 0, 0x2E, 8, "SERVICE-ID") +
                    coded("DataIdentifier", 1, did, 16, "ID") + value.format(n=name, p=3, ref=f"STRUCT.{name}"),
                    coded("SID_PR", 0, 0x6E, 8, "SERVICE-ID"), ["Extended", "Application"])
    for step, control in (("Start", 1), ("Stop", 2), ("Results", 3)):
        service(f"LedSelfTest_{step}", "ROUTINE", coded("SID_RQ", 0, 0x31, 8, "SERVICE-ID") +
                coded("RoutineControlType", 1, control, 8, "SUBFUNCTION") + coded("RoutineIdentifier", 2, 0x0301, 16, "ID"),
                coded("SID_PR", 0, 0x71, 8, "SERVICE-ID"), ["Extended", "Application"])
    dtcs = "".join(f'<DTC ID="SIM.DTC.{c:06X}"><SHORT-NAME>Sim{c:06X}</SHORT-NAME><TROUBLE-CODE>{c}</TROUBLE-CODE>'
                   f'<TEXT>Simulierter Fehler {c:06X}</TEXT><LEVEL>1</LEVEL></DTC>' for c in SIM_DTCS)
    comparams = "".join(f'<COMPARAM-REF ID-REF="{k}"><SIMPLE-VALUE>{v}</SIMPLE-VALUE></COMPARAM-REF>' for k, v in (
        ("ISO_15765_2.CP_CanPhysReqId", REQUEST_ID), ("ISO_15765_2.CP_CanRespUSDTId", RESPONSE_ID),
        ("ISO_15765_2.CP_CanFuncReqId", FUNCTIONAL_ID), ("ISO_15765_2.CP_CanFillerByte", 0xCC),
        ("ISO_15765_3.CP_P2Max", 50000), ("ISO_15765_3.CP_P2Star", 5000000),
        ("ISO_11898_2_DWCAN.CP_Baudrate", 500000)))
    states = "".join(f'<STATE ID="SIM.STATE.{_state_id(s)}"><SHORT-NAME>{s}</SHORT-NAME></STATE>'
                     for s in ("Default", "Extended", "Application"))
    units = "".join(f'<UNIT ID="SIM.UNIT.{u}"><SHORT-NAME>{u}</SHORT-NAME><DISPLAY-NAME>{u}</DISPLAY-NAME></UNIT>'
                    for u in ("mV", "ms", "s"))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<ODX xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'MODEL-VERSION="2.2.0"><DIAG-LAYER-CONTAINER ID="SIM.DLC"><SHORT-NAME>labcontrol_sim_ecu</SHORT-NAME>'
            '<BASE-VARIANTS><BASE-VARIANT ID="SIM.BV"><SHORT-NAME>labcontrol_sim_ecu</SHORT-NAME>'
            f'<DIAG-DATA-DICTIONARY-SPEC><DTC-DOPS><DTC-DOP ID="SIM.DOP.DTC"><SHORT-NAME>DTC</SHORT-NAME><DTCS>{dtcs}'
            f'</DTCS></DTC-DOP></DTC-DOPS><DATA-OBJECT-PROPS>{"".join(dops)}</DATA-OBJECT-PROPS>'
            f'<STRUCTURES>{"".join(structs)}</STRUCTURES><UNIT-SPEC><UNITS>{units}</UNITS></UNIT-SPEC>'
            f'</DIAG-DATA-DICTIONARY-SPEC><DIAG-COMMS>{"".join(parts)}</DIAG-COMMS>'
            f'<REQUESTS>{"".join(requests)}</REQUESTS><POS-RESPONSES>{"".join(responses)}</POS-RESPONSES>'
            f'<STATE-CHARTS><STATE-CHART ID="SIM.SC"><SHORT-NAME>Sessions</SHORT-NAME><STATES>{states}</STATES>'
            f'</STATE-CHART></STATE-CHARTS><COMPARAM-REFS>{comparams}</COMPARAM-REFS>'
            '</BASE-VARIANT></BASE-VARIANTS></DIAG-LAYER-CONTAINER></ODX>\n')


def sim_odx():
    import xml.etree.ElementTree as ET

    import uds_odx
    return uds_odx.parse(ET.fromstring(sim_odx_text()), source="builtin:sim")
