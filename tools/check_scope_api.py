"""Pruefskript fuer scope_api (Auswertung, Anfrage, 2204A-Adapter, Speicher).

Ohne Argument: nur gegen den Mock (picoscope2000/mock.py mit synthetischen
Signalen) und gegen rechnerisch bekannte Signale -- keine Hardware noetig.

Mit --hardware: zusaetzlich am echten PicoScope 2204A. Voraussetzungen:
Scope per USB angeschlossen, PicoScope-7-App GESCHLOSSEN (exklusiver Zugriff),
LabControl darf das Scope gerade nicht halten. Die Hardware-Pruefung stellt nur
Erfassungen ein und liest -- das Scope hat keinen Ausgang, der geschaltet wird.
Mit --signal-a "rechteck:<f_hz>:<high_v>" wird ein bekanntes Signal an Kanal A
erwartet und Frequenz/Pegel geprueft (z.B. vom Funktionsgenerator).

Aufruf:  python tools/check_scope_api.py [--hardware [--signal-a rechteck:1000:3.3]]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from picoscope2000 import mock as pico_mock  # noqa: E402
from scope_api import analysis  # noqa: E402
from scope_api.base import AcquireRequest, ScopeError  # noqa: E402
from scope_api.pico2000_adapter import open_pico2000  # noqa: E402
from scope_api.store import CaptureStore  # noqa: E402

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
        "channels": [{"name": "A", "range_v": 5}, {"name": "B", "range_v": 2}],
        "timing": {"duration_s": 0.01, "pre_trigger_pct": 20},
        "trigger": {"mode": "single", "source": "A", "level_v": 1.65, "edge": "rising", "timeout_s": 1},
    }
    data.update(overrides)
    return AcquireRequest.from_dict(data)


# -- Auswertung gegen bekannte Signale ---------------------------------------------


def check_analysis() -> None:
    print("Auswertung (bekannte Signale)")
    dt = 1e-6
    t = np.arange(10_000) * dt
    square = pico_mock.SquareWave(frequency_hz=1_000.0, low_v=0.0, high_v=3.3, duty=0.3, edge_s=20e-6)
    values, reasons = analysis.measure(t, square(t), list(analysis.MEASUREMENTS))
    check("Rechteck Frequenz 1 kHz", near(values["frequency"], 1000.0, 0.001), str(values["frequency"]))
    check("Rechteck Tastgrad 30 %", near(values["duty"], 30.0, abs_=0.5), str(values["duty"]))
    check("Rechteck Pegel 0 / 3,3 V", near(values["vbase"], 0.0, abs_=1e-6) and near(values["vtop"], 3.3, 0.001))
    check("Rechteck Anstiegszeit 10-90 % = 16 us", near(values["rise_time"], 16e-6, 0.05), str(values["rise_time"]))
    check("Rechteck Abfallzeit 10-90 % = 16 us", near(values["fall_time"], 16e-6, 0.05), str(values["fall_time"]))
    check("Rechteck 20 Flanken", values["edge_count"] == 20, str(values["edge_count"]))
    coarse = np.arange(1000) * 10e-6
    c_values, c_reasons = analysis.measure(coarse, pico_mock.SquareWave(edge_s=50e-9)(coarse),
                                           ["rise_time", "frequency"])
    check("Flanke unter der Aufloesung: keine Anstiegszeit, Hinweis", c_values["rise_time"] is None
          and "Auflösung" in c_reasons.get("rise_time", "") and near(c_values["frequency"], 1000.0, 0.01))
    check("Rechteck ohne Ueberschwingen", near(values["overshoot"], 0.0, abs_=0.1), str(values["overshoot"]))

    sine = pico_mock.SineWave(frequency_hz=2_500.0, amplitude_v=1.0, offset_v=0.5)
    values, _ = analysis.measure(t, sine(t), ["frequency", "vrms", "vmean", "vpp"])
    check("Sinus Frequenz 2,5 kHz", near(values["frequency"], 2500.0, 0.001), str(values["frequency"]))
    check("Sinus Effektivwert", near(values["vrms"], math.sqrt(0.5 ** 2 + 0.5), 0.01), str(values["vrms"]))
    check("Sinus Mittelwert 0,5 V", near(values["vmean"], 0.5, abs_=0.01), str(values["vmean"]))

    rng = np.random.default_rng(1)
    noisy = square(t) + rng.normal(0, 0.03, t.size)
    values, _ = analysis.measure(t, noisy, ["frequency", "duty", "edge_count"])
    check("Rechteck mit Rauschen: Tastgrad", near(values["duty"], 30.0, abs_=1.0), str(values["duty"]))
    slow = pico_mock.SineWave(frequency_hz=200.0, amplitude_v=0.5)(t) + rng.normal(0, 0.02, t.size)
    slow_values, _ = analysis.measure(t, slow, ["frequency", "edge_count"])
    check("Langsamer Sinus mit starkem Rauschen: genau 4 Flanken", slow_values["edge_count"] == 4,
          str(slow_values["edge_count"]))
    check("Rechteck mit Rauschen: keine Mehrfachflanken", values["edge_count"] == 20, str(values["edge_count"]))
    check("Rechteck mit Rauschen: Frequenz", near(values["frequency"], 1000.0, 0.002), str(values["frequency"]))

    values, reasons = analysis.measure(t, np.full(t.size, 1.2), ["frequency", "duty", "edge_count", "vmean"])
    check("Gleichspannung: keine Frequenz, mit Grund", values["frequency"] is None and "frequency" in reasons)
    check("Gleichspannung: 0 Flanken, Mittelwert", values["edge_count"] == 0 and near(values["vmean"], 1.2))

    small = 0.02 * np.sin(2 * np.pi * 3e3 * t) + rng.normal(0, 0.002, t.size)
    values, reasons = analysis.measure(t, small, ["frequency", "edge_count"], min_amplitude=analysis.noise_floor(5.0))
    check("Unter Rauschschwelle (5-V-Bereich): keine Frequenz, Grund nennt Schwelle",
          values["frequency"] is None and values["edge_count"] == 0 and "Rauschschwelle" in reasons["frequency"])
    values, _ = analysis.measure(t, small, ["frequency"], min_amplitude=analysis.noise_floor(0.05))
    check("Gleiches Signal im 50-mV-Bereich wird ausgewertet", near(values["frequency"], 3000.0, 0.01),
          str(values["frequency"]))

    values, _ = analysis.measure(t, square(t), ["frequency", "edge_count"], t_start=0.0, t_stop=2.5e-3)
    check("Zeitfenster begrenzt die Auswertung (3 steigend + 3 fallend)", values["edge_count"] == 6, str(values["edge_count"]))

    expect_error("Unbekannter Kennwert abgelehnt", "unknown_measurement",
                 lambda: analysis.measure(t, square(t), ["thd"]))

    spike = np.zeros(100_000)
    spike[54_321] = 7.0
    env = analysis.envelope(np.arange(spike.size) * 1e-6, spike, 200)
    check("Huellkurve: 200 Punkte", len(env["t"]) == 200)
    check("Huellkurve behaelt Einzelspitze", max(env["max"]) == 7.0)
    dec = analysis.decimate(t, square(t), 100, "mean")
    check("Verdichtung Mittel: 100 Punkte", dec["mode"] == "mean" and len(dec["v"]) == 100)
    check("Kurzes Signal bleibt roh", analysis.decimate(t[:50], square(t[:50]), 100)["mode"] == "raw")


# -- Anfrage -----------------------------------------------------------------------


def check_request() -> None:
    print("Anfrage (Form und Typen)")
    base = {"channels": [{"name": "a", "range_v": 5}], "timing": {"duration_s": 0.001}}
    req = AcquireRequest.from_dict(base)
    check("Kanalname gross, Trigger-Quelle = erster Kanal", req.channels[0].name == "A" and req.trigger.source == "A")
    check("Standard: auto-Trigger, DC, Tastkopf 1", req.trigger.mode == "auto" and req.channels[0].coupling == "DC"
          and req.channels[0].probe == 1.0)
    expect_error("Ohne Kanaele", "invalid_request", lambda: AcquireRequest.from_dict({**base, "channels": []}))
    expect_error("Dauer UND Intervall", "invalid_request", lambda: AcquireRequest.from_dict(
        {**base, "timing": {"duration_s": 1e-3, "sample_interval_s": 1e-6}}))
    expect_error("Weder Dauer noch Intervall", "invalid_request",
                 lambda: AcquireRequest.from_dict({**base, "timing": {}}))
    expect_error("Trigger auf nicht erfasstem Kanal", "invalid_request",
                 lambda: AcquireRequest.from_dict({**base, "trigger": {"source": "B"}}))
    expect_error("Kopplung unbekannt", "invalid_request", lambda: AcquireRequest.from_dict(
        {**base, "channels": [{"name": "A", "range_v": 5, "coupling": "GND"}]}))
    expect_error("Trigger-Timeout > 30 s", "invalid_request",
                 lambda: AcquireRequest.from_dict({**base, "trigger": {"timeout_s": 60}}))
    expect_error("Bereich negativ", "invalid_request", lambda: AcquireRequest.from_dict(
        {**base, "channels": [{"name": "A", "range_v": -1}]}))
    expect_error("Bool statt Zahl", "invalid_request", lambda: AcquireRequest.from_dict(
        {**base, "channels": [{"name": "A", "range_v": True}]}))
    expect_error("Pre-Trigger > 100 %", "invalid_request", lambda: AcquireRequest.from_dict(
        {**base, "timing": {"duration_s": 1e-3, "pre_trigger_pct": 150}}))
    check("Rundreise to_dict/from_dict", AcquireRequest.from_dict(req.to_dict()).to_dict() == req.to_dict())


# -- Adapter mit Mock --------------------------------------------------------------


def check_adapter_mock() -> None:
    print("2204A-Adapter mit Mock")
    pico_mock.MockPicoScope2000.signals = pico_mock.default_signals()
    scope = open_pico2000(simulate=True)
    caps = scope.capabilities().to_dict()
    check("Faehigkeiten: 2 Kanaele, 9 Bereiche", len(caps["channels"]) == 2
          and len(caps["channels"][0]["ranges_v"]) == 9)
    check("scope_id aus Seriennummer", scope.scope_id == "scope:" + pico_mock.SIM_SERIAL)

    cap = scope.acquire(request())
    n = cap.samples
    check("10 ms mit 2 Kanaelen -> 2,56 us, 3907 Samples",
          near(cap.sample_interval_s, 2.56e-6, 1e-6) and n == 3907, f"({cap.sample_interval_s}, {n})")
    check("Trigger bei 20 %", cap.trigger_index == round(n * 0.2), str(cap.trigger_index))
    a = cap.channels["A"].volts
    i = cap.trigger_index
    check("Signal kreuzt 1,65 V steigend am Trigger", a[i - 2] < 1.65 < a[i + 2], f"({a[i - 2]:.3f}, {a[i + 2]:.3f})")
    check("single -> triggered", cap.triggered is True)
    rep = analysis.report(cap)
    check("A: 1 kHz, 50 %", near(rep["channels"]["A"]["frequency"], 1000.0, 0.002)
          and near(rep["channels"]["A"]["duty"], 50.0, abs_=1.0),
          f"({rep['channels']['A']['frequency']}, {rep['channels']['A']['duty']})")
    check("B: Sinus 2 Vss, nicht uebersteuert", near(rep["channels"]["B"]["vpp"], 2.0, 0.05)
          and not rep["channels"]["B"]["overrange"])
    check("Bericht ist JSON und klein", len(json.dumps(rep)) < 25_000, f"({len(json.dumps(rep))} Bytes)")
    check("Tatsaechliche Bereiche 5 V / 2 V", rep["actual"]["channels"]["A"]["range_v"] == 5.0
          and rep["actual"]["channels"]["B"]["range_v"] == 2.0)

    cap = scope.acquire(request(channels=[{"name": "A", "range_v": 3}, {"name": "B", "range_v": 0.5}]))
    rep = analysis.report(cap)
    check("A 3 V -> naechster Bereich 5 V", cap.channels["A"].range_v == 5.0)
    check("B in 0,5 V uebersteuert + Warnung", cap.channels["B"].overrange
          and any("B übersteuert" in w for w in rep["warnings"]))

    cap = scope.acquire(request(channels=[{"name": "A", "range_v": 50, "probe": 10}],
                                trigger={"mode": "single", "source": "A", "level_v": 16.5}))
    values, _ = analysis.measure(cap.time_s, cap.channels["A"].volts, ["vtop"])
    check("Tastkopf 10:1: Bereich 5 V am Eingang, Werte x10", cap.channels["A"].range_v == 50.0
          and near(values["vtop"], 33.0, 0.01), str(values["vtop"]))

    cap = scope.acquire(request(trigger={"mode": "single", "source": "A", "level_v": 1.65, "edge": "falling"}))
    a, i = cap.channels["A"].volts, cap.trigger_index
    check("Fallende Flanke am Trigger", a[i - 2] > 1.65 > a[i + 2])

    cap = scope.acquire(request(channels=[{"name": "A", "range_v": 5}], timing={"sample_interval_s": 10e-9},
                                trigger={"mode": "none"}))
    check("1 Kanal: 10 ns moeglich, voller Speicher", near(cap.sample_interval_s, 10e-9, 1e-6)
          and cap.samples == 8000 and cap.triggered is None)
    expect_error("2 Kanaele: 10 ns nicht moeglich", "setting_not_supported",
                 lambda: scope.acquire(request(timing={"sample_interval_s": 10e-9})))
    expect_error("1000 s passen nicht in den Speicher", "duration_too_long",
                 lambda: scope.acquire(request(timing={"duration_s": 1000})))
    expect_error("50 V ohne Tastkopf", "setting_not_supported",
                 lambda: scope.acquire(request(channels=[{"name": "A", "range_v": 50}])))
    expect_error("Offset nicht unterstuetzt", "setting_not_supported",
                 lambda: scope.acquire(request(channels=[{"name": "A", "range_v": 5, "offset_v": 1}])))
    expect_error("Kanal C gibt es nicht", "setting_not_supported",
                 lambda: scope.acquire(request(channels=[{"name": "C", "range_v": 5}], trigger={"mode": "none"})))
    expect_error("Trigger-Pegel ausserhalb des Bereichs", "level_out_of_range",
                 lambda: scope.acquire(request(trigger={"mode": "single", "source": "A", "level_v": 6})))

    pico_mock.MockPicoScope2000.signals = {"A": pico_mock.Constant(0.2), "B": pico_mock.Constant(0.0)}
    expect_error("single ohne Flanke -> trigger_timeout", "trigger_timeout", lambda: scope.acquire(request()))
    cap = scope.acquire(request(trigger={"mode": "auto", "source": "A", "level_v": 1.65}))
    check("auto ohne Flanke: erfasst, triggered=False, Warnung", cap.triggered is False
          and any("Kein Trigger" in w for w in cap.warnings))
    pico_mock.MockPicoScope2000.signals = pico_mock.default_signals()
    scope.close()


# -- Speicher ----------------------------------------------------------------------


def check_store() -> None:
    print("Erfassungsspeicher")
    pico_mock.MockPicoScope2000.signals = pico_mock.default_signals()
    scope = open_pico2000(simulate=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = CaptureStore(Path(tmp) / "captures", capacity=3)
        cap = scope.acquire(request())
        cid = store.add(cap)
        check("ID-Format c-JJJJMMTT-HHMMSS-NN", cid.startswith("c-") and len(cid) == 20, cid)
        csv = Path(cap.csv_path)
        check("CSV geschrieben", csv.is_file())
        data = np.loadtxt(csv, delimiter=",", comments="#", skiprows=4)
        check("CSV: Zeit + 2 Kanaele, alle Samples", data.shape == (cap.samples, 3), str(data.shape))
        check("CSV: t=0 am Trigger-Index", abs(data[cap.trigger_index, 0]) < 1e-12)
        check("CSV-Kopf nennt Kanaele", "time_s,A_V,B_V" in csv.read_text(encoding="utf-8").splitlines()[3])

        ex = store.excerpt(cid, "a", max_points=200)
        check("Ausschnitt: min/max, <= 200 Punkte", ex["channels"]["A"]["mode"] == "minmax"
              and len(ex["channels"]["A"]["t"]) <= 200)
        ex = store.excerpt(cid, None, t_start=0.0, t_stop=1e-4, max_points=500)
        check("Ausschnitt 0..100 us roh", ex["channels"]["A"]["mode"] == "raw" and ex["samples_in_window"] < 500)
        m = store.measure(cid, ["frequency"], channel="A", t_start=0.0)
        check("Neu auswerten im Fenster", near(m["channels"]["A"]["frequency"], 1000.0, 0.005))
        expect_error("Kanal C nicht in Erfassung", "invalid_request", lambda: store.excerpt(cid, "C"))
        expect_error("Unbekannte ID", "unknown_capture", lambda: store.get("c-00000000-000000-00"))
        for _ in range(3):
            store.add(scope.acquire(request(trigger={"mode": "none"})))
        expect_error("Aelteste faellt bei Kapazitaet 3 heraus", "unknown_capture", lambda: store.get(cid))
        check("CSV bleibt nach Verdraengung", csv.is_file())
    scope.close()


# -- Hardware ----------------------------------------------------------------------


def check_hardware(signal_a: str | None) -> None:
    print("Echtes PicoScope 2204A (PicoScope 7 muss geschlossen sein)")
    try:
        scope = open_pico2000(simulate=False)
    except ScopeError as exc:
        check("Scope oeffnen", False, f"({exc.message})")
        return
    try:
        caps = scope.capabilities()
        print(f"          {caps.model}, Serial {caps.serial}")
        check("Oeffnen + Faehigkeiten", caps.serial != "")

        cap = scope.acquire(AcquireRequest.from_dict({
            "channels": [{"name": "A", "range_v": 5}, {"name": "B", "range_v": 5}],
            "timing": {"duration_s": 0.001}, "trigger": {"mode": "none"}}))
        rep = analysis.report(cap)
        check("Freilaufend, 2 Kanaele, 1 ms", cap.samples >= 2 and set(cap.channels) == {"A", "B"},
              f"({cap.samples} Samples, {cap.sample_interval_s:g} s)")
        print(f"          {cap.samples} Samples, Intervall {cap.sample_interval_s:g} s")
        for name in ("A", "B"):
            c = rep["channels"][name]
            print(f"          {name}: vmin {c['vmin']} V, vmax {c['vmax']} V, vmean {c['vmean']} V, "
                  f"Frequenz {c['frequency']}, uebersteuert {c['overrange']}")
            if not c["overrange"] and c["vpp"] < 0.1:
                check(f"{name} offen: Rauschen ergibt keine Frequenz", c["frequency"] is None, str(c["frequency"]))

        cap = scope.acquire(AcquireRequest.from_dict({
            "channels": [{"name": "A", "range_v": 5}], "timing": {"sample_interval_s": 10e-9},
            "trigger": {"mode": "none"}}))
        check("1 Kanal mit 10 ns", near(cap.sample_interval_s, 10e-9, 1e-6), f"({cap.sample_interval_s:g} s, "
              f"{cap.samples} Samples)")

        if signal_a:
            kind, f_hz, high = signal_a.split(":")
            f_hz, high = float(f_hz), float(high)
            req = AcquireRequest.from_dict({
                "channels": [{"name": "A", "range_v": high * 1.3}],
                "timing": {"duration_s": 10 / f_hz, "pre_trigger_pct": 20},
                "trigger": {"mode": "single", "source": "A", "level_v": high / 2, "timeout_s": 2}})
            cap = scope.acquire(req)
            a, i = cap.channels["A"].volts, cap.trigger_index
            values, _ = analysis.measure(cap.time_s, a, ["frequency", "duty", "vtop", "vbase", "rise_time"])
            print(f"          A: {values}")
            check("Trigger single steigend am erwarteten Index", a[max(0, i - 3)] < high / 2 < a[min(a.size - 1, i + 3)])
            check(f"Frequenz {f_hz:g} Hz (+-1 %)", near(values["frequency"], f_hz, 0.01), str(values["frequency"]))
            check(f"Oberer Pegel {high:g} V (+-5 %)", near(values["vtop"], high, 0.05), str(values["vtop"]))
            check("Anstiegszeit bei grobem Intervall gesperrt", values["rise_time"] is None, str(values["rise_time"]))
            cap = scope.acquire(AcquireRequest.from_dict({
                "channels": [{"name": "A", "range_v": high * 1.3}],
                "timing": {"sample_interval_s": 10e-9, "samples": 2000, "pre_trigger_pct": 50},
                "trigger": {"mode": "single", "source": "A", "level_v": high / 2, "timeout_s": 2}}))
            values, reasons = analysis.measure(cap.time_s, cap.channels["A"].volts,
                                               ["rise_time", "overshoot", "vtop"])
            print(f"          A mit 10 ns: {values} {reasons}")
            # Am 2204A mit JDS2915 gemessen: Flanke ~2,2 Samples (~22 ns) bei 10 ns, also genau an der
            # Aufloesungsgrenze -- je nach Lage zwischen den Samples Wert oder Hinweis, beides ist richtig.
            check("Anstiegszeit mit 10 ns: < 40 ns oder Aufloesungshinweis",
                  (values["rise_time"] is not None and values["rise_time"] < 40e-9)
                  or "Auflösung" in reasons.get("rise_time", ""), str(values["rise_time"]))
            check("Trigger bei 50 % Pre-Trigger in der Mitte", cap.trigger_index == 1000
                  and cap.channels["A"].volts[997] < high / 2 < cap.channels["A"].volts[1003])
        else:
            try:
                cap = scope.acquire(AcquireRequest.from_dict({
                    "channels": [{"name": "A", "range_v": 5}], "timing": {"duration_s": 0.001},
                    "trigger": {"mode": "single", "source": "A", "level_v": 4.0, "timeout_s": 0.5}}))
                print(f"          single-Trigger bei 4 V ausgeloest (Signal an A vorhanden)")
            except ScopeError as exc:
                check("single ohne Signal -> trigger_timeout", exc.code == "trigger_timeout", exc.code)
            cap = scope.acquire(AcquireRequest.from_dict({
                "channels": [{"name": "A", "range_v": 5}], "timing": {"duration_s": 0.001},
                "trigger": {"mode": "auto", "source": "A", "level_v": 4.0, "timeout_s": 0.2}}))
            check("auto erfasst auch ohne Trigger", cap.samples > 0, f"(triggered={cap.triggered})")
    finally:
        scope.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--signal-a", default=None, help="rechteck:<f_hz>:<high_v> an Kanal A")
    args = parser.parse_args()
    check_analysis()
    check_request()
    check_adapter_mock()
    check_store()
    if args.hardware:
        check_hardware(args.signal_a)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FEHLER:")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
