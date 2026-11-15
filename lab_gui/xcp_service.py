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
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

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


DEFAULT_RATE_HZ = 10.0
MAX_RATE_HZ = 50.0


def measure_list(text) -> list[str]:
    """Messliste aus den Einstellungen ("a, b c" oder Liste) -> Namen in Reihenfolge, ohne Doppelte."""
    items = text if isinstance(text, (list, tuple)) else re.split(r"[\s,;]+", str(text or ""))
    seen, result = set(), []
    for item in items:
        name = str(item).strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


class XcpService(QObject):
    finished = Signal(int, object)          # request_id, XcpResult
    state_changed = Signal(str)
    # Messliste (0.64.3, FT-10): {"t": Wanduhr, "values": {Name: Wert}, "units": {Name: Einheit}}
    values_polled = Signal(object)
    # {"active": bool, "names": [...], "rate_hz": float, "error": str}
    poll_state = Signal(dict)

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
        self._poll_owners: dict[str, set] = {}   # Wer die Messliste braucht (Kachel, Lauf) -> zusaetzliche Namen
        self._poll_busy = False
        self._poll_error = ""
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_tick)

    # -- Konfiguration --------------------------------------------------------------------

    def configure(self, config: dict, simulation: bool) -> None:
        with self._lock:
            self._config = dict(config)
            self._simulation = simulation
        self._executor.submit(self._drop_client)
        self._load_a2l()
        if self._poll_timer.isActive():
            self._poll_timer.start(round(1000.0 / self.rate_hz()))
        self._emit_poll_state()

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

    # -- Messliste (0.64.3) -----------------------------------------------------------------

    def rate_hz(self) -> float:
        try:
            rate = float(self._effective().get("xcp_rate_hz") or DEFAULT_RATE_HZ)
        except (TypeError, ValueError):
            rate = DEFAULT_RATE_HZ
        return min(max(rate, 0.5), MAX_RATE_HZ)

    def measure_names(self) -> list[str]:
        """Eingestellte Messliste plus die Namen, die ein Lauf gerade braucht; nur Objekte der A2L."""
        a2l = self.a2l()
        names = measure_list(self._effective().get("xcp_measure", ""))
        if not names and a2l is not None and a2l.module == "labcontrol_sim":
            names = ["Sim.Counter", "Sim.Supply_mV", "Sim.Temperature"]   # Vorgabe der Nachbildung
        for extra in self._poll_owners.values():
            names += [n for n in sorted(extra) if n not in names]
        return [n for n in names if a2l is not None and n in a2l.objects]

    def polling(self) -> bool:
        return bool(self._poll_owners)

    def set_polling(self, owner: str, on: bool, names=None) -> None:
        """Messliste abfragen, solange mindestens ein Besitzer sie braucht (GUI-Thread)."""
        if on:
            self._poll_owners[owner] = set(names or ())
            self._poll_error = ""
        else:
            self._poll_owners.pop(owner, None)
        if self._poll_owners and not self._poll_timer.isActive():
            self._poll_timer.start(round(1000.0 / self.rate_hz()))
        elif not self._poll_owners:
            self._poll_timer.stop()
        self._emit_poll_state()

    def _emit_poll_state(self) -> None:
        self.poll_state.emit({"active": self.polling(), "names": self.measure_names(), "rate_hz": self.rate_hz(),
                              "error": self._poll_error})

    def _poll_tick(self) -> None:
        if self._poll_busy or not self._poll_owners:
            return
        names = self.measure_names()
        if not names:
            return
        self._poll_busy = True
        self._executor.submit(self._poll_job, names)

    def _poll_job(self, names: list[str]) -> None:
        try:
            values, units = {}, {}
            a2l = self.a2l()
            for name in names:
                result = self._execute("read", {"name": name})
                if not result.ok:
                    raise RuntimeError(f"{name}: {result.error}")
                obj = a2l.get(name)
                if obj.count == 1:
                    values[name] = result.values[0]
                    units[name] = obj.unit
                else:
                    for i, value in enumerate(result.values):
                        values[f"{name}[{i}]"] = value
                        units[f"{name}[{i}]"] = obj.unit
            self.values_polled.emit({"t": time.time(), "values": values, "units": units})
        except Exception as exc:  # noqa: BLE001 -- jede Stoerung beendet die Abfrage mit Meldung
            logger.warning("XCP-Messliste: %s", exc)
            self._poll_error = str(exc)
            QTimer.singleShot(0, self, self._stop_after_error)
        finally:
            self._poll_busy = False

    def _stop_after_error(self) -> None:
        self._poll_owners.clear()
        self._poll_timer.stop()
        self._emit_poll_state()

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
        self._poll_owners.clear()
        self._poll_timer.stop()
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
