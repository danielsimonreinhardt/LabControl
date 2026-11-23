"""Prueft den Funktionsgenerator MHS-5200A (1.0.3). Ohne Argument nur gegen den Simulator
(device-driver/mhs5200a/mock.py, Ebene der seriellen Zeilen -- der echte Treiber laeuft mit), offscreen:
  pure    -- Kennung/Modell, Geraete-ID, Daempfung, Offset in Prozent, Treibermodul aus der ID
  driver  -- Befehle und Antworten, Grenzen, Arbitraer-Rueckmeldung, Ablehnungen (Puls, DC, Tastgrad, Offset),
             Reihenfolge beim Amplitudenwechsel (nie mehr als alter/neuer Wert), Handshake gegen fremde Ports
  worker  -- Erkennung am CH340-Port nach dem JDS-Handshake, eigene ID, belegter Port wird nicht erneut
             abgefragt, Ablauf-Aktionen, ALLE AUS
  model   -- statische Tastgrad-Pruefung im Ablauf-Editor (nur Rechteck)
Mit --hardware --port <Port>: zusaetzlich am echten Geraet (Werte setzen und zuruecklesen, Ausgaenge bleiben
wie vorgefunden, danach wird der vorgefundene Zustand wiederhergestellt) und Erkennung ueber den DeviceWorker.

Aufruf:  python tools/check_mhs5200a.py [pure|driver|worker|model] [--hardware --port /dev/cu.usbserial-11210]
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LABCONTROL_DATA_DIR"] = tempfile.mkdtemp(prefix="lc-mhs-check-")
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from jds66xx.driver import FunctionGeneratorError, FunctionGeneratorValueError  # noqa: E402
from mhs5200a import driver as mhs  # noqa: E402
from mhs5200a.driver import MHS5200A  # noqa: E402
from mhs5200a.mock import FakeMhsPort, MockMHS5200A  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def spin(seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return bool(until()) if until is not None else False


def refused(call, cls=FunctionGeneratorValueError) -> str:
    try:
        call()
    except cls as exc:
        return str(exc)
    return ""


# -- pure ------------------------------------------------------------------------------------------

def section_pure() -> None:
    print("pure -- Modell, ID, Daempfung, Offset")
    check("Kennung 5225A... -> MHS-5225A, 25 MHz", mhs.model_info("5225A5040000") == ("MHS-5225A", 25e6))
    check("Kennung 5206A -> 6 MHz", mhs.model_info("5206A") == ("MHS-5206A", 6e6))
    check("unbekannte Kennung -> MHS-5200A, 25 MHz", mhs.model_info("xyz") == ("MHS-5200A", 25e6))
    check("ID aus Port-ID", mhs.device_id_for("fg:usb-1-2:1.0") == "fg:mhs5200a-usb-1-2:1.0"
          and mhs.is_device_id("fg:mhs5200a-usb-1-2:1.0") and not mhs.is_device_id("fg:usb-1-2:1.0"))
    from fg_profiles import MAX_OFFSET_V, module_for_device_id
    check("Treibermodul aus der ID (auch beim Partner)", module_for_device_id("fg:mhs5200a-COM5") is mhs
          and module_for_device_id("@pi/fg:mhs5200a-usb-1") is mhs
          and module_for_device_id("fg:COM5").PROFILE == "jds66xx")
    check("Rahmen der Wertfelder umfasst den MHS", MAX_OFFSET_V >= mhs.MAX_OFFSET_V)
    check("Daempfung: bis 2 V mit -20 dB in mV, darueber in 10 mV",
          mhs.amplitude_raw(0.123) == (0, 123) and mhs.amplitude_raw(2.0) == (0, 2000)
          and mhs.amplitude_raw(2.01) == (1, 201) and mhs.amplitude_raw(15.0) == (1, 1500))
    check("Offset in Prozent der halben Amplitude", mhs.offset_percent(0.5, 2.0) == 50.0
          and mhs.offset_percent(-1.2, 2.0) == -120.0)
    check("Offset ueber 120 %: abgelehnt mit Hinweis", "120 %" in refused(lambda: mhs.offset_percent(1.0, 1.0)))


# -- driver ----------------------------------------------------------------------------------------

def section_driver() -> None:
    print("driver -- Befehle, Grenzen, Ablehnungen, Handshake")
    gen = MockMHS5200A()
    sim = gen.sim
    check("Handshake: Kennung gelesen", gen.identify() == "MHS-5225A" and gen.max_frequency_hz == 25e6)
    sim.log.clear()
    gen.set_frequency(1, 1234.56)
    check("Frequenz in 0,01 Hz", sim.log == [":s1f123456"] and gen.get_frequency(1) == 1234.56, str(sim.log))
    check("Frequenz ueber dem Modell: abgelehnt", "ausserhalb" in refused(lambda: gen.set_frequency(1, 26e6)))
    gen.set_amplitude(2, 1.5)
    check("1,5 Vss mit Daempfung (y=0, a=1500)", sim.values[2]["y"] == 0 and sim.values[2]["a"] == 1500
          and gen.get_amplitude(2) == 1.5)
    gen.set_amplitude(2, 5.0)
    check("5 Vss ohne Daempfung (y=1, a=500)", sim.values[2]["y"] == 1 and sim.values[2]["a"] == 500
          and gen.get_amplitude(2) == 5.0)
    check("Amplitude 0: abgelehnt (Stoersignal am Geraet)", "ausserhalb" in refused(lambda: gen.set_amplitude(1, 0.0)))
    gen.set_offset(2, 1.0)
    check("Offset 1 V bei 5 Vss = 40 % (o=160)", sim.values[2]["o"] == 160 and abs(gen.get_offset(2) - 1.0) < 1e-9)

    # Reihenfolge: nach jedem Schreibbefehl darf am Ausgang nie mehr als max(alt, neu) anliegen
    sim.values[2]["b"] = 1
    for old, new in ((5.0, 1.0), (1.0, 5.0), (0.2, 15.0), (15.0, 0.2), (2.5, 1.9)):
        gen.set_amplitude(2, old)
        gen.set_offset(2, min(0.08, old / 4))
        offset_v = gen.get_offset(2)
        peaks: list[float] = []
        original_write = sim.write

        def watching(data: bytes, original_write=original_write, peaks=peaks) -> int:
            n = original_write(data)
            vpp, dc = sim.output_volts(2)
            peaks.append(max(abs(dc + vpp / 2), abs(dc - vpp / 2)))
            return n
        sim.write = watching
        gen.set_amplitude(2, new)
        del sim.write
        # Offset-Aufloesung am Geraet: 1 % der halben Amplitude
        limit = max(old, new) / 2 + abs(offset_v) + max(old, new) / 200 + 0.01
        check(f"Amplitudenwechsel {old:g} -> {new:g} V: nie ueber dem groesseren Wert, Offset bleibt",
              max(peaks) <= limit and abs(gen.get_offset(2) - offset_v) <= new / 200 + 1e-9,
              f"Spitze {max(peaks):.3f} V > {limit:.3f} V oder Offset {gen.get_offset(2):g} != {offset_v:g}")
    sim.values[2]["b"] = 0

    gen.set_waveform(1, mhs.WAVE_TRIANGLE)
    check("Dreieck -> Geraet 2", sim.values[1]["w"] == 2 and gen.get_waveform(1) == mhs.WAVE_TRIANGLE)
    gen.set_waveform(1, mhs.WAVE_RAMP_DOWN)
    check("Rampe fallend -> Geraet 4", sim.values[1]["w"] == 4 and gen.get_waveform(1) == mhs.WAVE_RAMP_DOWN)
    gen.set_waveform(1, 116)
    check("Arbitraer 16: geschrieben 115, gemeldet 25 -> Code 116", ":s1w115" in sim.log
          and sim.values[1]["w"] == 25 and gen.get_waveform(1) == 116, str(sim.log[-3:]))
    check("Arbitraer-Name", mhs.waveform_name(gen.get_waveform(1)) == "Arbiträr 16")
    check("Puls abgelehnt (Rechteck mit Tastgrad)", "Rechteck" in refused(lambda: gen.set_waveform(1, 2)))
    check("DC abgelehnt", "DC" in refused(lambda: gen.set_waveform(1, 6)))
    gen.set_waveform(1, mhs.WAVE_SINE)
    check("Tastgrad bei Sinus: abgelehnt", "Rechteck" in refused(lambda: mhs.set_duty_checked(gen, 1, 25.0)))
    gen.set_waveform(1, mhs.WAVE_SQUARE)
    mhs.set_duty_checked(gen, 1, 25.0)
    check("Tastgrad bei Rechteck in 0,1 %", sim.values[1]["d"] == 250 and gen.get_duty(1) == 25.0)
    check("Modulation/Last: abgelehnt", "kennt" in refused(lambda: mhs.set_param_checked(gen, 1, "load", 50)))
    sim.log.clear()
    gen.set_outputs(True, False)
    check("Ausgaenge einzeln", sim.log == [":s1b1", ":s2b0"] and gen.get_outputs() == (True, False), str(sim.log))
    mhs.outputs_off(gen)
    check("outputs_off: beide AUS", gen.get_outputs() == (False, False))
    state = mhs.state_dict(gen)
    check("state_dict: Profil, zwei Kanaele, keine Phase", state["profile"] == "mhs5200a"
          and len(state["channels"]) == 2 and state["phase"] is None)
    check("readback nach Amplitude: auch Offset", mhs.readback_fields("amplitude", 1) == (("amplitude", 1),
                                                                                       ("offset", 1)))

    # Grenzen: das Geraet begrenzt still und quittiert mit ok
    sim.values[1]["a"] = 0
    gen._write(1, "a", 9999)
    check("Geraet begrenzt still (a 9999 -> 2000)", gen._read_int(1, "a") == 2000)
    check("unbekanntes Lesefeld (nur Echo): Fehler", "Unerwartete" in refused(lambda: gen._read(1, "q"),
                                                                            FunctionGeneratorError))

    # Handshake gegen fremde Ports
    class Echo(FakeMhsPort):
        def _answer(self, line: str) -> bytes:
            return f"{line}\r\n".encode()

    class Silent(FakeMhsPort):
        def _answer(self, line: str) -> bytes:
            return b""

    class Jds(FakeMhsPort):
        def _answer(self, line: str) -> bytes:
            return b":ok\r\n"

    class Late(FakeMhsPort):
        calls = 0

        def _answer(self, line: str) -> bytes:
            Late.calls += 1
            return b"\r\n###\r\n" if Late.calls == 1 else super()._answer(line)

    for name, port, expected in (("Echo-Port", Echo(), False), ("stummer Port", Silent(), False),
                                 ("Port mit :ok auf alles", Jds(), False), ("erst Fehler, dann Kennung", Late(), True)):
        candidate = MHS5200A.__new__(MHS5200A)
        candidate._ser = port
        candidate.model, candidate.max_frequency_hz = mhs.TITLE, mhs.MAX_FREQUENCY_HZ
        check(f"Handshake {name}: {'erkannt' if expected else 'abgelehnt'}", candidate.probe() is expected)


# -- worker ----------------------------------------------------------------------------------------

class _PortFactory:
    """Ersatz fuer serial.Serial: jeder Port ist ein FakeMhsPort (ein MHS-5225A)."""

    def __init__(self) -> None:
        self.ports: dict[str, FakeMhsPort] = {}
        self.opened: list[tuple[str, int]] = []

    def __call__(self, port: str, baudrate: int = 9600, timeout=None, rtscts: bool = False, **_kw):
        self.opened.append((port, baudrate))
        fake = self.ports.setdefault(port, FakeMhsPort())
        fake.port = port
        return fake


def make_worker():
    import no_hardware
    no_hardware.block()
    import device_worker as dw
    return dw, dw.DeviceWorker(simulation_mode=False)


def section_worker() -> None:
    print("worker -- Erkennung nach dem JDS-Handshake, Aktionen, ALLE AUS")
    import serial
    dw, worker = make_worker()
    factory = _PortFactory()
    original = serial.Serial
    serial.Serial = factory
    try:
        worker._fg_connect_job("fg:usb-9-9", "/dev/fake-mhs")
        spin(1.0, lambda: worker._fgs)
    finally:
        serial.Serial = original
    mhs_id = "fg:mhs5200a-usb-9-9"
    check("erst JDS (115200), dann MHS (57600) am selben Port",
          [b for _p, b in factory.opened] == [115200, 57600], str(factory.opened))
    check("verbunden unter eigener ID", list(worker._fgs) == [mhs_id], str(list(worker._fgs)))
    sim = factory.ports["/dev/fake-mhs"]

    submitted: list[str] = []
    worker._submit_reconnect = lambda kind, device_id, job, port: submitted.append(port)
    info = SimpleNamespace(device="/dev/fake-mhs", serial_number=None, location="9-9", vid=0x1A86, pid=0x7523)
    dw.JDS66xx.discover_ports = staticmethod(lambda: [info])
    worker._reconnect_fgs()
    check("belegter Port wird nicht erneut abgefragt", submitted == [], str(submitted))

    results: list[tuple[bool, str]] = []
    worker.action_completed.connect(lambda ok, message, _v: results.append((ok, message)))

    def run(action: str, value: float = 0.0, channel: int = 1) -> tuple[bool, str]:
        count = len(results)
        worker.execute_action(mhs_id, "fg", action, value, channel)
        spin(2.0, lambda: len(results) > count)
        return results[-1] if len(results) > count else (False, "keine Rueckmeldung")

    check("Ablauf FG_WAVE_SQUARE, FG_FREQ, FG_AMPL, FG_OFFS, FG_DUTY, FG_OUT_ON",
          all(run(a, v) == (True, "") for a, v in (("FG_WAVE_SQUARE", 0), ("FG_FREQ", 2000.0), ("FG_AMPL", 3.0),
                                                    ("FG_OFFS", 0.6), ("FG_DUTY", 30.0), ("FG_OUT_ON", 0)))
          and sim.values[1]["w"] == 1 and sim.values[1]["f"] == 200000 and sim.values[1]["a"] == 300
          and sim.values[1]["y"] == 1 and sim.values[1]["o"] == 160 and sim.values[1]["d"] == 300
          and sim.values[1]["b"] == 1, str(results[-6:]) + str(sim.values[1]))
    ok, message = run("FG_WAVE_PULSE")
    check("FG_WAVE_PULSE: FAIL mit Hinweis auf Rechteck", not ok and "Rechteck" in message, message)
    ok, message = run("FG_WAVE_TRIANGLE")
    check("FG_WAVE_TRIANGLE", ok and sim.values[1]["w"] == 2, message)
    ok, message = run("FG_DUTY", 40.0)
    check("FG_DUTY bei Dreieck: FAIL mit Meldung", not ok and "Rechteck" in message, message)
    ok, message = run("FG_PHASE", 90.0)
    check("FG_PHASE: abgelehnt (keine Phase)", not ok and "Phase" in message, message)
    ok, message = run("FG_AMPL", 0.2)
    check("FG_AMPL zu klein fuer den Offset: FAIL mit Meldung", not ok and "Offset" in message, message)
    ok, message = run("FG_OUT_ON", 0, 2)
    check("Kanal 2 EIN", ok and sim.values[2]["b"] == 1, message)
    worker.all_outputs_off("check")
    check("ALLE AUS: beide Ausgaenge AUS", sim.values[1]["b"] == 0 and sim.values[2]["b"] == 0, str(sim.values))
    worker._drop_fg(mhs_id, "Pruefung beendet", quiet=True)


# -- model -----------------------------------------------------------------------------------------

def section_model() -> None:
    print("model -- statische Tastgrad-Pruefung im Ablauf-Editor")
    import testcase_model as tm

    def steps(wave: str, device_id: str) -> list:
        return [tm.TestStep(device_kind="fg", device_id=device_id, action=wave, hil_channel=1),
                tm.TestStep(device_kind="fg", device_id=device_id, action="FG_DUTY", value=30.0, hil_channel=1)]

    check("MHS: Rechteck + Tastgrad ok", tm.fg_waveform_refusal(steps("FG_WAVE_SQUARE", "fg:mhs5200a-COM5")) is None)
    refusal = tm.fg_waveform_refusal(steps("FG_WAVE_SINE", "fg:mhs5200a-COM5"))
    check("MHS: Sinus + Tastgrad -> nur bei Rechteck", refusal is not None and "nur bei Wellenform Rechteck"
          in refusal[1], str(refusal))
    refusal = tm.fg_waveform_refusal(steps("FG_WAVE_SQUARE", "fg:COM5"))
    check("JDS unveraendert: Rechteck + Tastgrad -> nur bei Puls", refusal is not None and "Puls" in refusal[1],
          str(refusal))


# -- hardware --------------------------------------------------------------------------------------

def section_hardware(port: str) -> None:
    print(f"hardware -- MHS-5200A an {port} (Ausgaenge bleiben wie vorgefunden)")
    gen = MHS5200A(port)
    check("Handshake", gen.probe(), "keine Kennung auf :r0c")
    print(f"  Geraet: {gen.identify()}, bis {gen.max_frequency_hz / 1e6:g} MHz")
    snapshot = {(ch, f): gen._read_int(ch, f) for ch in mhs.CHANNELS for f in "wfyaodb"}
    try:
        ch = 1
        gen.set_waveform(ch, mhs.WAVE_SQUARE)
        gen.set_frequency(ch, 1234.56)
        gen.set_amplitude(ch, 1.234)
        gen.set_offset(ch, 0.3)
        gen.set_duty(ch, 33.3)
        check("Rechteck 1234,56 Hz, 1,234 Vss, Offset 0,3 V, 33,3 % zurueckgelesen",
              gen.get_waveform(ch) == mhs.WAVE_SQUARE and gen.get_frequency(ch) == 1234.56
              and gen.get_amplitude(ch) == 1.234 and abs(gen.get_offset(ch) - 0.3) <= 0.01
              and gen.get_duty(ch) == 33.3, str(mhs.state_dict(gen)["channels"][0]))
        gen.set_amplitude(ch, 6.0)
        check("6 Vss ohne Daempfung, Offset bleibt in Volt", gen._read_int(ch, "y") == 1
              and gen.get_amplitude(ch) == 6.0 and abs(gen.get_offset(ch) - 0.3) <= 0.03, str(gen.get_offset(ch)))
        for code in (mhs.WAVE_TRIANGLE, mhs.WAVE_RAMP_UP, mhs.WAVE_RAMP_DOWN, 101, 116, mhs.WAVE_SINE):
            gen.set_waveform(ch, code)
            check(f"Form {mhs.waveform_name(code)} zurueckgelesen", gen.get_waveform(ch) == code,
                  str(gen.get_waveform(ch)))
    finally:
        for (ch, f) in snapshot:
            if f != "b":
                gen._write(ch, f, snapshot[(ch, f)])
        restored = {(ch, f): gen._read_int(ch, f) for ch in mhs.CHANNELS for f in "wfyaodb"}
        check("vorgefundener Zustand wiederhergestellt", restored == snapshot,
              str({k: (v, restored[k]) for k, v in snapshot.items() if restored[k] != v}))
        gen.close()

    print("hardware -- Erkennung ueber den DeviceWorker")
    dw, worker = make_worker()
    device_id = dw._resolve_device_ids("fg", [i for i in MHS5200A.discover_ports() if i.device == port])
    port_id = next(iter(device_id), f"fg:{port}")
    worker._fg_connect_job(port_id, port)
    spin(3.0, lambda: worker._fgs)
    expected = mhs.device_id_for(port_id)
    check(f"verbunden als {expected}", list(worker._fgs) == [expected], str(list(worker._fgs)))
    if expected in worker._fgs:
        check("Zustand gelesen", spin(2.0, lambda: expected in worker._fg_cache)
              and worker._fg_cache[expected]["outputs"] == [bool(snapshot[(1, "b")]), bool(snapshot[(2, "b")])],
              str(worker._fg_cache.get(expected)))
        worker._drop_fg(expected, "Pruefung beendet", quiet=True)


SECTIONS = {"pure": section_pure, "driver": section_driver, "worker": section_worker, "model": section_model}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sections", nargs="*")
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--port", default="")
    args = parser.parse_args()
    wanted = [s for s in args.sections if s in SECTIONS] or list(SECTIONS)
    for name in wanted:
        SECTIONS[name]()
    if args.hardware:
        if not args.port:
            print("--hardware braucht --port")
            return 2
        section_hardware(args.port)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FEHLER: " + "; ".join(FAILURES))
        return 1
    print("Alle Pruefungen bestanden." + ("" if args.hardware else " (nur Simulator)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
