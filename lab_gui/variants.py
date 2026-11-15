"""Varianten und Parametersaetze (FT-5, PI-55 a, 0.66.0). Qt-frei.

Eine Variante ist eine Datei <gemeinsamer Ordner>/varianten/<name>.json:

    {"format": "labcontrol-variant", "name": "PowerUnit",
     "description": "PowerUnit-Platine am Pi",
     "roles": {"Versorgung": "psu:0001", "Last": "load:A1B2"},
     "vars":  {"U_nom": 12.0, "I_max": 2.5}}

- Rollen: Ein Schritt kann statt einer Geraete-ID eine Rolle tragen (device_id/cond_device_id "role:<Name>").
  Vor dem Lauf ersetzt apply_roles sie durch das Geraet der gewaehlten Variante.
- Parameter: Variablen, mit denen der Lauf startet (vor --var). Ein Schritt kann Zahlenfelder aus Variablen
  nehmen: params["refs"] = {"value": "U_nom", "check_min": "U_min", ...} (siehe REF_FIELDS); der Runner setzt die
  Werte beim Ausfuehren ein (resolve_refs).
- Matrix: CSV-Datei, eine Zeile = ein Lauf; Spalte "variant" (optional) waehlt die Variante, alle anderen Spalten
  sind Variablen (read_matrix).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from i18n import tr

FORMAT = "labcontrol-variant"
FOLDER = "varianten"
ROLE_PREFIX = "role:"
# Zahlenfelder eines Schritts, die aus einer Variable kommen duerfen
REF_FIELDS = {
    "value": "Sollwert",
    "duration": "Dauer",
    "check_min": "Prüfung min",
    "check_max": "Prüfung max",
    "cond_value": "Bedingungswert",
}
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_ROLE = re.compile(r"^[^:/\\]{1,40}$")
MATRIX_VARIANT_COLUMNS = ("variant", "variante")


@dataclass
class Variant:
    name: str
    roles: dict[str, str] = field(default_factory=dict)
    vars: dict[str, float] = field(default_factory=dict)
    description: str = ""
    path: Path | None = None

    def to_json(self) -> dict:
        return {"format": FORMAT, "name": self.name, "description": self.description,
                "roles": dict(sorted(self.roles.items())), "vars": dict(sorted(self.vars.items()))}


# -- Rollen -------------------------------------------------------------------------------------

def is_role(device_id: str) -> bool:
    return str(device_id or "").startswith(ROLE_PREFIX)


def role_ref(name: str) -> str:
    return ROLE_PREFIX + name


def role_name(device_id: str) -> str:
    return str(device_id)[len(ROLE_PREFIX):] if is_role(device_id) else ""


def device_kind_of(device_id: str) -> str:
    """Geraeteart aus der ID ("psu:0001" -> "psu", "@raspberrypi/load:1" -> "load")."""
    text = str(device_id or "")
    if text.startswith("@") and "/" in text:
        text = text.split("/", 1)[1]
    return text.split(":", 1)[0] if ":" in text else ""


def valid_role(name: str) -> bool:
    return bool(_ROLE.match(name or "")) and name.strip() == name


def valid_var(name: str) -> bool:
    return bool(_NAME.match(name or ""))


def used_roles(steps) -> set[str]:
    return {role_name(getattr(s, a)) for s in steps for a in ("device_id", "cond_device_id")
            if is_role(getattr(s, a, ""))}


def used_refs(steps) -> set[str]:
    names = set()
    for step in steps:
        names.update(str(v) for v in ((step.params or {}).get("refs") or {}).values() if v)
    return names


def apply_roles(steps, variant: Variant | None) -> list[str]:
    """Rollen in `steps` durch die Geraete der Variante ersetzen (aendert die Schritte). Liefert Probleme
    (Rolle ohne Variante, nicht belegte Rolle, Geraet passt nicht zur Art des Schritts)."""
    problems: list[str] = []
    for step in steps:
        for attr, kind_attr in (("device_id", "device_kind"), ("cond_device_id", "cond_device_kind")):
            ref = getattr(step, attr, "")
            if not is_role(ref):
                continue
            name = role_name(ref)
            device = (variant.roles.get(name) if variant is not None else None) or ""
            if not device:
                problem = (tr("Rolle '{role}' braucht eine Variante", role=name) if variant is None
                           else tr("Rolle '{role}' ist in Variante '{variant}' nicht belegt", role=name,
                                   variant=variant.name))
                if problem not in problems:
                    problems.append(problem)
                continue
            kind = getattr(step, kind_attr, "") or ""
            if kind and device_kind_of(device) and device_kind_of(device) != kind:
                problem = tr("Rolle '{role}' ist {device}, der Schritt braucht ein Gerät der Art {kind}",
                             role=name, device=device, kind=kind)
                if problem not in problems:
                    problems.append(problem)
                continue
            setattr(step, attr, device)
    return problems


# -- Parameter in Schritten ---------------------------------------------------------------------

def step_refs(step) -> dict[str, str]:
    refs = (getattr(step, "params", None) or {}).get("refs") or {}
    return {k: str(v) for k, v in refs.items() if k in REF_FIELDS and v}


def resolve_refs(step, variables: dict[str, float]) -> str:
    """Setzt die Felder eines Schritts aus den Variablen ein; liefert "" oder eine Fehlermeldung."""
    for attr, name in step_refs(step).items():
        if name not in variables:
            return tr("Parameter '{name}' ist nicht gesetzt", name=name)
        setattr(step, attr, float(variables[name]))
        if attr == "value" and "value" in (step.params or {}):
            step.params = {**step.params, "value": float(variables[name])}   # Restbus-Signal (FT-6)
    return ""


def refs_summary(step) -> str:
    refs = step_refs(step)
    return ", ".join(f"{tr(REF_FIELDS[k])} = {v}" for k, v in refs.items())


# -- Dateien ------------------------------------------------------------------------------------

def _number(value) -> float:
    if isinstance(value, bool):
        raise ValueError(str(value))
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).strip().replace(",", "."))


def parse_variant(data: dict, path: Path | None = None) -> Variant:
    if not isinstance(data, dict):
        raise ValueError(tr("Keine Variantendatei"))
    name = str(data.get("name") or (path.stem if path else "")).strip()
    if not name:
        raise ValueError(tr("Variante ohne Namen"))
    roles = {}
    for role, device in (data.get("roles") or {}).items():
        role = str(role).strip()
        if not valid_role(role):
            raise ValueError(tr("Ungültiger Rollenname: {name}", name=role))
        roles[role] = str(device or "").strip()
    variables = {}
    for var, value in (data.get("vars") or {}).items():
        if not valid_var(str(var)):
            raise ValueError(tr("Ungültiger Parametername: {name}", name=var))
        try:
            variables[str(var)] = _number(value)
        except ValueError:
            raise ValueError(tr("Parameter {name}: keine Zahl ({value})", name=var, value=value)) from None
    return Variant(name, roles, variables, str(data.get("description") or ""), path)


def load_variant(path: Path) -> Variant:
    return parse_variant(json.loads(Path(path).read_text(encoding="utf-8")), Path(path))


def file_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()).strip("._") or "variante"
    return f"{safe}.json"


def save_variant(variant: Variant, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = variant.path if variant.path is not None and variant.path.parent == folder \
        else folder / file_name(variant.name)
    path.write_text(json.dumps(variant.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
    variant.path = path
    return path


def list_variants(folder: Path) -> tuple[list[Variant], list[str]]:
    """Alle Varianten im Ordner (nach Name), dazu Probleme mit unlesbaren Dateien."""
    variants, problems = [], []
    if folder is None or not folder.is_dir():
        return variants, problems
    for path in sorted(folder.glob("*.json")):
        try:
            variants.append(load_variant(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            problems.append(f"{path.name}: {exc}")
    variants.sort(key=lambda v: v.name.lower())
    return variants, problems


def find_variant(spec: str, folder: Path | None) -> Variant:
    """Variante per Name (aus dem Ordner) oder Dateipfad."""
    spec = str(spec or "").strip()
    if not spec:
        raise ValueError(tr("Keine Variante angegeben"))
    path = Path(spec)
    if path.suffix.lower() == ".json" and path.is_file():
        return load_variant(path)
    variants, _problems = list_variants(folder) if folder is not None else ([], [])
    for variant in variants:
        if variant.name == spec or (variant.path is not None and variant.path.stem == spec):
            return variant
    lowered = [v for v in variants if v.name.lower() == spec.lower()]
    if len(lowered) == 1:
        return lowered[0]
    raise ValueError(tr("Variante '{name}' nicht gefunden", name=spec))


def known_roles(variants: list[Variant]) -> dict[str, str]:
    """Rolle -> Geraeteart (aus der ersten Variante, die sie belegt)."""
    roles: dict[str, str] = {}
    for variant in variants:
        for role, device in variant.roles.items():
            if role not in roles or not roles[role]:
                roles[role] = device_kind_of(device)
    return roles


def known_vars(variants: list[Variant]) -> list[str]:
    return sorted({name for v in variants for name in v.vars})


# -- Matrix -------------------------------------------------------------------------------------

def read_matrix(path: Path) -> list[dict]:
    """CSV (Komma oder Semikolon) -> [{"variant": str, "vars": {..}, "label": str}], eine Zeile = ein Lauf."""
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else ",", delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
    result = []
    for number, row in enumerate(rows, start=2):
        variant = ""
        variables: dict[str, float] = {}
        for key, value in row.items():
            if key is None:
                raise ValueError(tr("Matrix Zeile {n}: mehr Werte als Spalten", n=number))
            key = key.strip()
            value = (value or "").strip()
            if key.lower() in MATRIX_VARIANT_COLUMNS:
                variant = value
            elif key and value != "":
                if not valid_var(key):
                    raise ValueError(tr("Matrix: ungültiger Spaltenname {name}", name=key))
                try:
                    variables[key] = _number(value)
                except ValueError:
                    raise ValueError(tr("Matrix Zeile {n}, {name}: keine Zahl ({value})", n=number, name=key,
                                        value=value)) from None
        label = ", ".join([variant] * bool(variant) + [f"{k}={v:g}" for k, v in variables.items()])
        result.append({"variant": variant, "vars": variables, "label": label or tr("Zeile {n}", n=number)})
    if not result:
        raise ValueError(tr("Matrix ohne Zeilen"))
    return result


def run_variables(variant: Variant | None, overrides: dict[str, float] | None = None) -> dict[str, float]:
    """Startvariablen eines Laufs: Parameter der Variante, darueber --var bzw. Matrixzeile."""
    values = dict(variant.vars) if variant is not None else {}
    values.update(overrides or {})
    return values
