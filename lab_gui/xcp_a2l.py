"""A2L-Leser fuer XCP (0.64.0, FT-10): eigener Leser fuer das ASAP2-Subset, das neXtBSW tools/a2lgen schreibt.

Gelesen werden MOD_PAR (EPK, ADDR_EPK), MOD_COMMON (BYTE_ORDER), IF_DATA XCP (PROTOCOL_LAYER: Zeiten, MAX_CTO;
XCP_ON_CAN: CAN_ID_MASTER/SLAVE, Bit 31 = 29-Bit-ID, BAUDRATE), COMPU_METHOD (IDENTICAL, LINEAR, RAT_FUNC mit
a = d = 0, TAB_VERB), COMPU_VTAB, RECORD_LAYOUT (FNC_VALUES), MEASUREMENT (Datentyp, Umrechnung, Grenzen,
ECU_ADDRESS, ARRAY_SIZE/MATRIX_DIM, PHYS_UNIT, READ_WRITE) und CHARACTERISTIC VALUE/VAL_BLK (Adresse, Ablage,
Umrechnung, Grenzen, NUMBER/MATRIX_DIM, READ_ONLY). Kennlinien/Kennfelder (CURVE/MAP/AXIS_PTS) noch nicht.
Qt-frei.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

DATATYPES = {   # A2L-Datentyp -> (struct-Code, Bytes)
    "UBYTE": ("B", 1), "SBYTE": ("b", 1), "UWORD": ("H", 2), "SWORD": ("h", 2), "ULONG": ("I", 4),
    "SLONG": ("i", 4), "A_UINT64": ("Q", 8), "A_INT64": ("q", 8), "FLOAT32_IEEE": ("f", 4),
    "FLOAT64_IEEE": ("d", 8),
}
_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|/\*.*?\*/|//[^\n]*|\S+', re.S)


class A2lError(ValueError):
    pass


@dataclass
class _Block:
    name: str
    args: list = field(default_factory=list)
    children: list = field(default_factory=list)

    def child(self, name: str) -> "_Block | None":
        return next((c for c in self.children if c.name == name), None)

    def all(self, name: str) -> list:
        found = []
        for c in self.children:
            if c.name == name:
                found.append(c)
            found.extend(c.all(name))
        return found


def _parse(text: str) -> _Block:
    root = _Block("ROOT")
    stack = [root]
    tokens = [t for t in _TOKEN.findall(text) if not t.startswith("/*") and not t.startswith("//")]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "/begin":
            block = _Block(tokens[i + 1])
            stack[-1].children.append(block)
            stack.append(block)
            i += 2
            continue
        if token == "/end":
            if len(stack) == 1:
                raise A2lError(f"/end {tokens[i + 1]} ohne /begin")
            stack.pop()
            i += 2
            continue
        stack[-1].args.append(token[1:-1] if token.startswith('"') else token)
        i += 1
    return root


def _num(token) -> float:
    return float(int(token, 0)) if re.fullmatch(r"[+-]?0[xX][0-9a-fA-F]+", str(token)) else float(token)


def _int(token) -> int:
    return int(str(token), 0)


def _keyword(args: list, key: str, count: int = 1):
    """Wert(e) nach einem Schluesselwort in einer flachen Argumentliste, sonst None."""
    for i, token in enumerate(args):
        if token == key and i + count < len(args) + 1:
            values = args[i + 1:i + 1 + count]
            return values[0] if count == 1 else values
    return None


@dataclass
class Compu:
    name: str = "IDENTITY"
    kind: str = "IDENTICAL"          # IDENTICAL | LINEAR | RAT_FUNC | TAB_VERB
    coeffs: tuple = (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    unit: str = ""
    table: dict = field(default_factory=dict)

    def to_phys(self, raw: float) -> float:
        if self.kind == "LINEAR":
            a, b = self.coeffs[:2]
            return a * raw + b
        if self.kind == "RAT_FUNC":
            _a, b, c, _d, e, f = self.coeffs
            return (c - f * raw) / (e * raw - b)
        return raw

    def to_raw(self, phys: float) -> float:
        if self.kind == "LINEAR":
            a, b = self.coeffs[:2]
            return (phys - b) / a
        if self.kind == "RAT_FUNC":
            _a, b, c, _d, e, f = self.coeffs
            return (b * phys + c) / (e * phys + f)
        return phys

    def text(self, raw: float) -> str:
        return self.table.get(int(raw), "") if self.kind == "TAB_VERB" else ""


@dataclass
class A2lObject:
    name: str
    kind: str                         # "measurement" | "characteristic"
    address: int
    datatype: str
    count: int = 1
    compu: Compu = field(default_factory=Compu)
    lower: float = float("-inf")
    upper: float = float("inf")
    unit: str = ""
    writable: bool = False
    description: str = ""

    @property
    def size(self) -> int:
        return DATATYPES[self.datatype][1] * self.count


@dataclass
class A2l:
    objects: dict = field(default_factory=dict)
    epk: str = ""
    addr_epk: int | None = None
    little_endian: bool = True
    master_id: int = 0
    slave_id: int = 0
    extended_ids: bool = False
    baudrate: int = 0
    max_cto: int = 8
    t1_s: float = 0.2
    module: str = ""

    def get(self, name: str) -> A2lObject:
        try:
            return self.objects[name]
        except KeyError:
            raise A2lError(f"Unbekanntes A2L-Objekt {name!r}") from None

    def names(self, kind: str | None = None) -> list[str]:
        return sorted(n for n, o in self.objects.items() if kind is None or o.kind == kind)

    def decode(self, obj: A2lObject, data: bytes) -> list[float]:
        code, width = DATATYPES[obj.datatype]
        fmt = ("<" if self.little_endian else ">") + code * obj.count
        raw = struct.unpack(fmt, bytes(data[:width * obj.count]))
        return [obj.compu.to_phys(float(v)) for v in raw]

    def encode(self, obj: A2lObject, values: list[float]) -> bytes:
        """Physikalische Werte -> Bytes; ausserhalb LOWER/UPPER_LIMIT oder des Datentyps: A2lError."""
        if len(values) != obj.count:
            raise A2lError(f"{obj.name}: {obj.count} Wert(e) erwartet, {len(values)} bekommen")
        code, _width = DATATYPES[obj.datatype]
        raws = []
        for value in values:
            if not obj.lower <= value <= obj.upper:
                raise A2lError(f"{obj.name}: {value:g} außerhalb {obj.lower:g}–{obj.upper:g}")
            raw = obj.compu.to_raw(value)
            raws.append(raw if code in "fd" else int(round(raw)))
        fmt = ("<" if self.little_endian else ">") + code * obj.count
        try:
            return struct.pack(fmt, *raws)
        except struct.error as exc:
            raise A2lError(f"{obj.name}: passt nicht in {obj.datatype} ({exc})") from None


def _count(args: list) -> int:
    for key in ("ARRAY_SIZE", "NUMBER"):
        value = _keyword(args, key)
        if value is not None:
            return max(1, _int(value))
    dims = []
    for i, token in enumerate(args):
        if token == "MATRIX_DIM":
            for t in args[i + 1:i + 4]:
                if re.fullmatch(r"\d+", t):
                    dims.append(int(t))
                else:
                    break
            break
    count = 1
    for d in dims:
        count *= max(1, d)
    return count


def parse(text: str) -> A2l:
    root = _parse(text)
    module = next(iter(root.all("MODULE")), None)
    if module is None:
        raise A2lError("Keine A2L-Datei (MODULE fehlt)")
    a2l = A2l(module=module.args[0] if module.args else "")
    mod_par = module.child("MOD_PAR")
    if mod_par is not None:
        a2l.epk = _keyword(mod_par.args, "EPK") or ""
        addr = _keyword(mod_par.args, "ADDR_EPK")
        a2l.addr_epk = _int(addr) if addr is not None else None
    mod_common = module.child("MOD_COMMON")
    if mod_common is not None:
        a2l.little_endian = (_keyword(mod_common.args, "BYTE_ORDER") or "MSB_LAST") == "MSB_LAST"
    for if_data in module.all("IF_DATA"):
        if not if_data.args or if_data.args[0] != "XCP":
            continue
        layer = if_data.child("PROTOCOL_LAYER")
        if layer is not None and len(layer.args) >= 10:
            a2l.t1_s = _num(layer.args[1]) / 1000.0
            a2l.max_cto = _int(layer.args[8])
        can = if_data.child("XCP_ON_CAN")
        if can is not None:
            master = _int(_keyword(can.args, "CAN_ID_MASTER") or 0)
            slave = _int(_keyword(can.args, "CAN_ID_SLAVE") or 0)
            a2l.extended_ids = bool(master & 0x80000000)
            a2l.master_id, a2l.slave_id = master & 0x1FFFFFFF, slave & 0x1FFFFFFF
            a2l.baudrate = _int(_keyword(can.args, "BAUDRATE") or 0)
    vtabs = {}
    for block in module.all("COMPU_VTAB"):
        args = block.args
        pairs = args[4:]
        vtabs[args[0]] = {int(_num(pairs[i])): pairs[i + 1] for i in range(0, len(pairs) - 1, 2)}
    compus = {}
    for block in module.all("COMPU_METHOD"):
        args = block.args
        name, kind = args[0], args[2] if len(args) > 2 else "IDENTICAL"
        compu = Compu(name=name, kind=kind, unit=args[4] if len(args) > 4 else "")
        coeffs = _keyword(args, "COEFFS", 6)
        if kind == "RAT_FUNC" and coeffs:
            compu.coeffs = tuple(_num(c) for c in coeffs)
            if compu.coeffs[0] or compu.coeffs[3]:
                raise A2lError(f"COMPU_METHOD {name}: quadratische RAT_FUNC wird nicht unterstuetzt")
        linear = _keyword(args, "COEFFS_LINEAR", 2)
        if kind == "LINEAR" and linear:
            compu.coeffs = tuple(_num(c) for c in linear)
        if kind == "TAB_VERB":
            compu.table = vtabs.get(_keyword(args, "COMPU_TAB_REF") or "", {})
        compus[name] = compu
    layouts = {}
    for block in module.all("RECORD_LAYOUT"):
        datatype = _keyword(block.args, "FNC_VALUES", 2)
        if datatype:
            layouts[block.args[0]] = datatype[1]

    def compu_of(name: str) -> Compu:
        return compus.get(name, Compu(name=name))

    for block in module.all("MEASUREMENT"):
        args = block.args
        name, datatype, conversion = args[0], args[2], args[3]
        address = _keyword(args, "ECU_ADDRESS")
        if address is None or datatype not in DATATYPES:
            continue
        compu = compu_of(conversion)
        a2l.objects[name] = A2lObject(
            name=name, kind="measurement", address=_int(address), datatype=datatype, count=_count(args),
            compu=compu, lower=_num(args[6]), upper=_num(args[7]),
            unit=_keyword(args, "PHYS_UNIT") or compu.unit, writable="READ_WRITE" in args, description=args[1])
    for block in module.all("CHARACTERISTIC"):
        args = block.args
        name, ctype = args[0], args[2]
        if ctype not in ("VALUE", "VAL_BLK"):
            continue
        datatype = layouts.get(args[4], "")
        if datatype not in DATATYPES:
            continue
        compu = compu_of(args[6])
        a2l.objects[name] = A2lObject(
            name=name, kind="characteristic", address=_int(args[3]), datatype=datatype,
            count=_count(args) if ctype == "VAL_BLK" else 1, compu=compu, lower=_num(args[7]), upper=_num(args[8]),
            unit=_keyword(args, "PHYS_UNIT") or compu.unit, writable="READ_ONLY" not in args, description=args[1])
    return a2l


def load(path) -> A2l:
    path = Path(path)
    try:
        return parse(path.read_text(encoding="utf-8", errors="replace"))
    except (IndexError, ValueError) as exc:
        if isinstance(exc, A2lError):
            raise
        raise A2lError(f"{path.name}: nicht lesbar ({exc})") from None
