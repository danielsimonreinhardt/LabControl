"""UDS-Dienst der App (FT-1, 0.60.0): fuehrt UDS-Befehle am eingestellten Pruefling aus.

Ein eigener Thread arbeitet die Befehle nacheinander ab (udsoncan ist nicht thread-sicher), das Ergebnis kommt
als Qt-Signal `finished(request_id, UdsResult)` im GUI-Thread an. Genutzt von Testablauf-Schritten (UDS_*),
Kachel und spaeter Profilen (Reset-/DTC-Pruefung, FT-2).

Pruefling (Settings.dut_config, FP-12 a): CAN-Interface, ODX-Datei (FP-10 b, "builtin:sim" = nachgebildetes
Steuergeraet am simulierten Bus), Schluesselname. Der Master-Schluessel kommt aus <gemeinsam>/keys/<name>.key
(FP-11 b) und wird nie geloggt; fuer die Simulation gilt ihr fester Testschluessel.
"""
from __future__ import annotations

import itertools
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal

import uds_keys
from uds_client import UdsClient, UdsResult

logger = logging.getLogger(__name__)

COMMANDS = ("session", "unlock", "read_did", "write_did", "read_dtcs", "clear_dtcs", "routine", "reset", "raw")
ROUTINE_STEPS = {"start": 0x01, "stop": 0x02, "results": 0x03}


class UdsService(QObject):
    finished = Signal(int, object)          # request_id, UdsResult
    state_changed = Signal(str)              # Kurztext fuer Kachel/Status ("" = nicht eingerichtet)

    def __init__(self, worker, shared_root, parent=None) -> None:
        super().__init__(parent)
        self._worker = worker
        self._shared_root = shared_root      # () -> Path des gemeinsamen Ordners
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="uds")
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._config: dict = {}
        self._odx = None
        self._odx_error = ""
        self._client: UdsClient | None = None
        self._client_key: tuple | None = None
        self._can_online: set[str] = set()
        self._simulation = False

    # -- Konfiguration (GUI-Thread) -------------------------------------------------------

    def configure(self, config: dict, simulation: bool) -> None:
        with self._lock:
            self._config = dict(config)
            self._simulation = simulation
            self._odx, self._odx_error = None, ""
        self._executor.submit(self._drop_client)
        self._load_odx()

    def _effective(self) -> dict:
        config = dict(self._config)
        if not config.get("odx") and self._simulation:
            config.update(odx="builtin:sim", can_device=config.get("can_device") or "can:mock:SIM",
                          name=config.get("name") or "Simulation")
        return config

    def _load_odx(self) -> None:
        config = self._effective()
        source = config.get("odx", "")
        try:
            if source == "builtin:sim":
                from uds_sim import sim_odx
                odx = sim_odx()
            elif source:
                import uds_odx
                odx = uds_odx.load(Path(source).expanduser())
            else:
                odx = None
        except Exception as exc:  # noqa: BLE001 -- jede kaputte Datei ist ein Konfigurationsfehler
            logger.warning("ODX %s nicht lesbar: %s", source, exc)
            with self._lock:
                self._odx, self._odx_error = None, str(exc)
            self.state_changed.emit(f"ODX: {exc}")
            return
        with self._lock:
            self._odx = odx
        self.state_changed.emit(config.get("name") or (odx.name if odx else ""))

    def odx(self):
        with self._lock:
            return self._odx

    def odx_error(self) -> str:
        with self._lock:
            return self._odx_error

    def configured(self) -> bool:
        return self.odx() is not None

    def can_device(self) -> str:
        return self._effective().get("can_device", "")

    def on_can_connected(self, device_id: str, online: bool) -> None:
        if online:
            self._can_online.add(device_id)
        else:
            self._can_online.discard(device_id)
            if device_id == self.can_device() or not self.can_device():
                self._executor.submit(self._drop_client)

    # -- Befehle ---------------------------------------------------------------------------

    def submit(self, command: str, args: dict | None = None) -> int:
        """Befehl einreihen; das Ergebnis kommt ueber `finished` mit der zurueckgegebenen ID."""
        request_id = next(self._ids)
        self._executor.submit(self._run, request_id, command, dict(args or {}))
        return request_id

    def call(self, command: str, args: dict | None = None, timeout: float = 30.0) -> UdsResult:
        """Blockierend (Pruefungen, Kommandozeile) -- nie aus dem GUI-Thread waehrend eines Laufs."""
        return self._executor.submit(self._execute, command, dict(args or {})).result(timeout)

    def shutdown(self) -> None:
        self._executor.submit(self._drop_client)
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run(self, request_id: int, command: str, args: dict) -> None:
        result = self._execute(command, args)
        logger.info("UDS %s %s -> %s", command, _args_text(args), result.describe())
        self.finished.emit(request_id, result)

    def _drop_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                logger.exception("UDS-Verbindung schliessen")
        self._client, self._client_key = None, None

    def _ensure_client(self) -> UdsClient:
        config = self._effective()
        odx = self.odx()
        if odx is None:
            raise RuntimeError(self._odx_error or "Kein Prüfling eingestellt (Einstellungen → Prüfling)")
        can_id = config.get("can_device") or ""
        if not can_id:
            candidates = sorted(d for d in self._can_online if d.startswith("can:"))
            if len(candidates) != 1:
                raise RuntimeError("CAN-Interface des Prüflings nicht eindeutig – in Einstellungen → Prüfling wählen")
            can_id = candidates[0]
        request_id = config.get("request_id") or odx.request_id
        response_id = config.get("response_id") or odx.response_id
        key = (can_id, request_id, response_id, odx.filler)
        if self._client is None or self._client_key != key:
            self._drop_client()
            self._client = UdsClient(self._worker, can_id, request_id, response_id, filler=odx.filler,
                                     p2_s=max(0.2, odx.p2_ms / 1000.0 * 4), p2_star_s=odx.p2_star_ms / 1000.0 + 1.0)
            self._client_key = key
        return self._client

    def _master_key(self) -> bytes:
        config = self._effective()
        if config.get("odx") == "builtin:sim":
            from uds_sim import SIM_MASTER_KEY
            return SIM_MASTER_KEY
        name = config.get("key") or ""
        if not name:
            raise uds_keys.KeyError_("Kein Schlüsselname beim Prüfling eingetragen")
        return uds_keys.load_master_key(Path(self._shared_root()), name)

    def _execute(self, command: str, args: dict) -> UdsResult:
        if command not in COMMANDS:
            return UdsResult(False, message=f"Unbekannter UDS-Befehl {command}")
        try:
            client = self._ensure_client()
            odx = self.odx()
            if command == "session":
                return client.session(odx.session_id(args.get("session", "Default")))
            if command == "unlock":
                level = _security_level(odx, args.get("level", ""))
                return client.unlock(level.level, self._master_key(), level.key_bytes)
            if command == "read_did":
                did = _did_id(odx, args.get("did"))
                result = client.read_did(did)
                if result.ok and did in odx.dids:
                    definition = odx.dids[did]
                    try:
                        result.values = {"did": did, "decoded": definition.decode(result.data),
                                         "numeric": {e.name: e.numeric(result.data) for e in definition.elements}}
                    except Exception as exc:  # noqa: BLE001
                        result.values = {"did": did, "decode_error": str(exc)}
                return result
            if command == "write_did":
                did = _did_id(odx, args.get("did"))
                data = _did_payload(odx, did, args)
                return client.write_did(did, data)
            if command == "read_dtcs":
                result = client.read_dtcs(int(args.get("mask", 0xFF)))
                if result.ok:
                    result.values["texts"] = {code: odx.dtcs[code].text for code, _s in result.values.get("dtcs", [])
                                              if code in odx.dtcs}
                return result
            if command == "clear_dtcs":
                return client.clear_dtcs(int(args.get("group", 0xFFFFFF)))
            if command == "routine":
                rid = _routine_id(odx, args.get("routine"))
                control = ROUTINE_STEPS.get(str(args.get("step", "start")).lower(), 0x01)
                return client.routine(rid, control, bytes.fromhex(str(args.get("data", "")).replace(" ", "")))
            if command == "reset":
                return client.reset(int(args.get("type", 0x01)))
            if command == "raw":
                return client.raw(bytes.fromhex(str(args.get("request", "")).replace(" ", "")))
        except uds_keys.KeyError_ as exc:
            return UdsResult(False, message=str(exc))
        except (RuntimeError, ValueError, KeyError) as exc:
            return UdsResult(False, message=str(exc))
        except Exception as exc:  # noqa: BLE001 -- ein Dienstfehler darf den UDS-Thread nicht beenden
            logger.exception("UDS %s", command)
            return UdsResult(False, message=str(exc) or exc.__class__.__name__)
        return UdsResult(False, message="nicht ausgeführt")


