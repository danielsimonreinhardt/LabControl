"""Restbus aus DBC (FT-6, PI-56 c, 0.67.0): Botschaften zyklisch senden, Signale per Name und physikalischem Wert.

Ein ``RestbusNode`` haengt wie der Batterie-Simulator als CanNode am CanScheduler eines Interfaces (siehe node.py)
und sendet die eingeschalteten Botschaften im eigenen Zyklus. Werte kommen aus der DBC (Startwert, sonst 0 bzw.
Minimum), Aenderungen ueber set_signal. Optional E2E je Botschaft (e2e.py).

Konfiguration (settings.json "restbus" je CAN-Interface):
    {"messages": [{"name": "PC_Command", "cycle_ms": 100, "enabled": true,
                   "values": {"PcCmdLed": 1}, "e2e": {"profile": "P01", "data_id": 528, ...}}]}

Qt-frei; Steuer-Methoden aus dem Worker-Thread, due_frames aus dem Scheduler-Thread (Lock wie CanNode).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from can_bus.e2e import E2EConfig, E2EError, E2EProtector
from can_bus.node import CanNode, TxFrame

logger = logging.getLogger(__name__)

MIN_CYCLE_MS = 10
MAX_CYCLE_MS = 60000
DEFAULT_CYCLE_MS = 100


class RestbusError(ValueError):
    pass


def default_cycle_ms(message) -> int:
    """Zykluszeit aus der DBC (GenMsgCycleTime bzw. cantools cycle_time), sonst 100 ms."""
    cycle = getattr(message, "cycle_time", None)
    if not cycle:
        attrs = getattr(getattr(message, "dbc", None), "attributes", {}) or {}
        attr = attrs.get("GenMsgCycleTime")
        cycle = getattr(attr, "value", None)
    try:
        cycle = int(cycle or 0)
    except (TypeError, ValueError):
        cycle = 0
    return min(max(cycle, MIN_CYCLE_MS), MAX_CYCLE_MS) if cycle > 0 else DEFAULT_CYCLE_MS


def initial_value(signal) -> float:
    """Startwert eines Signals: DBC-Startwert (physikalisch), sonst 0 innerhalb der Grenzen."""
    value = getattr(signal, "initial", None)
    if value is None:
        raw = getattr(signal, "raw_initial", None)
        value = (raw * signal.scale + signal.offset) if raw is not None else 0.0
    if not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
    if signal.minimum is not None and value < signal.minimum:
        value = signal.minimum
    if signal.maximum is not None and value > signal.maximum:
        value = signal.maximum
    return float(value)


def check_value(signal, value) -> float | str:
    """Wert pruefen: Zahl in den DBC-Grenzen oder Name aus der Wertetabelle (wird zur Zahl)."""
    choices = getattr(signal, "choices", None) or {}
    if isinstance(value, str):
        text = value.strip()
        for raw, name in choices.items():
            if str(name) == text:
                return float(raw) * signal.scale + signal.offset
        try:
            value = float(text.replace(",", "."))
        except ValueError:
            raise RestbusError(f"{signal.name}: '{value}' ist weder Zahl noch Wert der Wertetabelle") from None
    value = float(value)
    if signal.minimum is not None and value < signal.minimum - 1e-9:
        raise RestbusError(f"{signal.name}: {value:g} unter dem Minimum {signal.minimum:g}")
    if signal.maximum is not None and value > signal.maximum + 1e-9:
        raise RestbusError(f"{signal.name}: {value:g} über dem Maximum {signal.maximum:g}")
    raw_max = (1 << signal.length) - 1
    raw = round((value - signal.offset) / signal.scale) if signal.scale else 0
    if signal.is_signed:
        low, high = -(1 << (signal.length - 1)), (1 << (signal.length - 1)) - 1
    else:
        low, high = 0, raw_max
    if not low <= raw <= high:
        raise RestbusError(f"{signal.name}: {value:g} passt nicht in {signal.length} Bit")
    return value


@dataclass
class _Entry:
    message: object
    cycle_s: float
    enabled: bool
    values: dict
    protector: E2EProtector | None = None
    next_due: float = 0.0
    sent: int = 0
    e2e: dict = field(default_factory=dict)


class RestbusNode(CanNode):
    """Sendet die eingeschalteten Botschaften einer DBC, solange ``running``."""

    def __init__(self, db) -> None:
        super().__init__()
        self._db = db
        self._entries: dict[str, _Entry] = {}
        self._running = False
        self.errors: list[str] = []

    # -- Konfiguration ------------------------------------------------------------------------

    def configure(self, config: dict) -> list[str]:
        """Botschaften aus der Konfiguration uebernehmen; liefert Probleme (betroffene Botschaften fehlen dann).
        Zaehler laufen bei unveraenderter E2E-Angabe weiter."""
        problems: list[str] = []
        entries: dict[str, _Entry] = {}
        for item in (config or {}).get("messages", []) or []:
            name = str((item or {}).get("name", ""))
            try:
                message = self._db.get_message_by_name(name)
            except KeyError:
                problems.append(f"Botschaft {name} nicht in der DBC")
                continue
            cycle = int((item or {}).get("cycle_ms") or default_cycle_ms(message))
            cycle = min(max(cycle, MIN_CYCLE_MS), MAX_CYCLE_MS)
            values = {s.name: initial_value(s) for s in message.signals}
            for sig, value in ((item or {}).get("values") or {}).items():
                signal = next((s for s in message.signals if s.name == sig), None)
                if signal is None:
                    problems.append(f"{name}: Signal {sig} nicht in der DBC")
                    continue
                try:
                    values[sig] = check_value(signal, value)
                except RestbusError as exc:
                    problems.append(f"{name}: {exc}")
            protector = None
            e2e = (item or {}).get("e2e") or {}
            if e2e:
                try:
                    protector = E2EProtector(message, E2EConfig.from_dict(e2e))
                except (E2EError, ValueError) as exc:
                    problems.append(f"{name}: E2E {exc}")
                    continue
            with self._lock:
                old = self._entries.get(name)
            if old is not None and protector is not None and old.protector is not None and old.e2e == e2e:
                protector.counter = old.protector.counter
            entries[name] = _Entry(message, cycle / 1000.0, bool((item or {}).get("enabled", True)), values,
                                   protector, 0.0, old.sent if old else 0, dict(e2e))
        with self._lock:
            self._entries = entries
            self.errors = problems
        return problems

    # -- Bedienung ----------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def set_running(self, on: bool) -> None:
        with self._lock:
            self._running = bool(on)
            for entry in self._entries.values():
                entry.next_due = 0.0

    def set_signal(self, message: str, signal: str, value) -> float:
        """Signalwert setzen (gilt ab dem naechsten Senden). Unbekannt/ausserhalb -> RestbusError."""
        with self._lock:
            entry = self._entries.get(message)
            if entry is None:
                raise RestbusError(f"Botschaft {message} ist nicht im Restbus")
            sig = next((s for s in entry.message.signals if s.name == signal), None)
            if sig is None:
                raise RestbusError(f"Signal {signal} nicht in {message}")
            if entry.protector is not None and signal in (entry.protector.config.counter, entry.protector.config.crc):
                raise RestbusError(f"{signal} setzt der E2E-Schutz selbst")
            checked = check_value(sig, value)
            entry.values[signal] = checked
            return checked

    def set_enabled(self, message: str, on: bool) -> None:
        with self._lock:
            entry = self._entries.get(message)
            if entry is None:
                raise RestbusError(f"Botschaft {message} ist nicht im Restbus")
            entry.enabled = bool(on)
            entry.next_due = 0.0

    def state(self) -> dict:
        with self._lock:
            return {"running": self._running, "errors": list(self.errors), "tx_error": self.last_tx_error,
                    "messages": {name: {"enabled": e.enabled, "cycle_ms": round(e.cycle_s * 1000),
                                        "values": dict(e.values), "sent": e.sent,
                                        "e2e": e.protector.config.profile if e.protector else "",
                                        "counter": e.protector.counter if e.protector else None}
                                 for name, e in self._entries.items()}}

    # -- CanNode ------------------------------------------------------------------------------

    def on_frame(self, frame) -> None:
        return None

    def online(self, now: float | None = None) -> bool:
        return True

    def due_frames(self, now: float) -> list[TxFrame]:
        frames: list[TxFrame] = []
        with self._lock:
            if not self._running:
                return frames
            for name, entry in self._entries.items():
                if not entry.enabled or now < entry.next_due:
                    continue
                # naechster Termin im Raster, nach einer Pause nicht nachholen
                entry.next_due = now + entry.cycle_s if entry.next_due == 0.0 or now - entry.next_due > entry.cycle_s \
                    else entry.next_due + entry.cycle_s
                try:
                    if entry.protector is not None:
                        data = entry.protector.protect(entry.values)
                    else:
                        data = entry.message.encode(entry.values, scaling=True, padding=False, strict=False)
                except Exception as exc:  # noqa: BLE001 -- eine kaputte Botschaft stoppt nicht die anderen
                    message = f"{name}: {exc}"
                    if message not in self.errors:
                        self.errors.append(message)
                        logger.warning("Restbus %s", message)
                    continue
                entry.sent += 1
                frames.append(TxFrame(entry.message.frame_id, bytes(data), bool(entry.message.is_extended_frame)))
        return frames


def messages_overview(db) -> list[dict]:
    """Botschaften der DBC fuer die Auswahl: Name, ID, Laenge, Zyklus, Signale (Name, Einheit, Grenzen, Werte)."""
    result = []
    for message in sorted(db.messages, key=lambda m: m.frame_id):
        result.append({
            "name": message.name, "id": message.frame_id, "extended": bool(message.is_extended_frame),
            "length": message.length, "cycle_ms": default_cycle_ms(message),
            "senders": list(getattr(message, "senders", []) or []),
            "signals": [{"name": s.name, "unit": s.unit or "", "minimum": s.minimum, "maximum": s.maximum,
                         "initial": initial_value(s), "length": s.length,
                         "choices": {int(k): str(v) for k, v in (getattr(s, "choices", None) or {}).items()}}
                        for s in message.signals],
        })
    return result


def import_e2e_from_rte(data: dict) -> dict[str, dict]:
    """E2E-Angaben aus einer neXtBSW rte.yaml (schon geparst; can.qualifiers.<Botschaft>.e2e)
    -> {Botschaft: e2e-dict}."""
    qualifiers = ((data.get("can") or {}).get("qualifiers") or {}) if isinstance(data, dict) else {}
    result = {}
    for name, entry in qualifiers.items():
        e2e = (entry or {}).get("e2e") if isinstance(entry, dict) else None
        if isinstance(e2e, dict) and e2e.get("profile"):
            result[str(name)] = {k: e2e[k] for k in ("profile", "data_id", "data_id_mode", "crc", "counter",
                                                     "data_id_nibble", "data_id_list") if k in e2e}
    return result
