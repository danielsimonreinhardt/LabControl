"""E2E-Schutz fuer gesendete Restbus-Botschaften (FT-6, PI-56 c, 0.67.0). Qt-frei, nur Standardbibliothek.

Eigene Umsetzung nach AUTOSAR PRS E2E Protocol (Profile 1, 2, 11) und SWS CRC Library, passend zur Konfiguration
der neXtBSW-RTE (rte.yaml, can.qualifiers.<Botschaft>.e2e): profile P01/P02/P11, data_id, data_id_mode
(BOTH/ALT/LOW/NIBBLE), crc/counter/data_id_nibble als DBC-Signalnamen, bei P02 data_id_list (16 Werte).
Zusaetzlich "COUNTER": nur ein Zaehler ohne CRC (Alive-Counter).

- P01: CRC-8 SAE J1850 (Polynom 0x1D) mit Start- und XOR-Wert 0x00, zuerst ueber die Data ID (BOTH: Low- dann
  High-Byte; LOW: nur Low; ALT: Low bei geradem, High bei ungeradem Zaehler; NIBBLE: Low und 0x00, das High-Nibble
  steht im Signal data_id_nibble), dann ueber alle Datenbytes ausser dem CRC-Byte. Zaehler 0..14, danach 0.
- P11: wie P01 (nur BOTH/NIBBLE).
- P02: CRC-8H2F (Polynom 0x2F, Start 0xFF, XOR 0xFF) ueber alle Datenbytes ausser dem CRC-Byte, danach die Data ID
  aus data_id_list[Zaehler]. Zaehler wird vor dem Senden erhoeht, 0..15.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PROFILES = ("P01", "P02", "P11", "COUNTER")
DATA_ID_MODES = ("BOTH", "ALT", "LOW", "NIBBLE")


class E2EError(ValueError):
    pass


def _make_table(poly: int) -> list[int]:
    table = []
    for value in range(256):
        reg = value
        for _ in range(8):
            reg = ((reg << 1) ^ poly) & 0xFF if reg & 0x80 else (reg << 1) & 0xFF
        table.append(reg)
    return table


_TABLE_1D = _make_table(0x1D)
_TABLE_2F = _make_table(0x2F)


def crc8(data, table: list[int], start: int, xor_out: int) -> int:
    reg = start
    for byte in data:
        reg = table[reg ^ (int(byte) & 0xFF)]
    return reg ^ xor_out


def crc8_sae_j1850(data) -> int:
    """Crc_CalculateCRC8 als Einzelaufruf (Start 0xFF, XOR 0xFF) -- Pruefwert 0x4B fuer '123456789'."""
    return crc8(data, _TABLE_1D, 0xFF, 0xFF)


def crc8_h2f(data) -> int:
    """Crc_CalculateCRC8H2F als Einzelaufruf (Start 0xFF, XOR 0xFF) -- Pruefwert 0xDF fuer '123456789'."""
    return crc8(data, _TABLE_2F, 0xFF, 0xFF)


@dataclass
class E2EConfig:
    profile: str
    data_id: int = 0
    data_id_mode: str = "BOTH"
    crc: str = ""                   # Signalname des CRC-Bytes
    counter: str = ""               # Signalname des Zaehlers
    data_id_nibble: str = ""        # Signalname des Data-ID-Nibbles (NIBBLE)
    data_id_list: list[int] = field(default_factory=list)   # P02

    @classmethod
    def from_dict(cls, data: dict) -> "E2EConfig":
        if not isinstance(data, dict):
            raise E2EError("E2E-Angabe ist kein Objekt")
        profile = str(data.get("profile", "")).upper()
        if profile not in PROFILES:
            raise E2EError(f"E2E-Profil {profile or '?'} unbekannt ({', '.join(PROFILES)})")
        mode = str(data.get("data_id_mode", "BOTH")).upper()
        if mode not in DATA_ID_MODES:
            raise E2EError(f"data_id_mode {mode} unbekannt")
        if profile == "P11" and mode not in ("BOTH", "NIBBLE"):
            raise E2EError("P11 kennt nur data_id_mode BOTH oder NIBBLE")
        config = cls(profile=profile, data_id=_int(data.get("data_id", 0)), data_id_mode=mode,
                     crc=str(data.get("crc", "") or ""), counter=str(data.get("counter", "") or ""),
                     data_id_nibble=str(data.get("data_id_nibble", "") or ""),
                     data_id_list=[_int(v) for v in data.get("data_id_list", []) or []])
        if not config.counter:
            raise E2EError("E2E ohne Zaehler-Signal (counter)")
        if profile != "COUNTER" and not config.crc:
            raise E2EError("E2E ohne CRC-Signal (crc)")
        if profile == "P02" and len(config.data_id_list) != 16:
            raise E2EError("P02 braucht data_id_list mit 16 Werten")
        if mode == "NIBBLE" and profile in ("P01", "P11") and not config.data_id_nibble:
            raise E2EError("data_id_mode NIBBLE braucht das Signal data_id_nibble")
        if not 0 <= config.data_id <= 0xFFFF:
            raise E2EError("data_id ausserhalb 0..0xFFFF")
        return config

    def to_dict(self) -> dict:
        data = {"profile": self.profile, "counter": self.counter}
        if self.profile != "COUNTER":
            data.update(crc=self.crc, data_id=self.data_id)
        if self.profile in ("P01", "P11"):
            data["data_id_mode"] = self.data_id_mode
            if self.data_id_mode == "NIBBLE":
                data["data_id_nibble"] = self.data_id_nibble
        if self.profile == "P02":
            data["data_id_list"] = list(self.data_id_list)
        return data


def _int(value) -> int:
    if isinstance(value, bool):
        raise E2EError(str(value))
    if isinstance(value, int):
        return value
    return int(str(value).strip(), 0)


def crc_byte_index(signal) -> int:
    """Byte des CRC-Signals (cantools Signal): 8 Bit, an einer Bytegrenze."""
    if signal.length != 8:
        raise E2EError(f"CRC-Signal {signal.name} hat {signal.length} Bit statt 8")
    if signal.byte_order == "little_endian" and signal.start % 8 == 0:
        return signal.start // 8
    if signal.byte_order == "big_endian" and signal.start % 8 == 7:
        return signal.start // 8
    raise E2EError(f"CRC-Signal {signal.name} liegt nicht auf einer Bytegrenze")


class E2EProtector:
    """Zaehlerstand und Schutz einer Botschaft. protect() bekommt die physikalischen Signalwerte, setzt Zaehler und
    Data-ID-Nibble, kodiert mit cantools und traegt die CRC ein."""

    def __init__(self, message, config: E2EConfig) -> None:
        self.message = message
        self.config = config
        names = {s.name: s for s in message.signals}
        for role, name in (("counter", config.counter), ("crc", config.crc), ("data_id_nibble", config.data_id_nibble)):
            if name and name not in names:
                raise E2EError(f"Signal {name} ({role}) fehlt in {message.name}")
        counter_signal = names[config.counter]
        self._counter_modulo = {"P01": 15, "P11": 15, "P02": 16}.get(config.profile, 1 << counter_signal.length)
        if config.profile in ("P01", "P02", "P11") and counter_signal.length != 4:
            raise E2EError(f"Zaehler {config.counter} hat {counter_signal.length} Bit statt 4")
        self._crc_index = crc_byte_index(names[config.crc]) if config.profile != "COUNTER" else None
        self.counter = 0

    def protect(self, values: dict) -> bytes:
        cfg = self.config
        if cfg.profile == "P02":
            self.counter = (self.counter + 1) % 16
        counter = self.counter
        values = dict(values)
        values[cfg.counter] = counter
        if cfg.crc:
            values[cfg.crc] = 0
        if cfg.data_id_mode == "NIBBLE" and cfg.data_id_nibble and cfg.profile in ("P01", "P11"):
            values[cfg.data_id_nibble] = (cfg.data_id >> 8) & 0x0F
        data = bytearray(self.message.encode(values, scaling=True, padding=False, strict=False))
        if self._crc_index is not None:
            payload = [b for i, b in enumerate(data) if i != self._crc_index]
            data[self._crc_index] = self._crc(payload, counter)
        if cfg.profile != "P02":
            self.counter = (counter + 1) % self._counter_modulo
        return bytes(data)

    def _crc(self, payload: list[int], counter: int) -> int:
        cfg = self.config
        low, high = cfg.data_id & 0xFF, (cfg.data_id >> 8) & 0xFF
        if cfg.profile == "P02":
            return crc8_h2f(payload + [cfg.data_id_list[counter] & 0xFF])
        prefix = {"BOTH": [low, high], "LOW": [low], "NIBBLE": [low, 0x00],
                  "ALT": [low] if counter % 2 == 0 else [high]}[cfg.data_id_mode]
        return crc8(prefix + payload, _TABLE_1D, 0x00, 0x00)