def _args_text(args: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in args.items() if k != "key")


def _parse_int(value) -> int:
    return value if isinstance(value, int) else int(str(value).strip(), 0)


def _did_id(odx, value) -> int:
    if value in (None, ""):
        raise ValueError("Keine DID angegeben")
    if isinstance(value, str) and odx.did_by_name(value) is not None:
        return odx.did_by_name(value).did
    return _parse_int(value)


def _routine_id(odx, value) -> int:
    if isinstance(value, str) and odx.routine_by_name(value) is not None:
        return odx.routine_by_name(value).rid
    return _parse_int(value)


def _security_level(odx, value):
    if not odx.security:
        raise ValueError("Der Prüfling hat keine Security-Stufe im ODX")
    if value in (None, "") and len(odx.security) == 1:
        return next(iter(odx.security.values()))
    if str(value) in odx.security:
        return odx.security[str(value)]
    level = _parse_int(value)
    match = next((s for s in odx.security.values() if s.level == level), None)
    if match is None:
        raise ValueError(f"Unbekannte Security-Stufe {value}")
    return match


def _did_payload(odx, did: int, args: dict) -> bytes:
    """Daten zum Schreiben: "hex" roh, oder "values" {Element: Wert} ueber das ODX (fehlende Elemente aus
    dem aktuellen Wert nicht bekannt -> 0)."""
    if args.get("hex"):
        return bytes.fromhex(str(args["hex"]).replace(" ", ""))
    values = args.get("values") or {}
    definition = odx.dids.get(did)
    if definition is None:
        raise ValueError(f"DID 0x{did:04X} nicht im ODX -- Daten als hex angeben")
    data = bytearray(definition.size)
    for element in definition.elements:
        if element.name in values:
            encoded = element.encode(values[element.name])
            data[element.byte_pos:element.byte_pos + len(encoded)] = encoded
    return bytes(data)
