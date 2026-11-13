"""ODX-Beschreibung eines Pruefling-Steuergeraets lesen (FT-1, 0.60.0, FP-10 b).

Quelle ist der ODX-Export von neXtBSW tools/diaggen (`python -m tools.diaggen odx|pdx <projekt> -o …`):
eine .odx-d-Datei oder ein .pdx-Archiv (Zip mit index.xml und .odx-d). Gelesen wird, was der Export schreibt
(ODX 2.2, ein BASE-VARIANT): Sessions, Security-Stufen, DIDs mit Elementen (Kodierung, Skalierung LINEAR,
Wertetabelle, Einheit), Routinen, DTCs und die Kommunikationsparameter (CAN-IDs, Fuellbyte, P2).
Eigene Implementierung (keine Kopie aus neXtBSW), nur Standardbibliothek.
"""
from __future__ import annotations

import struct
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


class OdxError(Exception):
    pass


@dataclass
class Element:
    """Ein Feld einer DID (STRUCTURE-Parameter mit seiner DATA-OBJECT-PROP)."""
    name: str
    byte_pos: int
    bit_length: int
    base_type: str = "A_UINT32"       # A_UINT32 | A_INT32 | A_FLOAT32 | A_ASCIISTRING | A_BYTEFIELD
    big_endian: bool = True           # ODX: IS-HIGHLOW-BYTE-ORDER, Standard true
    factor: float = 1.0
    offset: float = 0.0
    texttable: dict = field(default_factory=dict)   # Rohwert -> Text
    unit: str = ""
    label: str = ""

    @property
    def size(self) -> int:
        return max(1, (self.bit_length + 7) // 8)

    def decode(self, data: bytes):
        raw = data[self.byte_pos:self.byte_pos + self.size]
        if len(raw) < self.size:
            raise OdxError(f"{self.name}: Antwort zu kurz ({len(data)} Byte)")
        if self.base_type == "A_ASCIISTRING":
            return raw.split(b"\0", 1)[0].decode("ascii", "replace")
        if self.base_type == "A_BYTEFIELD":
            return raw.hex(" ").upper()
        if self.base_type == "A_FLOAT32" and self.size == 4:
            value = struct.unpack(">f" if self.big_endian else "<f", raw)[0]
        else:
            value = int.from_bytes(raw, "big" if self.big_endian else "little",
                                   signed=self.base_type == "A_INT32")
        if self.texttable:
            return self.texttable.get(int(value), value)
        if self.factor != 1.0 or self.offset != 0.0:
            return value * self.factor + self.offset
        return value

    def numeric(self, data: bytes) -> float | None:
        """Wert als Zahl fuer Pruefungen (Wertetabelle: Rohwert), sonst None."""
        raw = data[self.byte_pos:self.byte_pos + self.size]
        if len(raw) < self.size or self.base_type in ("A_ASCIISTRING", "A_BYTEFIELD"):
            return None
        if self.base_type == "A_FLOAT32" and self.size == 4:
            return float(struct.unpack(">f" if self.big_endian else "<f", raw)[0])
        value = int.from_bytes(raw, "big" if self.big_endian else "little", signed=self.base_type == "A_INT32")
        return float(value) if self.texttable else value * self.factor + self.offset

    def encode(self, value) -> bytes:
        if self.base_type == "A_ASCIISTRING":
            return str(value).encode("ascii")[:self.size].ljust(self.size, b"\0")
        if self.base_type == "A_BYTEFIELD":
            return bytes.fromhex(str(value).replace(" ", ""))[:self.size].ljust(self.size, b"\0")
        if isinstance(value, str) and self.texttable:
            reverse = {text: raw for raw, text in self.texttable.items()}
            if value not in reverse:
                raise OdxError(f"{self.name}: '{value}' nicht in {sorted(reverse)}")
            value = reverse[value]
        if self.base_type == "A_FLOAT32" and self.size == 4:
            return struct.pack(">f" if self.big_endian else "<f", float(value))
        raw = round((float(value) - self.offset) / self.factor) if not self.texttable else int(value)
        return int(raw).to_bytes(self.size, "big" if self.big_endian else "little",
                                 signed=self.base_type == "A_INT32")


@dataclass
class Did:
    did: int
    name: str
    label: str = ""
    size: int = 0
    elements: list[Element] = field(default_factory=list)
    read: bool = False
    write: bool = False
    read_sessions: list[str] = field(default_factory=list)
    write_sessions: list[str] = field(default_factory=list)
    write_security: list[str] = field(default_factory=list)

    def element(self, name: str) -> Element | None:
        return next((e for e in self.elements if e.name == name), None)

    def decode(self, data: bytes) -> dict:
        return {e.name: e.decode(data) for e in self.elements}


@dataclass
class Routine:
    rid: int
    name: str
    label: str = ""
    steps: list[str] = field(default_factory=list)   # "start" | "stop" | "results"
    sessions: list[str] = field(default_factory=list)
    security: list[str] = field(default_factory=list)


@dataclass
class SecurityLevel:
    name: str
    level: int            # Unterfunktion RequestSeed (ungerade), SendKey = level + 1
    seed_bytes: int = 0
    key_bytes: int = 0
    method: str = ""
    sessions: list[str] = field(default_factory=list)


@dataclass
class Dtc:
    code: int
    name: str
    text: str = ""
    level: int = 0

    @property
    def display(self) -> str:
        return f"0x{self.code:06X}"


@dataclass
class OdxDiag:
    name: str
    sessions: dict[str, int] = field(default_factory=dict)          # "Extended" -> 0x03
    security: dict[str, SecurityLevel] = field(default_factory=dict)
    dids: dict[int, Did] = field(default_factory=dict)
    routines: dict[int, Routine] = field(default_factory=dict)
    dtcs: dict[int, Dtc] = field(default_factory=dict)
    comparams: dict[str, str] = field(default_factory=dict)
    source: str = ""

    def _cp(self, key: str, default: int) -> int:
        for name, value in self.comparams.items():
            if name.endswith("." + key):
                try:
                    return int(value)
                except ValueError:
                    return default
        return default

    @property
    def request_id(self) -> int:
        return self._cp("CP_CanPhysReqId", 0x7E0)

    @property
    def response_id(self) -> int:
        return self._cp("CP_CanRespUSDTId", 0x7E8)

    @property
    def functional_id(self) -> int:
        return self._cp("CP_CanFuncReqId", 0x7DF)

    @property
    def filler(self) -> int:
        return self._cp("CP_CanFillerByte", 0xCC)

    @property
    def p2_ms(self) -> float:
        return self._cp("CP_P2Max", 50_000) / 1000.0

    @property
    def p2_star_ms(self) -> float:
        return self._cp("CP_P2Star", 5_000_000) / 1000.0

    @property
    def bitrate(self) -> int:
        return self._cp("CP_Baudrate", 500_000)

    def did_by_name(self, name: str) -> Did | None:
        return next((d for d in self.dids.values() if d.name == name), None)

    def routine_by_name(self, name: str) -> Routine | None:
        return next((r for r in self.routines.values() if r.name == name), None)

    def session_id(self, name_or_id) -> int:
        if isinstance(name_or_id, int):
            return name_or_id
        text = str(name_or_id).strip()
        if text in self.sessions:
            return self.sessions[text]
        try:
            return int(text, 0)
        except ValueError:
            raise OdxError(f"Unbekannte Session '{text}' (bekannt: {', '.join(self.sessions)})") from None


# -- Lesen --------------------------------------------------------------------------

def _text(element, path: str, default: str = "") -> str:
    found = element.findtext(path) if element is not None else None
    return found.strip() if found else default


def _read_xml(path: Path) -> bytes:
    if path.suffix.lower() == ".pdx" or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".odx-d")]
            if not names:
                raise OdxError(f"{path.name}: keine .odx-d im Archiv")
            return archive.read(names[0])
    return path.read_bytes()


