"""SCPI-Oszilloskope im Netzwerk: welcher Treiber gehört zu einer Adresse?

Alle SCPI-Oszilloskope teilen sich die Geräteart "scpiscope" (eine
Adressliste in den Einstellungen, dieselbe Kachel). Welcher Hersteller hinter
einer Adresse steckt, sagt erst *IDN? -- das fragt probe() und wählt danach
Adapter und Fähigkeiten:

- UNI-T MSO1000HD: unit_mso1000hd / unit_mso_adapter.py
- R&S RTB2000:     rs_rtb2000 / rs_rtb_adapter.py

Die Simulatoren haben je eine eigene Pseudo-Adresse ("SIM", "SIM-RTB") und
brauchen keine Abfrage.
"""
from __future__ import annotations

from dataclasses import dataclass

from rs_rtb2000 import driver as rtb_driver
from scope_api.base import Capabilities, ScopeError
from scope_api.rs_rtb_adapter import capabilities_for as rtb_capabilities, open_rtb
from scope_api.unit_mso_adapter import capabilities_for as unit_mso_capabilities, open_unit_mso
from unit_mso1000hd import driver as mso_driver

FAMILY_UNIT_MSO = "unit_mso"
FAMILY_RS_RTB = "rs_rtb"

SIM_DEVICE_IDS = (mso_driver.SIM_DEVICE_ID, rtb_driver.SIM_DEVICE_ID)

# Je eingetragener Adresse der Weg (Socket oder VXI-11), der zuletzt geantwortet hat: probe()
# versucht ihn zuerst. Am UNI-T lief sonst jede Erkennung erst 2-10 s gegen den Socket-Port,
# den das Gerät unbeantwortet lässt -- und hielt dabei das Gerät für Erfassungen besetzt (1.0.2).
_LAST_GOOD: dict[str, str] = {}


class ScpiProbeError(RuntimeError):
    """Adresse nicht erreichbar oder kein bekanntes Oszilloskop."""


@dataclass(frozen=True)
class ScpiIdn:
    family: str
    vendor: str
    model: str
    serial: str
    firmware: str


def family_for(vendor: str, model: str) -> str | None:
    if rtb_driver.is_rtb_idn(vendor, model):
        return FAMILY_RS_RTB
    if "UNI" in vendor.upper():
        return FAMILY_UNIT_MSO
    return None


def family_for_model(model: str) -> str:
    """Nur Modellname bekannt (Gerätestand im ScopeService): RTB2... ist R&S,
    alles andere wie bisher UNI-T."""
    return FAMILY_RS_RTB if model.upper().startswith("RTB2") else FAMILY_UNIT_MSO


def parse_idn(text: str) -> ScpiIdn:
    parts = [p.strip() for p in text.split(",")]
    parts += [""] * (4 - len(parts))
    family = family_for(parts[0], parts[1])
    if family is None:
        raise ScpiProbeError(f"Kein unterstütztes Oszilloskop: *IDN? -> {text!r}")
    return ScpiIdn(family, parts[0], parts[1], parts[2], parts[3])


def probe(resource: str, timeout_s: float = 2.0) -> ScpiIdn:
    """Öffnen, *IDN?, schließen -- für die Erkennung im DeviceWorker."""
    if resource == mso_driver.SIM_RESOURCE:
        from unit_mso1000hd.mock import SIM_IDN
        return parse_idn(SIM_IDN)
    if resource == rtb_driver.SIM_RESOURCE:
        from rs_rtb2000.mock import SIM_IDN
        return parse_idn(SIM_IDN)
    # Zuerst der SCPI-Socket desselben Hosts, dann die eingetragene Adresse
    # (VXI-11). Am RTB2002 gingen nach VXI-11-Timeouts die Verbindungen im
    # Gerät aus ("error creating link: 3"), der Socket blieb erreichbar -- und
    # den nutzt der RTB-Treiber ohnehin für die Daten. Ein Gerät ohne Socket
    # (UNI-T?) weist die Verbindung sofort ab, das kostet kaum Zeit.
    # Transport des UNI-T-Treibers: reines pyvisa, herstellerneutral.
    last: Exception | None = None
    order = [_LAST_GOOD.get(resource), rtb_driver.socket_resource(resource), resource]
    for candidate in dict.fromkeys(filter(None, order)):
        try:
            transport = mso_driver.VisaTransport(candidate, timeout_s)
        except mso_driver.MSO1000HDError as exc:
            last = exc
            continue
        try:
            transport.write("*IDN?")
            text = transport.read_line()
        except mso_driver.MSO1000HDError as exc:
            last = exc
            continue
        finally:
            transport.close()
        info = parse_idn(text)
        _LAST_GOOD[resource] = candidate
        return info
    raise ScpiProbeError(str(last))


def open_scope(resource: str):
    """UnitMsoScope oder RtbScope für ``resource``. Wirft ScopeError."""
    if resource == mso_driver.SIM_RESOURCE:
        return open_unit_mso(resource)
    if resource == rtb_driver.SIM_RESOURCE:
        return open_rtb(resource)
    try:
        info = probe(resource, mso_driver.DEFAULT_TIMEOUT_S)
    except ScpiProbeError as exc:
        raise ScopeError("device_error", str(exc)) from exc
    if info.family == FAMILY_RS_RTB:
        return open_rtb(resource)
    return open_unit_mso(resource)


def capabilities_for(model: str, serial: str, simulated: bool = False) -> Capabilities:
    if family_for_model(model) == FAMILY_RS_RTB:
        return rtb_capabilities(model, serial, simulated)
    return unit_mso_capabilities(model, serial, simulated)


def is_simulated(device_id: str) -> bool:
    return device_id in SIM_DEVICE_IDS
