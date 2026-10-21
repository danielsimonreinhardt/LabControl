"""Pruefskript fuer das UNI-T MSO1000HD (Treiber, scope_api-Adapter, ScopeService).

Ohne Argument: nur gegen den Simulator (device-driver/unit_mso1000hd/mock.py,
SCPI-Ebene -- der echte Treiber- und Adaptercode laeuft mit). Keine Hardware.

Mit --hardware --address <IP>: zusaetzlich am echten Geraet ueber LAN
(VXI-11, pyvisa-py). Voraussetzungen: Oszilloskop im Netz mit fester IP,
LabControl haelt es gerade nicht (Kachel nicht "Netzwerk/MCP"). Die Pruefung
stellt nur Erfassungen ein und liest -- das Geraet hat keinen Ausgang. Es
bleibt danach gestoppt mit der letzten Erfassung auf dem Bildschirm.
Mit --signal-ch1 "rechteck:<f_hz>:<high_v>" wird an CH1 ein bekanntes Signal
erwartet (z.B. der Kalibrierausgang oder der Funktionsgenerator) und
Frequenz/Pegel geprueft.

Aufruf:  python tools/check_unit_mso.py [--hardware --address 192.168.1.50 [--signal-ch1 rechteck:1000:3]]
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))  # Geraete-Treiber
sys.path.insert(0, str(ROOT / "lab_gui"))        # scope_service (flache Importe wie in der App)

from scope_api import analysis  # noqa: E402
from scope_api.base import AcquireRequest, ScopeError  # noqa: E402
from scope_api.unit_mso_adapter import capabilities_for, open_unit_mso  # noqa: E402
from unit_mso1000hd import driver, mock  # noqa: E402

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


def measure(capture, name: str, items: list[str]) -> dict:
    values, _reasons = analysis.measure(capture.time_s, capture.channels[name].volts, items)
    return values


# -- reine Funktionen ------------------------------------------------------------


def check_pure() -> None:
    print("Treiber: Modell, Adressen, Geraete-IDs")
    m = driver.parse_model("MSO1154HD-S")
    check("MSO1154HD-S: 4 Kanaele, 150 MHz", m.channels == 4 and m.bandwidth_hz == 150e6)
    m = driver.parse_model("MSO1252HD")
    check("MSO1252HD: 2 Kanaele, 250 MHz", m.channels == 2 and m.bandwidth_hz == 250e6)
    check("Unbekanntes Modell: 4 Kanaele", driver.parse_model("XYZ").channels == 4)
    check("IP -> VXI-11-Ressource",
          driver.normalize_resource(" 192.168.1.50 ") == "TCPIP0::192.168.1.50::inst0::INSTR")
    check("VISA-Adresse bleibt", driver.normalize_resource("USB0::0x5345::0x1234::ABC::INSTR")
          == "USB0::0x5345::0x1234::ABC::INSTR")
    check("sim -> SIM", driver.normalize_resource("sim") == driver.SIM_RESOURCE)
    check("Geraete-ID LAN = Adresse",
          driver.device_id_for("TCPIP0::192.168.1.50::inst0::INSTR") == "scpiscope:192.168.1.50")
    check("Geraete-ID USB = Seriennummer",
          driver.device_id_for("USB0::0x5345::0x1234::ABC::INSTR") == "scpiscope:ABC")
    check("Geraete-ID Simulation", driver.device_id_for("SIM") == driver.SIM_DEVICE_ID)
    check("Adresspruefung", driver.is_valid_address("192.168.1.50") and driver.is_valid_address("scope-lab")
          and not driver.is_valid_address("") and not driver.is_valid_address("a b"))
    caps = capabilities_for("MSO1154HD-S", "X", simulated=True).to_dict()
    names = [c["name"] for c in caps["channels"]]
    check("Faehigkeiten: CH1..CH4", names == ["CH1", "CH2", "CH3", "CH4"], str(names))


# -- Simulator: Treiber + Adapter ----------------------------------------------------


def check_sim() -> None:
    print("Simulator: Erfassungen ueber Treiber und Adapter")
    mock.SimulatedInstrument.signals = mock.default_signals()
    mock.SimulatedInstrument.hang_after_run = False
    scope = open_unit_mso(driver.SIM_RESOURCE)
    try:
        cap = scope.acquire(request())
        values = measure(cap, "CH1", ["frequency", "duty", "vtop", "vbase"])
        check("Einzelerfassung: getriggert", cap.triggered is True)
        check("CH1 Rechteck 1 kHz", near(values["frequency"], 1000.0, 0.01), str(values["frequency"]))
        check("CH1 Tastgrad 50 %", near(values["duty"], 50.0, abs_=1.0), str(values["duty"]))
        check("CH1 Pegel 0 / 3,3 V", near(values["vtop"], 3.3, 0.03) and near(values["vbase"], 0.0, abs_=0.05),
              f"{values['vtop']} / {values['vbase']}")
        check("Pre-Trigger 20 %: Trigger-Index bei 20 % der Punkte",
              near(cap.trigger_index, 0.2 * cap.samples, 0.02), f"{cap.trigger_index} von {cap.samples}")
        v = cap.channels["CH1"].volts
        i = cap.trigger_index
        check("Steigende Flanke am Trigger-Index", v[max(0, i - 5)] < 1.65 < v[min(v.size - 1, i + 50)])
        check("Bereich zurueckgelesen: 5 V -> 2 V/div = +-8 V", near(cap.channels["CH1"].range_v, 8.0))
        check("Nicht uebersteuert", cap.channels["CH1"].overrange is False)

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

        cap = scope.acquire(request(
            channels=[{"name": "CH3", "range_v": 0.1, "offset_v": 12.0}],
            timing={"duration_s": 50e-6}, trigger={"mode": "none"}))
        values = measure(cap, "CH3", ["vmean", "vpp", "frequency"])
        check("Offset 12 V, Bereich 100 mV: Welligkeit sichtbar",
              near(values["vmean"], 12.0, abs_=0.005) and near(values["vpp"], 0.05, 0.2), str(values))
        check("Welligkeit 100 kHz", near(values["frequency"], 100e3, 0.02), str(values["frequency"]))
        check("Ohne Trigger: triggered None", cap.triggered is None)

        cap = scope.acquire(request(
            channels=[{"name": "CH4", "range_v": 1}],
            trigger={"mode": "auto", "source": "CH4", "level_v": 0.5, "timeout_s": 0.1}))
        check("Auto ohne Flanke: erzwungen, Warnung",
              cap.triggered is False and any("Kein Trigger" in w for w in cap.warnings), str(cap.warnings))

        cap = scope.acquire(request(timing={"sample_interval_s": 1e-6, "samples": 25_000}, trigger={"mode": "none"}))
        check("Abtastintervall-Anfrage: hoechstens 1 us", cap.sample_interval_s <= 1e-6 * (1 + 1e-6),
              str(cap.sample_interval_s))

        expect_error("Einzeltrigger ohne Flanke: trigger_timeout", "trigger_timeout", lambda: scope.acquire(request(
            channels=[{"name": "CH4", "range_v": 1}],
            trigger={"mode": "single", "source": "CH4", "level_v": 0.5, "timeout_s": 0.1})))
        expect_error("CH5 gibt es nicht", "setting_not_supported",
                     lambda: scope.acquire(request(channels=[{"name": "CH5", "range_v": 1}], trigger={"mode": "none"})))
        expect_error("Bereich zu gross", "setting_not_supported",
                     lambda: scope.acquire(request(channels=[{"name": "CH1", "range_v": 500}])))
        expect_error("Trigger-Pegel ausserhalb des Bereichs", "level_out_of_range",
                     lambda: scope.acquire(request(channels=[{"name": "CH1", "range_v": 0.5}],
                                                   trigger={"mode": "single", "source": "CH1", "level_v": 3})))
        expect_error("Zu viele Samples", "setting_not_supported",
                     lambda: scope.acquire(request(timing={"duration_s": 1.0, "samples": 10_000_000})))

        mock.SimulatedInstrument.hang_after_run = True
        try:
            started = time.monotonic()
            expect_error("Geraet wird nicht fertig: device_error", "device_error",
                         lambda: scope.acquire(request(trigger={"mode": "none"}, timing={"duration_s": 1e-3})))
            check("... und haengt nicht", time.monotonic() - started < 10.0)
        finally:
            mock.SimulatedInstrument.hang_after_run = False
    finally:
        scope.close()


# -- ScopeService (wie Netzwerk/MCP) -----------------------------------------------


def check_service() -> None:
    print("ScopeService mit scpiscope:SIM (Weg von Netzwerk/MCP)")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from scope_service import ScopeService

    tmp = tempfile.TemporaryDirectory()
    service = ScopeService(Path(tmp.name) / "captures")
    sid = driver.SIM_DEVICE_ID
    try:
        expect_error("Vor der Meldung: device_offline", "device_offline", lambda: service.capabilities(sid))
        service.on_scope_connected(sid, True)
        service.on_scope_state(sid, "free", mock.SIM_MODEL, mock.SIM_SERIAL)
        caps = service.capabilities(sid)
        check("Faehigkeiten: Modell und 4 Kanaele",
              caps["model"].startswith("MSO1154HD") and len(caps["channels"]) == 4, caps["model"])
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
    resource = driver.normalize_resource(address)
    print(f"Hardware: {resource}")
    try:
        info = driver.probe(resource, timeout_s=3.0)
    except driver.MSO1000HDError as exc:
        check("Geraet erreichbar (*IDN?)", False, str(exc))
        return
    check("Geraet erreichbar (*IDN?)", True)
    print(f"          {info.vendor} {info.model}, Seriennummer {info.serial}, Firmware {info.firmware}")
    scope = open_unit_mso(resource)
    try:
        started = time.monotonic()
        cap = scope.acquire(request(trigger={"mode": "none"}, timing={"duration_s": 0.01}))
        took = time.monotonic() - started
        print(f"          frei erfasst: {cap.samples} Samples a {cap.sample_interval_s:g} s in {took:.1f} s,"
              f" Warnungen {cap.warnings}")
        check("Freie Erfassung CH1 liefert Daten", cap.samples > 1000 and cap.triggered is None)
        check("Laufzeit unter 30 s (ASCII ueber LAN)", took < 30.0, f"({took:.1f} s)")
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
            print(f"          {values}")
            check(f"Rechteck {f_hz:g} Hz", near(values["frequency"], f_hz, 0.01), str(values["frequency"]))
            check(f"Pegel {high:g} V", near(values["vtop"], high, 0.05), str(values["vtop"]))
            check("Trigger-Index bei 20 % (Vorzeichen TIMebase:OFFSet)",
                  near(cap.trigger_index, 0.2 * cap.samples, 0.03), f"{cap.trigger_index} von {cap.samples}")
            v = cap.channels["CH1"].volts
            i = cap.trigger_index
            check("Steigende Flanke am Trigger-Index", v[max(0, i - 20)] < high / 2 < v[min(v.size - 1, i + 20)])
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