def load(path) -> OdxDiag:
    path = Path(path)
    try:
        root = ET.fromstring(_read_xml(path))
    except (OSError, zipfile.BadZipFile) as exc:
        raise OdxError(f"{path}: {exc}") from exc
    except ET.ParseError as exc:
        raise OdxError(f"{path}: kein gueltiges XML ({exc})") from exc
    return parse(root, source=str(path))


def parse(root, source: str = "") -> OdxDiag:
    by_id = {e.get("ID"): e for e in root.iter() if e.get("ID")}
    layer = next(root.iter("BASE-VARIANT"), None)
    if layer is None:
        raise OdxError("Kein BASE-VARIANT in der ODX-Datei")
    diag = OdxDiag(name=_text(layer, "SHORT-NAME"), source=source)
    units = {u.get("ID"): _text(u, "DISPLAY-NAME") or _text(u, "SHORT-NAME") for u in root.iter("UNIT")}
    states = {s.get("ID"): _text(s, "SHORT-NAME") for s in root.iter("STATE")}

    def ref(element, path: str):
        node = element.find(path) if element is not None else None
        return by_id.get(node.get("ID-REF")) if node is not None else None

    def coded(params, semantic: str) -> int | None:
        for param in params.iter("PARAM"):
            if param.get("SEMANTIC") == semantic and param.find("CODED-VALUE") is not None:
                return int(_text(param, "CODED-VALUE"))
        return None

    def pre_states(service) -> tuple[list[str], list[str]]:
        sessions, security = [], []
        for node in service.iter("PRE-CONDITION-STATE-REF"):
            state_id = node.get("ID-REF", "")
            name = states.get(state_id, state_id.rsplit(".", 1)[-1])
            (sessions if ".Session." in state_id else security).append(name)
        return sessions, security

    def dop_element(name: str, byte_pos: int, dop) -> Element:
        coded_type = dop.find("DIAG-CODED-TYPE")
        element = Element(name=name, byte_pos=byte_pos,
                          bit_length=int(_text(coded_type, "BIT-LENGTH", "8")) if coded_type is not None else 8,
                          base_type=coded_type.get("BASE-DATA-TYPE", "A_UINT32") if coded_type is not None else "A_UINT32",
                          big_endian=(coded_type.get("IS-HIGHLOW-BYTE-ORDER", "true") != "false")
                          if coded_type is not None else True,
                          label=_text(dop, "LONG-NAME"))
        compu = dop.find("COMPU-METHOD")
        category = _text(compu, "CATEGORY", "IDENTICAL")
        if category == "LINEAR":
            nums = [float(v.text) for v in compu.findall(".//COMPU-NUMERATOR/V")]
            dens = [float(v.text) for v in compu.findall(".//COMPU-DENOMINATOR/V")] or [1.0]
            if len(nums) >= 2:
                element.offset, element.factor = nums[0] / dens[0], nums[1] / dens[0]
        elif category == "TEXTTABLE":
            for scale in compu.iter("COMPU-SCALE"):
                lower = scale.findtext("LOWER-LIMIT")
                vt = scale.findtext("COMPU-CONST/VT")
                if lower is not None and vt is not None:
                    element.texttable[int(float(lower))] = vt
        unit_ref = dop.find("UNIT-REF")
        if unit_ref is not None:
            element.unit = units.get(unit_ref.get("ID-REF"), "")
        return element

    def value_elements(response) -> tuple[list[Element], int]:
        """Elemente der Nutzdaten einer Antwort, Byte-Positionen ab Beginn der Nutzdaten."""
        elements: list[Element] = []
        size = 0
        for param in response.find("PARAMS") if response is not None and response.find("PARAMS") is not None else []:
            if param.get(XSI_TYPE) != "VALUE":
                continue
            start = int(_text(param, "BYTE-POSITION", "0"))
            target = ref(param, "DOP-REF")
            if target is None:
                continue
            if target.tag == "STRUCTURE":
                size = int(_text(target, "BYTE-SIZE", "0"))
                for sub in target.find("PARAMS") if target.find("PARAMS") is not None else []:
                    sub_dop = ref(sub, "DOP-REF")
                    if sub_dop is not None:
                        elements.append(dop_element(_text(sub, "SHORT-NAME"),
                                                    int(_text(sub, "BYTE-POSITION", "0")), sub_dop))
            else:
                element = dop_element(_text(param, "SHORT-NAME"), 0, target)
                elements.append(element)
                size = max(size, element.size)
            _ = start
        return elements, size

    for service in root.iter("DIAG-SERVICE"):
        semantic = service.get("SEMANTIC", "")
        name = _text(service, "SHORT-NAME")
        request = ref(service, "REQUEST-REF")
        response = ref(service, "POS-RESPONSE-REFS/POS-RESPONSE-REF")
        sessions, security = pre_states(service)
        if request is None:
            continue
        if semantic == "SESSION":
            sub = coded(request, "SUBFUNCTION")
            if sub is not None:
                diag.sessions[name.removeprefix("Session_")] = sub
        elif semantic == "SECURITY" and name.startswith("RequestSeed_"):
            level_name = name.removeprefix("RequestSeed_")
            sub = coded(request, "SUBFUNCTION") or 1
            seed_elements, _ = value_elements(response)
            sds = [sd.text or "" for sd in service.iter("SD")]
            entry = diag.security.setdefault(level_name, SecurityLevel(level_name, sub))
            entry.level = sub
            entry.seed_bytes = sum(e.size for e in seed_elements)
            entry.method = sds[0] if sds else ""
            entry.sessions = sessions
        elif semantic == "SECURITY" and name.startswith("SendKey_"):
            level_name = name.removeprefix("SendKey_")
            key_elements, _ = value_elements(request)
            entry = diag.security.setdefault(level_name, SecurityLevel(level_name, (coded(request, "SUBFUNCTION") or 2) - 1))
            entry.key_bytes = sum(e.size for e in key_elements)
        elif semantic in ("DATA-READ", "DATA-WRITE"):
            did_id = coded(request, "ID")
            if did_id is None:
                continue
            did_name = name.split("_", 1)[1] if "_" in name else name
            did = diag.dids.setdefault(did_id, Did(did_id, did_name, label=_text(service, "LONG-NAME")))
            if semantic == "DATA-READ":
                did.read = True
                did.read_sessions = sessions
                did.elements, did.size = value_elements(response)
            else:
                did.write = True
                did.write_sessions = sessions
                did.write_security = security
                if not did.elements:
                    did.elements, did.size = value_elements(request)
        elif semantic == "ROUTINE":
            rid = coded(request, "ID")
            if rid is None:
                continue
            base, _, step = name.rpartition("_")
            routine = diag.routines.setdefault(rid, Routine(rid, base or name, label=_text(service, "LONG-NAME"),
                                                            sessions=sessions, security=security))
            if step.lower() in ("start", "stop", "results"):
                routine.steps.append(step.lower())

    for dtc in root.iter("DTC"):
        try:
            code = int(_text(dtc, "TROUBLE-CODE"))
        except ValueError:
            continue
        diag.dtcs[code] = Dtc(code, _text(dtc, "SHORT-NAME"), _text(dtc, "TEXT"), int(_text(dtc, "LEVEL", "0") or 0))
    for cp in root.iter("COMPARAM-REF"):
        value = cp.findtext("SIMPLE-VALUE") or cp.findtext("VALUE")
        if value is not None:
            diag.comparams[cp.get("ID-REF", "")] = value.strip()
    return diag
