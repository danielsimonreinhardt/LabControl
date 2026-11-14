"""XCP-Dienst der App (0.64.0, FT-10): liest Messgroessen und schreibt Parameter am Pruefling ueber XCP on CAN.

Wie UdsService: ein eigener Thread arbeitet die Befehle nacheinander ab, das Ergebnis kommt als Qt-Signal
`finished(request_id, XcpResult)`. Pruefling aus Settings.dut_config: "a2l" (Pfad, "builtin:sim" = Nachbildung
am simulierten Bus, in der Simulation Vorgabe) und "can_device" wie bei UDS. CAN-IDs, Byte-Reihenfolge und
Zeitlimit kommen aus der A2L. Nach jedem CONNECT wird der EPK des Steuergeraets mit dem der A2L verglichen;
passt er nicht, lehnt der Dienst alles ab -- die Adressen der A2L gelten dann nicht fuer diese Software.
"""
from __future__ import annotations

import itertools
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


@dataclass
class XcpResult:
    ok: bool
    values: list = field(default_factory=list)
    text: str = ""
    error: str = ""
    code: int | None = None

    def describe(self) -> str:
        if not self.ok:
            return self.error
        return self.text or " ".join(f"{v:g}" for v in self.values) or "ok"


class XcpService(QObject):
    finished = Signal(int, object)          # request_id, XcpResult
    state_changed = Signal(str)

    def __init__(self, worker, parent=None) -> None:
        super().__init__(parent)
        self._worker = worker
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xcp")
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._config: dict = {}
        self._simulation = False
        self._a2l = None
        self._a2l_error = ""
        self._client = None
        self._client_key: tuple | None = None
        self._can_online: set[str] = set()

    # -- Konfiguration --------------------------------------------------------------------

    def configure(self, config: dict, simulation: bool) -> None:
        with self._lock:
            self._config = dict(config)
            self._simulation = simulation
        self._executor.submit(self._drop_client)
        self._load_a2l()

    def _effective(self) -> dict:
        config = dict(self._config)
        if not config.get("a2l") and self._simulation:
            config.update(a2l="builtin:sim", can_device=config.get("can_device") or "can:mock:SIM")
        return config

    def _load_a2l(self) -> None:
        import xcp_a2l
        source = self._effective().get("a2l", "")
        try:
            if source == "builtin:sim":
                from xcp_sim import sim_a2l_text
                a2l = xcp_a2l.parse(sim_a2l_text())
            elif source:
                a2l = xcp_a2l.load(Path(source).expanduser())
            else:
                a2l = None
            error = ""
        except (OSError, ValueError) as exc:
            logger.warning("A2L %s nicht lesbar: %s", source, exc)
            a2l, error = None, str(exc)
        with self._lock:
            self._a2l, self._a2l_error = a2l, error
        self.state_changed.emit(f"A2L: {error}" if error else (a2l.module if a2l else ""))

    def a2l(self):
        with self._lock:
            return self._a2l

    def a2l_error(self) -> str:
        with self._lock:
            return self._a2l_error

    def configured(self) -> bool:
        return self.a2l() is not None

    def can_device(self) -> str:
        return self._effective().get("can_device", "")

    def on_can_connected(self, device_id: str, online: bool) -> None:
        if online:
            self._can_online.add(device_id)
        else:
            self._can_online.discard(device_id)
            self._executor.submit(self._drop_client)

    # -- Befehle --------------------------------------------------------------------------

    def submit(self, command: str, args: dict | None = None) -> int:
        request_id = next(self._ids)
        self._executor.submit(self._run, request_id, command, dict(args or {}))
        return request_id

    def call(self, command: str, args: dict | None = None, timeout: float = 30.0) -> XcpResult:
        return self._executor.submit(self._execute, command, dict(args or {})).result(timeout)

    def shutdown(self) -> None:
        self._executor.submit(self._drop_client)
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run(self, request_id: int, command: str, args: dict) -> None:
        result = self._execute(command, args)
        logger.info("XCP %s %s -> %s", command, args.get("name", ""), result.describe())
        self.finished.emit(request_id, result)

    def _drop_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                logger.exception("XCP-Verbindung schliessen")
        self._client, self._client_key = None, None

    def _ensure_client(self):
        from xcp_client import XcpClient, XcpError
        a2l = self.a2l()
        if a2l is None:
            raise RuntimeError(self.a2l_error() or "Keine A2L beim Prüfling eingestellt (Einstellungen → Prüfling)")
        can_id = self.can_device()
        if not can_id:
            candidates = sorted(d for d in self._can_online if d.startswith("can:"))
            if len(candidates) != 1:
                raise RuntimeError("CAN-Interface des Prüflings nicht eindeutig – in Einstellungen → Prüfling wählen")
            can_id = candidates[0]
        key = (can_id, a2l.master_id, a2l.slave_id, a2l.extended_ids)
        if self._client is None or self._client_key != key or not self._client.connected:
            self._drop_client()
            client = XcpClient(self._worker, can_id, a2l.master_id, a2l.slave_id, a2l.extended_ids,
                               timeout_s=max(0.1, a2l.t1_s))
            try:
                client.connect()
                if a2l.epk and a2l.addr_epk is not None:
                    try:
                        raw = client.read(a2l.addr_epk, len(a2l.epk.encode("latin-1")))
                    except XcpError as exc:
                        if exc.code in (0x22, 0x24):   # Adresse ausserhalb der Objekte dieser Firmware (0.64.1)
                            raise RuntimeError(f"EPK an 0x{a2l.addr_epk:X} nicht lesbar ({exc}) – die A2L passt "
                                               f"nicht zur Firmware auf dem Steuergerät") from None
                        raise
                    found = raw.decode("latin-1", errors="replace")
                    if found != a2l.epk:
                        raise RuntimeError(f"EPK passt nicht zur A2L: Steuergerät „{found}“, "
                                           f"A2L „{a2l.epk}“")
            except (XcpError, RuntimeError):
                client.close()
                raise
            self._client, self._client_key = client, key
        return self._client

    def _execute(self, command: str, args: dict) -> XcpResult:
        from xcp_a2l import A2lError
        from xcp_client import XcpError
        try:
            client = self._ensure_client()
            a2l = self.a2l()
            if command == "connect":
                return XcpResult(True, text=f"verbunden, EPK {a2l.epk or '–'}")
            obj = a2l.get(args["name"])
            if command == "read":
                values = a2l.decode(obj, client.read(obj.address, obj.size))
                index = int(args.get("index", 0) or 0)
                if not 0 <= index < len(values):
                    return XcpResult(False, error=f"{obj.name}: Index {index} außerhalb 0–{len(values) - 1}")
                text = obj.compu.text(obj.compu.to_raw(values[index]))
                return XcpResult(True, values=values, text=(f"{values[index]:g} ({text})" if text else ""))
            if command == "write":
                if not obj.writable:
                    return XcpResult(False, error=f"{obj.name} ist nicht schreibbar")
                data = a2l.encode(obj, [float(v) for v in args["values"]])
                client.write(obj.address, data)
                back = a2l.decode(obj, client.read(obj.address, obj.size))
                return XcpResult(True, values=back)
            return XcpResult(False, error=f"Unbekannter XCP-Befehl {command}")
        except XcpError as exc:
            if exc.code is None:
                self._drop_client()
            return XcpResult(False, error=str(exc), code=exc.code)
        except (A2lError, RuntimeError, KeyError, ValueError) as exc:
            return XcpResult(False, error=str(exc))
