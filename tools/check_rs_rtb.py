"""Pruefskript fuer das R&S RTB2000 (Treiber, scope_api-Adapter, Erkennung, ScopeService).

Ohne Argument: nur gegen den Simulator (device-driver/rs_rtb2000/mock.py,
SCPI-Ebene -- der echte Treiber- und Adaptercode laeuft mit). Keine Hardware.

Mit --hardware --address <IP>: zusaetzlich am echten Geraet ueber LAN
(VXI-11, pyvisa-py). Voraussetzungen: Oszilloskop im Netz mit fester IP, am
Geraet Setup > Interface "Ethernet", LabControl haelt es gerade nicht (Kachel
nicht "Netzwerk/MCP"). Die Pruefung stellt nur Erfassungen ein und liest --
das Geraet bleibt danach gestoppt mit der letzten Erfassung auf dem Bildschirm.
Mit --signal-ch1 "rechteck:<f_hz>:<high_v>" wird an CH1 ein bekanntes Signal
erwartet und Frequenz/Pegel, Trigger-Lage und Offset geprueft. Bequem ist der
Tastkopf-Abgleichausgang ("Probe Comp.", 1 kHz Rechteck) oder der eingebaute
Generator (Option RTB-B6).

Aufruf:  python tools/check_rs_rtb.py [--hardware --address 192.168.1.60 [--signal-ch1 rechteck:1000:2.5]]
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))  # Geraete-Treiber
sys.path.insert(0, str(ROOT / "lab_gui"))        # scope_service (flache Importe wie in der App)

from rs_rtb2000 import driver, mock  # noqa: E402
from scope_api import analysis, scpi  # noqa: E402
from scope_api.base import AcquireRequest, ScopeError  # noqa: E402
from scope_api.rs_rtb_adapter import open_rtb  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        FAILURES.append(f"{name} {detail}".strip())
        print(f"  FEHLER  {name} {detail}")
    else:
        print(f"  ok      {name}")


def near(value, target, rel=0.01, abs_=0.0) -> bool:
    return value is not None and abs(value - target) <= max(abs(target) * rel, abs_)


def expect_error(name: str, code: str, func) -> None:
    try:
        func()
    except ScopeError as exc:
        check(name, exc.code == code, f"(Code {exc.code!r}, erwartet {code!r}: {exc.message})")
        return
    check(name, False, f"(kein Fehler, erwartet {code!r})")


def request(**overrides) -> AcquireRequest:
    data = {
        "channels": [{"name": "CH1", "range_v": 5}],
        "timing": {"duration_s": 0.005, "pre_trigger_pct": 20},
        "trigger": {"mode": "single", "source": "CH1", "level_v": 1.65, "edge": "rising", "timeout_s": 1},
    }
    data.update(overrides)
    return AcquireRequest.from_dict(data)


def edge_offset(capture, name: str, level: float, rising: bool) -> float:
    """Punkte von trigger_index bis zur naechsten Kreuzung von level in der Richtung (unterabtastgenau)."""
    v = capture.channels[name].volts
    below = v < level
    idx = np.nonzero(below[:-1] & ~below[1:])[0] if rising else np.nonzero(~below[:-1] & below[1:])[0]
    if idx.size == 0:
        return float("inf")
    k = idx[np.argmin(np.abs(idx - capture.trigger_index))]
    return float(k + (level - v[k]) / (v[k + 1] - v[k]) - capture.trigger_index)


def measure(capture, name: str, items: list[str]) -> dict:
    values, _reasons = analysis.measure(capture.time_s, capture.channels[name].volts, items)
    return values


# -- reine Funktionen ------------------------------------------------------------


def check_pure() -> None:
    print("Treiber: Modell, Optionen, Erkennung")
    m = driver.parse_model("RTB2002")
    check("RTB2002: 2 Kanaele, 70 MHz", m.channels == 2 and m.bandwidth_hz == 70e6)
    m = driver.parse_model("RTB2004", "B1,B243,K1")
    check("RTB2004 mit B243: 4 Kanaele, 300 MHz", m.channels == 4 and m.bandwidth_hz == 300e6)
    check("RTB2002 mit RTB-B222: 200 MHz", driver.parse_model("RTB2002", "RTB-B222,0,0").bandwidth_hz == 200e6)
    check("R&S erkannt", driver.is_rtb_idn("Rohde&Schwarz", "RTB2004"))
    check("UNI-T kein RTB", not driver.is_rtb_idn("UNI-T Technologies", "MSO1154HD-S"))
    check("VXI-11 -> Socket 5025", driver.socket_resource("TCPIP0::192.168.1.60::inst0::INSTR")
          == "TCPIP0::192.168.1.60::5025::SOCKET")
    check("Socket/USB bleiben", driver.socket_resource("TCPIP0::192.168.1.60::5025::SOCKET") is None
          and driver.socket_resource("USB0::0x0AAD::0x01D6::123::INSTR") is None)
    head = driver.Header(xstart=-4.998e-7, xstop=5.0e-7, points=5000, values_per_sample=1)
    check("Kopf: XINC wie Handbuch S. 244 (2e-10 s)", near(head.xinc, 2e-10, 1e-3), str(head.xinc))

    check("Nachlauf der Daten: bis 100k keiner, ab 200k nach Kanalzahl",
          driver.trigger_delay_points(100_000, 1) == 0 and driver.trigger_delay_points(200_000, 1) == 61.5
          and driver.trigger_delay_points(500_000, 2) == 27.0 and driver.trigger_delay_points(20_000_000, 1) == 50.5)

    print("Weiche scope_api.scpi")
    idn = scpi.parse_idn("Rohde&Schwarz,RTB2002,1333.1005k02/101234,02.202")
    check("*IDN? R&S -> rs_rtb", idn.family == scpi.FAMILY_RS_RTB and idn.serial == "1333.1005k02/101234")
    idn = scpi.parse_idn("UNI-T Technologies,MSO1154HD-S,ABC,1.00.0009")
    check("*IDN? UNI-T -> unit_mso", idn.family == scpi.FAMILY_UNIT_MSO)
    try:
        scpi.parse_idn("KEYSIGHT,DSOX1102G,X,1")
        check("Fremdes Geraet abgelehnt", False)
    except scpi.ScpiProbeError:
        check("Fremdes Geraet abgelehnt", True)
    check("Probe SIM-RTB ohne Netz", scpi.probe(driver.SIM_RESOURCE).model == mock.SIM_MODEL)
    check("Probe SIM (UNI-T) unveraendert", scpi.probe("SIM").family == scpi.FAMILY_UNIT_MSO)
    caps = scpi.capabilities_for("RTB2002", "X", simulated=True).to_dict()
    names = [c["name"] for c in caps["channels"]]
    check("Faehigkeiten RTB2002: CH1, CH2, R&S", names == ["CH1", "CH2"] and caps["vendor"] == "Rohde & Schwarz",
          str(names))
    caps = scpi.capabilities_for("MSO1154HD-S", "X").to_dict()
    check("Faehigkeiten UNI-T weiter ueber die Weiche", caps["vendor"] == "UNI-T" and len(caps["channels"]) == 4)
    check("Simulierte IDs", scpi.is_simulated(driver.SIM_DEVICE_ID) and scpi.is_simulated("scpiscope:SIM")
          and not scpi.is_simulated("scpiscope:192.168.1.60"))


# -- Simulator: Treiber + Adapter ----------------------------------------------------


def check_sim() -> None:
    print("Simulator: Erfassungen ueber Treiber und Adapter")
    mock.SimulatedRTB2000.signals = mock.default_signals()
    mock.SimulatedRTB2000.hang_after_run = False
    scope = open_rtb(driver.SIM_RESOURCE)
    try:
        caps = scope.capabilities()
        check("Offenes Geraet: Simulation, 70 MHz", caps.model.endswith("(Simulation)") and caps.bandwidth_hz == 70e6)

        cap = scope.acquire(request())
        values = measure(cap, "CH1", ["frequency", "duty", "vtop", "vbase"])
        check("Einzelerfassung: getriggert", cap.triggered is True)
        check("CH1 Rechteck 1 kHz", near(values["frequency"], 1000.0, 0.01), str(values["frequency"]))
        check("CH1 Tastgrad 50 %", near(values["duty"], 50.0, abs_=1.0), str(values["duty"]))
        check("CH1 Pegel 0 / 3,3 V", near(values["vtop"], 3.3, 0.03) and near(values["vbase"], 0.0, abs_=0.05),
              f"{values['vtop']} / {values['vbase']}")
        check("Pre-Trigger 20 %: Trigger-Index bei 20 % der Punkte",
              near(cap.trigger_index, 0.2 * cap.samples, 0.02), f"{cap.trigger_index} von {cap.samples}")
        check("Keine Warnung zum Pre-Trigger", not any("TIMebase:POSition" in w for w in cap.warnings),
              str(cap.warnings))
        off = edge_offset(cap, "CH1", 1.65, True)
        check("Steigende Flanke am Trigger-Index (+-2 Punkte, Nachlauf der Daten ausgeglichen)", abs(off) <= 2,
              f"({off:+.1f} Punkte)")
        check("Bereich zurueckgelesen: 5 V -> 1 V/div = +-5 V (10 Teilungen)", near(cap.channels["CH1"].range_v, 5.0))
        check("Nicht uebersteuert", cap.channels["CH1"].overrange is False)
        check("Mindestens 250 000 Samples", cap.samples >= 250_000, str(cap.samples))

        cap = scope.acquire(request(
            channels=[{"name": "CH1", "range_v": 5}, {"name": "CH2", "range_v": 2}],
            trigger={"mode": "single", "source": "CH2", "level_v": 0.0, "edge": "falling", "timeout_s": 1}))
        values = measure(cap, "CH2", ["frequency", "vpp"])
        check("Zwei Kanaele, Trigger an CH2 fallend", cap.triggered is True and set(cap.channels) == {"CH1", "CH2"})
        check("CH2 Sinus 1 kHz, 2 Vpp", near(values["frequency"], 1000.0, 0.01) and near(values["vpp"], 2.0, 0.05),
              str(values))
        v = cap.channels["CH2"].volts
        i = cap.trigger_index
        check("Fallende Flanke am Trigger-Index", v[max(0, i - 200)] > 0.0 > v[min(v.size - 1, i + 200)])
        mock.SimulatedRTB2000.signals = {**mock.default_signals(), "CH2": mock.SquareWave(2000.0, 0.0, 1.0)}
        cap = scope.acquire(request(
            channels=[{"name": "CH1", "range_v": 5}, {"name": "CH2", "range_v": 2}],
            trigger={"mode": "single", "source": "CH2", "level_v": 0.5, "edge": "falling", "timeout_s": 1}))
        mock.SimulatedRTB2000.signals = mock.default_signals()
        off = edge_offset(cap, "CH2", 0.5, False)
        check("Zwei Kanaele, Rechteck an CH2: fallende Flanke am Trigger-Index (+-2 Punkte, Nachlauf ausgeglichen)",
              abs(off) <= 2, f"({off:+.1f} Punkte)")
        cap = scope.acquire(request(timing={"duration_s": 5e-5, "pre_trigger_pct": 20}))
        off = edge_offset(cap, "CH1", 1.65, True)
        check("Kurze Aufzeichnung (ohne Nachlauf): Flanke am Trigger-Index", abs(off) <= 2, f"({off:+.1f} Punkte)")

        # Offset: Gleichspannung mit kleiner Welligkeit an CH2.
        mock.SimulatedRTB2000.signals = {**mock.default_signals(),
                                         "CH2": lambda t: 12.0 + 0.025 * mock.np.sin(2 * mock.np.pi * 100e3 * t)}
        cap = scope.acquire(request(
            channels=[{"name": "CH2", "range_v": 0.1, "offset_v": 12.0}],
            timing={"duration_s": 50e-6}, trigger={"mode": "none"}))
        values = measure(cap, "CH2", ["vmean", "vpp", "frequency"])
        check("Offset 12 V, Bereich 100 mV: Welligkeit sichtbar",
              near(values["vmean"], 12.0, abs_=0.005) and near(values["vpp"], 0.05, 0.2), str(values))
        check("Welligkeit 100 kHz", near(values["frequency"], 100e3, 0.02), str(values["frequency"]))
        check("Ohne Trigger: triggered None", cap.triggered is None)
        mock.SimulatedRTB2000.signals = mock.default_signals()

        mock.SimulatedRTB2000.signals = {**mock.default_signals(), "CH2": mock.Constant(0.0)}
        cap = scope.acquire(request(
            channels=[{"name": "CH2", "range_v": 1}],
            trigger={"mode": "auto", "source": "CH2", "level_v": 0.5, "timeout_s": 0.1}))
        check("Auto ohne Flanke: erzwungen, Warnung",
              cap.triggered is False and any("Kein Trigger" in w for w in cap.warnings), str(cap.warnings))

        expect_error("Einzeltrigger ohne Flanke: trigger_timeout", "trigger_timeout", lambda: scope.acquire(request(
            channels=[{"name": "CH2", "range_v": 1}],
            trigger={"mode": "single", "source": "CH2", "level_v": 0.5, "timeout_s": 0.1})))
        mock.SimulatedRTB2000.signals = mock.default_signals()

        # Am RTB2002 gefunden: das Gerät begrenzt bei 99,9 % des Bereichs.
        cap = scope.acquire(request(channels=[{"name": "CH1", "range_v": 1, "offset_v": 2.5}],
                                    timing={"duration_s": 0.005}, trigger={"mode": "none"}))
        check("Abgeschnittene Kurve (0 V unter 1,5 V) als uebersteuert erkannt",
              cap.channels["CH1"].overrange is True, str(float(cap.channels["CH1"].volts.min())))

        cap = scope.acquire(request(timing={"sample_interval_s": 1e-6, "samples": 25_000}, trigger={"mode": "none"}))
        check("Abtastintervall-Anfrage: hoechstens 1 us", cap.sample_interval_s <= 1e-6 * (1 + 1e-6),
              str(cap.sample_interval_s))

        cap = scope.acquire(request(channels=[{"name": "CH1", "range_v": 50, "probe": 10}], trigger={"mode": "none"}))
        check("Tastkopf 10:1: Bereich an der Spitze", cap.channels["CH1"].range_v >= 50.0,
              str(cap.channels["CH1"].range_v))

        expect_error("CH3 gibt es am RTB2002 nicht", "setting_not_supported",
                     lambda: scope.acquire(request(channels=[{"name": "CH3", "range_v": 1}], trigger={"mode": "none"})))
        expect_error("Bereich zu gross", "setting_not_supported",
                     lambda: scope.acquire(request(channels=[{"name": "CH1", "range_v": 500}])))
        expect_error("Trigger-Pegel ausserhalb des Bereichs", "level_out_of_range",
                     lambda: scope.acquire(request(channels=[{"name": "CH1", "range_v": 0.5}],
                                                   trigger={"mode": "single", "source": "CH1", "level_v": 3})))
        expect_error("Zu viele Samples", "setting_not_supported",
                     lambda: scope.acquire(request(timing={"duration_s": 1.0, "samples": 20_000_000})))

        mock.SimulatedRTB2000.hang_after_run = True
        try:
            started = time.monotonic()
            expect_error("Geraet wird nicht fertig: device_error", "device_error",
                         lambda: scope.acquire(request(trigger={"mode": "none"}, timing={"duration_s": 1e-3})))
            check("... und haengt nicht", time.monotonic() - started < 10.0)
        finally:
            mock.SimulatedRTB2000.hang_after_run = False
    finally:
        scope.close()


# -- ScopeService (wie Netzwerk/MCP) -----------------------------------------------


def check_service() -> None:
    print(f"ScopeService mit {driver.SIM_DEVICE_ID} (Weg von Netzwerk/MCP)")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from scope_service import ScopeService
    from unit_mso1000hd.driver import register_resource

    tmp = tempfile.TemporaryDirectory()
    service = ScopeService(Path(tmp.name) / "captures")
    sid = driver.SIM_DEVICE_ID
    register_resource(sid, driver.SIM_RESOURCE)   # macht sonst der DeviceWorker im Simulationsmodus
    try:
        expect_error("Vor der Meldung: device_offline", "device_offline", lambda: service.capabilities(sid))
        service.on_scope_connected(sid, True)
        service.on_scope_state(sid, "free", mock.SIM_MODEL, mock.SIM_SERIAL)
        caps = service.capabilities(sid)
        check("Faehigkeiten: RTB2002 (Simulation), 2 Kanaele",
              caps["model"] == "RTB2002 (Simulation)" and len(caps["channels"]) == 2, caps["model"])
        report = service.acquire(sid, {
            "channels": [{"name": "ch1", "range_v": 5}],
            "timing": {"duration_s": 0.005, "pre_trigger_pct": 50},
            "trigger": {"mode": "single", "source": "CH1", "level_v": 1.65},
            "measurements": ["frequency", "vpp"],
        })
        ch1 = report["channels"]["CH1"]
        check("acquire: Bericht mit Kennwerten", near(ch1["frequency"], 1000.0, 0.01) and near(ch1["vpp"], 3.3, 0.05),
              str(ch1))
        check("Verbindung offen (connected)", service.describe(sid)["state"] == "connected")
        check("Faehigkeiten jetzt vom offenen Geraet", service.capabilities(sid)["bandwidth_hz"] == 70e6)
        again = service.measure(report["capture_id"], {"measurements": ["duty"]})
        check("measure auf gespeicherter Erfassung", near(again["channels"]["CH1"]["duty"], 50.0, abs_=1.0),
              str(again["channels"]["CH1"]))
        check("release trennt", service.release(sid) == {"released": True}
              and service.describe(sid)["state"] == "idle")
        service.on_scope_connected(sid, False)
        expect_error("Offline: device_offline", "device_offline",
                     lambda: service.acquire(sid, {"channels": [{"name": "CH1", "range_v": 5}],
                                                   "timing": {"duration_s": 0.001}}))
    finally:
        service.shutdown()
        tmp.cleanup()


# -- Hardware -------------------------------------------------------------------------


def check_hardware(address: str, signal: str | None) -> None:
    from unit_mso1000hd.driver import normalize_resource
    resource = normalize_resource(address)
    print(f"Hardware: {resource} (Erkennung und Daten ueber {driver.socket_resource(resource) or resource})")
    try:
        info = scpi.probe(resource, timeout_s=3.0)
    except scpi.ScpiProbeError as exc:
        check("Geraet erreichbar (*IDN?)", False, str(exc))
        return
    check("Geraet erreichbar (*IDN?)", True)
    check("Als R&S RTB2000 erkannt", info.family == scpi.FAMILY_RS_RTB, f"({info.vendor} {info.model})")
    print(f"          {info.vendor} {info.model}, Seriennummer {info.serial}, Firmware {info.firmware}")
    if info.family != scpi.FAMILY_RS_RTB:
        return
    scope = open_rtb(resource)
    try:
        caps = scope.capabilities()
        print(f"          Optionen {scope._options!r} -> Bandbreite {caps.bandwidth_hz / 1e6:g} MHz,"
              f" {scope._h_divs} horizontale Teilungen")
        dev = scope._device
        started = time.monotonic()
        cap = scope.acquire(request(trigger={"mode": "none"}, timing={"duration_s": 0.01}))
        took = time.monotonic() - started
        print(f"          frei erfasst: {cap.samples} Samples a {cap.sample_interval_s:g} s in {took:.1f} s,"
              f" Warnungen {cap.warnings}")
        print(f"          ACQuire:STATe? danach: {dev.acquisition_state()!r}, "
              f"Vollausschlag CH1 {cap.channels['CH1'].range_v:g} V "
              f"(RANGe {dev.channel_range(1):g} V / SCALe {dev.channel_scale(1):g} V/div)")
        check("Freie Erfassung CH1 liefert Daten", cap.samples > 1000 and cap.triggered is None)
        check("Laufzeit unter 30 s", took < 30.0, f"({took:.1f} s)")
        if signal:
            kind, f_text, high_text = signal.split(":")
            if kind != "rechteck":
                raise SystemExit(f"--signal-ch1: nur 'rechteck:<f_hz>:<high_v>' (nicht {kind!r})")
            f_hz, high = float(f_text), float(high_text)
            cap = scope.acquire(request(
                channels=[{"name": "CH1", "range_v": high * 1.5}],
                timing={"duration_s": 5.0 / f_hz, "pre_trigger_pct": 20},
                trigger={"mode": "single", "source": "CH1", "level_v": high / 2, "timeout_s": 2}))
            values = measure(cap, "CH1", ["frequency", "vtop", "vbase"])
            print(f"          {values}, Warnungen {cap.warnings}")
            check(f"Rechteck {f_hz:g} Hz", near(values["frequency"], f_hz, 0.01), str(values["frequency"]))
            check(f"Pegel {high:g} V", near(values["vtop"], high, 0.05), str(values["vtop"]))
            check("Trigger-Index bei 20 % (Vorzeichen TIMebase:POSition)",
                  near(cap.trigger_index, 0.2 * cap.samples, 0.03), f"{cap.trigger_index} von {cap.samples}")
            off = edge_offset(cap, "CH1", high / 2, True)
            print(f"          Flanke {off:+.1f} Punkte vom Trigger-Index ({dev.acquisition_points()} Punkte)")
            check("Steigende Flanke am Trigger-Index (+-10 Punkte)", abs(off) <= 10, f"({off:+.1f} Punkte)")
            cap = scope.acquire(request(
                channels=[{"name": "CH1", "range_v": high / 4, "offset_v": high}],
                timing={"duration_s": 5.0 / f_hz}, trigger={"mode": "none"}))
            top = measure(cap, "CH1", ["vmax"])["vmax"]
            check("Offset: Bereichsmitte bei +high (Vorzeichen CHANnel:OFFSet)", near(top, high, 0.05), str(top))
    finally:
        scope.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--address", help="IP-Adresse oder VISA-Ressource des Oszilloskops")
    parser.add_argument("--signal-ch1", help="rechteck:<f_hz>:<high_v>")
    args = parser.parse_args()
    if args.hardware and not args.address:
        parser.error("--hardware braucht --address")

    check_pure()
    check_sim()
    check_service()
    if args.hardware:
        check_hardware(args.address, args.signal_ch1)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} Fehler:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("Alles ok." + ("" if args.hardware else " (nur Simulator, keine Hardware geprueft)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
