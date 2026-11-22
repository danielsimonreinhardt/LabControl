"""Prueft den eingebauten Funktionsgenerator AWG1 der UNI-T MSO1000HD-S (1.0.2, PI-88 b).

Ohne Argument nur gegen den Simulator (device-driver/unit_mso_awg/mock.py, SCPI-Ebene -- der echte
Treiber laeuft mit), offscreen, keine Hardware:
  driver   -- Antworten lesen, Befehle, Grenzen, Ablehnungen (Tastgrad, DC), unbekannter Befehl, ohne Generator
  lock     -- Geraetesperre in unit_mso1000hd.driver.VisaTransport: Abfragen zweier Verbindungen vermischen
              sich nicht (am Geraet gibt es nur EINE Ausgabe), close() schliesst nur die eigene Verbindung
  worker   -- Erkennung ueber das Oszilloskop, Ablauf/Fernsteuerung/Kachel ueber den Thread _fg_io,
              ALLE AUS, Abschalten, Watchdog-Herzschlag, Trennen mit dem Oszilloskop
  model    -- Aktionen in Ablauf-Editor und Fernsteuerung, statische Tastgrad-Pruefung, Bericht
  tile     -- Kachel: ein Kanal, keine Phase, Modulation/Last je Art, Preset; Uebersicht
  partner  -- LiveState-Felder und Rueckweg beim Verbund-Partner, Bedienung aus der Partnerkachel

Mit --hardware --address <IP>: zusaetzlich am echten Geraet (Generator-Ausgang an CH2 desselben
Oszilloskops). Kleine Pegel (<= 2 Vss, |Offset| <= 1 V, <= 1 MHz), danach wird der vorgefundene Zustand
wiederhergestellt und abgefragt.

Aufruf:  python tools/check_unit_mso_awg.py [driver|lock|worker|model|tile|partner] [--hardware --address 192.168.1.50]
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LABCONTROL_DATA_DIR"] = tempfile.mkdtemp(prefix="lc-awg-check-")
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from jds66xx.driver import FunctionGeneratorError, FunctionGeneratorValueError  # noqa: E402
from unit_mso_awg import driver as awg  # noqa: E402
from unit_mso_awg.driver import UnitMsoAwg  # noqa: E402
from unit_mso_awg.mock import SimulatedAwg  # noqa: E402

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
        return str(exc) or cls.__name__
    return ""


# -- Treiber gegen den Simulator -------------------------------------------------------------------------

def section_driver() -> None:
    print("driver -- Treiber gegen den Simulator")
    sim = SimulatedAwg()
    gen = UnitMsoAwg(sim)
    check("probe: AWG1:OUTPut? beantwortet", gen.probe())
    check("identify: Modell, Seriennummer, AWG1", gen.identify() == "MSO1154HD-S SIM-AWG-0001 V1.01.0081 AWG1",
          gen.identify())
    state = awg.state_dict(gen)
    check("Zustand wie vorgefunden: RAMP 1 kHz 1 Vss, Last 1 MΩ, BASe, Ausgang EIN",
          state["profile"] == "unit_mso_awg" and state["outputs"] == [True] and state["phase"] is None
          and state["channels"][0] == {"waveform": awg.WAVE_RAMP, "frequency": 1000.0, "amplitude": 1.0,
                                       "offset": 0.0, "duty": 50.0}
          and state["mode"] == "BASE" and state["load"] == 1e6 and state["mod_wave"] == "SINE"
          and state["sweep_type"] == "LIN", str(state))
    gen.set_output(1, False)
    check("Ausgang AUS: AWG1:OUTPut OFF", sim.log[-1] == "AWG1:OUTPut OFF" and gen.get_outputs() == (False,))
    awg.outputs_off(gen)
    check("outputs_off: ein Befehl", sim.log[-1] == "AWG1:OUTPut OFF")
    gen.set_waveform(1, awg.WAVE_SQUARE)
    gen.set_frequency(1, 12345.678)
    gen.set_amplitude(1, 1.5)
    gen.set_offset(1, -0.25)
    check("Befehle wie am Geraet geprueft", sim.log[-4:] == ["AWG1:BASe:WAVe SQUare", "AWG1:BASe:FREQuency 12345.678",
                                                              "AWG1:BASe:AMPLitude 1.5", "AWG1:BASe:OFFSet -0.25"],
          str(sim.log[-4:]))
    check("zurueckgelesen", gen.get_waveform(1) == awg.WAVE_SQUARE and abs(gen.get_frequency(1) - 12345.678) < 0.01
          and gen.get_amplitude(1) == 1.5 and gen.get_offset(1) == -0.25)
    check("Kanal 2 abgelehnt", "nur Kanal 1" in refused(lambda: gen.set_frequency(2, 1000)))
    check("Frequenz 0 vom Treiber abgelehnt (nicht gesendet)",
          refused(lambda: gen.set_frequency(1, 0.0)) != "" and not sim.log[-1].endswith(" 0"))
    check("Amplitude ueber Rahmen abgelehnt", refused(lambda: gen.set_amplitude(1, 25.0)) != "")
    check("Arbitraer nicht einstellbar", refused(lambda: gen.set_waveform(1, awg.WAVE_ARB)) != "")

    # Tastgrad: am Geraet wirkt er bei Rechteck/Puls; in RAMP schaltet er die Ausgabe still auf Rechteck
    awg.set_duty_checked(gen, 1, 25.0)
    check("Tastgrad bei Rechteck: gesetzt", gen.get_duty(1) == 25.0 and sim.effective_waveform() == "SQUare")
    awg.set_waveform_checked(gen, 1, awg.WAVE_RAMP)
    message = refused(lambda: awg.set_duty_checked(gen, 1, 30.0))
    check("Tastgrad bei Rampe abgelehnt, nicht gesendet (Eigenheit: Ausgabe wuerde Rechteck)",
          "Rechteck und Puls" in message and "Rampe" in message and sim.effective_waveform() == "RAMP"
          and gen.get_duty(1) == 25.0, message)
    awg.set_waveform_checked(gen, 1, awg.WAVE_PULSE)
    awg.set_duty_checked(gen, 1, 10.0)
    check("Tastgrad bei Puls: gesetzt", gen.get_duty(1) == 10.0)
    sim.set_simulated = None
    sim.write("AWG1:BASe:DUTY 150")
    check("Simulator klemmt wie das Geraet (150 -> 99 %)", gen.get_duty(1) == 99.0)
    awg.set_waveform_checked(gen, 1, awg.WAVE_DC)
    message = refused(lambda: awg.set_amplitude_checked(gen, 1, 1.0))
    check("DC: Amplitude abgelehnt", "DC" in message, message)
    awg.set_offset_checked(gen, 1, 0.5)
    check("DC: Spannung = Offset", gen.get_offset(1) == 0.5)

    # Modulation und Last
    for field, value, expect in (("mode", "AM", "AM"), ("mode", "sweep", "SWEEP"), ("mod_wave", "DNRAMP", "DNRAMP"),
                                 ("mod_freq", 250.0, 250.0), ("am_depth", 80.0, 80.0), ("fm_dev", 500.0, 500.0),
                                 ("fsk_rate", 50.0, 50.0), ("fsk_freq", 3000.0, 3000.0), ("sweep_type", "LOG", "LOG"),
                                 ("sweep_start", 100.0, 100.0), ("sweep_stop", 900.0, 900.0),
                                 ("sweep_time", 0.5, 0.5)):
        awg.set_param_checked(gen, 1, field, value)
        check(f"{field} = {value!r}", gen.get_param(1, field) == expect, str(gen.get_param(1, field)))
    check("Modulationsart unbekannt abgelehnt", refused(lambda: gen.set_mode(1, "PM")) != "")
    check("AM-Tiefe 130 % abgelehnt", refused(lambda: gen.set_param(1, "am_depth", 130.0)) != "")
    check("Wobbelzeit 1000 s abgelehnt", refused(lambda: gen.set_param(1, "sweep_time", 1000.0)) != "")
    gen.set_mode(1, "BASE")
    gen.set_waveform(1, awg.WAVE_SINE)
    gen.set_amplitude(1, 0.5)
    gen.set_output(1, True)
    gen.set_load(1, 50)
    check("Last 50 Ω: Amplitude zaehlt an der Last (0,5 -> 0,25), am Ausgang unveraendert",
          gen.get_load() == 50 and gen.get_amplitude(1) == 0.25 and sim.amplitude_at_output() == 0.5,
          f"{gen.get_amplitude(1)} {sim.amplitude_at_output()}")
    gen.set_load(1, 123.4)
    check("Last ganze Ohm (123,4 -> 123)", gen.get_load() == 123.0 and "AWG1:LOAD 123" in sim.log[-3:])
    check("Last unter 50 Ω abgelehnt (am Geraet ab 50)", refused(lambda: gen.set_load(1, 10)) != "")
    gen.set_load(1, awg.HIGH_Z_OHM)
    check("hochohmig: Amplitude wieder 0,5", gen.get_load() == 1e6 and gen.get_amplitude(1) == 0.5)
    check("readback nach Last: Last, Amplitude, Offset",
          awg.readback_fields("load") == (("load", 0), ("amplitude", 1), ("offset", 1)))

    # unbekannter Befehl: I/O-Fehler beim Senden, Fehlerspeicher -113, Verbindung bleibt
    message = refused(lambda: gen._write("AWG1:FOO 1"))
    check("unbekannter Befehl: Wertfehler mit -113, kein Verbindungsfehler", "-113" in message, message)
    check("... Fehlerspeicher danach leer, Generator antwortet", not sim.errors and gen.get_outputs() in ((True,), (False,)))
    # ohne Generator (Modell ohne -S)
    plain = SimulatedAwg(has_awg=False)
    check("Oszilloskop ohne Generator: probe False, Fehlerspeicher abgeholt",
          not UnitMsoAwg(plain).probe() and not plain.errors)
    # Verbindung weg
    sim.dead = True
    check("Verbindung weg: FunctionGeneratorError (kein Wertfehler)",
          refused(lambda: gen.get_outputs(), FunctionGeneratorError) != ""
          and not isinstance(_raised(lambda: gen.get_outputs()), FunctionGeneratorValueError))


def _raised(call):
    try:
        call()
    except Exception as exc:  # noqa: BLE001
        return exc
    return None


# -- Geraetesperre im Transport --------------------------------------------------------------------------

class _SharedOutputDevice:
    """Nachbildung des Befunds am MSO1154HD-S: eine Ausgabe fuer alle Verbindungen."""

    def __init__(self) -> None:
        self.output: list[str] = []
        self.lock = threading.Lock()

    def resource(self):
        device = self

        class Resource:
            closed = False

            def write(self, command: str) -> None:
                time.sleep(0.001)
                if command.endswith("?"):
                    with device.lock:
                        device.output.append(command)

            def read(self) -> str:
                time.sleep(0.002)
                with device.lock:
                    return device.output.pop(0) if device.output else ""

            def read_raw(self) -> bytes:
                return self.read().encode()

            def close(self) -> None:
                self.closed = True
        return Resource()


def section_lock() -> None:
    print("lock -- Geraetesperre (eine Ausgabe fuer alle Verbindungen)")
    from unit_mso1000hd import driver as mso
    device = _SharedOutputDevice()

    def transport(resource: str):
        t = mso.VisaTransport.__new__(mso.VisaTransport)
        t._lock = mso.io_lock(resource)
        t._pending = 0
        t._res = device.resource()
        return t

    a, b = transport("TCPIP0::10.0.0.7::inst0::INSTR"), transport("TCPIP0::10.0.0.7::5025::SOCKET")
    check("VXI-11 und Socket desselben Hosts teilen die Sperre", a._lock is b._lock)
    check("anderer Host, andere Sperre", transport("TCPIP0::10.0.0.8::inst0::INSTR")._lock is not a._lock)
    mixed: list[str] = []

    def worker(t, tag: str) -> None:
        for n in range(60):
            command = f"{tag}{n}?"
            t.write(command)
            answer = t.read_line()
            if answer != command:
                mixed.append(f"{command} -> {answer}")
            t.write(f"{tag}SET {n}")   # reiner Befehl: haelt die Sperre nicht

    threads = [threading.Thread(target=worker, args=(a, "A")), threading.Thread(target=worker, args=(b, "B"))]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    check("120 Abfragen aus zwei Threads: jede Antwort beim Fragenden", not mixed, str(mixed[:3]))
    check("Sperre danach frei", a._lock.acquire(timeout=0.1) and (a._lock.release() or True))
    a.write("X?")
    a.close()
    check("close() mit offener Abfrage gibt die Sperre frei", b._lock.acquire(timeout=0.1) and (b._lock.release() or True))
    check("close() schliesst nur die eigene Verbindung (nicht den ResourceManager)",
          a._res.closed and not b._res.closed and "rm.close" not in Path(mso.__file__).read_text(encoding="utf-8"))


# -- Worker ----------------------------------------------------------------------------------------------

def make_worker(simulation: bool = True):
    import no_hardware
    no_hardware.block()
    import device_worker as dw
    worker = dw.DeviceWorker(simulation_mode=False)
    if simulation:
        worker._add_mock_scpiscope()
    return dw, worker


def section_worker() -> None:
    print("worker -- Erkennung, Bedienung, ALLE AUS")
    dw, worker = make_worker()
    awg_id = dw.SIM_AWG_ID
    check("Simulation: Generator im simulierten Oszilloskop als fg:SIM:AWG1", awg_id == "fg:SIM:AWG1"
          and awg_id in worker._fgs, str(list(worker._fgs)))
    sim = worker._fgs[awg_id]._t
    states: list[tuple[str, dict]] = []
    worker.fg_state.connect(lambda d, st: states.append((d, st)))
    results: list[tuple[bool, str]] = []
    worker.action_completed.connect(lambda ok, message, _v: results.append((ok, message)))
    remote: list[tuple[int, bool, str]] = []
    worker.remote_action_completed.connect(lambda rid, ok, message, _v: remote.append((rid, ok, message)))

    def run(action: str, value: float = 0.0, channel: int = 1) -> tuple[bool, str]:
        count = len(results)
        worker.execute_action(awg_id, "fg", action, value, channel)
        spin(3.0, lambda: len(results) > count)
        return results[-1] if len(results) > count else (False, "keine Rueckmeldung")

    check("Ablauf FG_OUT_OFF (Auftrag im Thread, Ergebnis danach)", run("FG_OUT_OFF") == (True, "")
          and not sim.output)
    check("... Zustand gemeldet: Ausgang AUS", spin(1.0, lambda: states and states[-1][1]["outputs"] == [False]),
          str(states[-1:]))
    check("Ablauf FG_WAVE_SQUARE, FG_FREQ, FG_AMPL, FG_OFFS, FG_DUTY",
          all(run(a, v) == (True, "") for a, v in (("FG_WAVE_SQUARE", 0), ("FG_FREQ", 2000.0), ("FG_AMPL", 0.8),
                                                    ("FG_OFFS", 0.1), ("FG_DUTY", 30.0)))
          and sim.wave == "SQUare" and sim.frequency == 2000.0 and sim.duty == 30.0, str(results[-5:]))
    ok, message = run("FG_WAVE_TRIANGLE")
    check("FG_WAVE_TRIANGLE = RAMP", ok and sim.wave == "RAMP")
    ok, message = run("FG_DUTY", 40.0)
    check("Ablauf FG_DUTY bei Rampe: FAIL mit Meldung", not ok and "Rechteck und Puls" in message, message)
    ok, message = run("FG_PHASE", 90.0)
    check("Ablauf FG_PHASE: abgelehnt (keine Phase)", not ok and "Phase" in message, message)
    ok, message = run("FG_FREQ", 1000.0, 2)
    check("Ablauf Kanal 2: abgelehnt", not ok and "Kanal 2" in message, message)
    for action, value, check_attr in (("FG_MODE_FSK", 0.0, lambda: sim.mode == "FSK"),
                                      ("FG_FSK_RATE", 20.0, lambda: sim.params["FSK:RATio"] == 20.0),
                                      ("FG_MOD_WAVE_UPRAMP", 0.0, lambda: sim.params["MODulate:WAVe"] == "UPRamp"),
                                      ("FG_SWEEP_LOG", 0.0, lambda: sim.params["SWEep:TYPe"] == "LOG"),
                                      ("FG_LOAD", 75.0, lambda: sim.load == 75.0),
                                      ("FG_MODE_BASE", 0.0, lambda: sim.mode == "BASe")):
        check(f"Ablauf {action}", run(action, value) == (True, "") and check_attr(), str(results[-1]))
    check("Zustand enthaelt Modulation/Last", spin(1.0, lambda: states[-1][1].get("load") == 75.0
                                                    and states[-1][1].get("fsk_rate") == 20.0), str(states[-1][1]))

    # Fernsteuerung (MCP/REST/Verbund) und Kachel
    worker.execute_remote_action(7, awg_id, "fg", "FG_LOAD", 1e6, 1)
    spin(2.0, lambda: remote)
    check("Fernsteuerung FG_LOAD 1 MΩ", remote and remote[-1] == (7, True, "") and sim.load == 1e6, str(remote))
    worker.execute_remote_action(8, awg_id, "fg", "FG_DUTY", 20.0, 1)
    spin(2.0, lambda: len(remote) > 1)
    check("Fernsteuerung FG_DUTY bei Rampe: Fehler mit Meldung", len(remote) > 1 and not remote[-1][1]
          and "Rechteck" in remote[-1][2], str(remote))
    worker.fg_tile_action(awg_id, "FG_MODE_AM", 0.0, 1)
    worker.set_fg_frequency(awg_id, 1, 3000.0)
    spin(2.0, lambda: sim.mode == "AM" and sim.frequency == 3000.0)
    check("Kachel: Modulation AM und Frequenz", sim.mode == "AM" and sim.frequency == 3000.0)
    log = worker.switch_log
    check("Schaltprotokoll: Kachel-Aktion verbucht",
          spin(1.0, lambda: log.last("fg", awg_id, "FG_MODE_AM") is not None))

    # JDS2915 lehnt Modulation ab
    worker._add_mock_fg()
    count = len(results)
    worker.execute_action(dw.SIM_FG_ID, "fg", "FG_MODE_AM", 0.0, 1)
    check("JDS2915: FG_MODE_AM abgelehnt (sofort)", len(results) == count + 1 and not results[-1][0]
          and "JDS66xx" in results[-1][1], str(results[-1:]))

    # Herzschlag fuer den Watchdog: Abfrage im Takt
    states.clear()
    for _ in range(6):
        worker._poll_fgs()
        spin(0.05)
    awg_states = [s for d, s in states if d == awg_id]
    check("Abfrage im Thread: Zustand je Zyklus gemeldet (Herzschlag)", len(awg_states) >= 5, str(len(awg_states)))
    check("... hoechstens eine Abfrage zugleich", len(worker._fg_poll_busy) <= 2)
    # ALLE AUS: Generator aus, JDS beide aus
    sim.output = True
    worker._fgs[dw.SIM_FG_ID].set_outputs(True, True)
    finished: list[str] = []
    worker.all_off_finished.connect(finished.append)
    worker.all_outputs_off("check")
    check("ALLE AUS: Generator im Oszilloskop sofort aus (nicht hinter Auftraegen)", not sim.output and finished
          and finished[-1] == "", str(finished))
    check("ALLE AUS: JDS beide aus", worker._fgs[dw.SIM_FG_ID].get_outputs() == (False, False))
    check("ALLE AUS: Zustand gemeldet", spin(1.0, lambda: any(d == awg_id and s["outputs"] == [False]
                                                             for d, s in states[-3:])))
    check("ALLE AUS im Schaltprotokoll", log.last("fg", awg_id, "FG_OUT_OFF") is not None)
    sim.output = True
    done: list[tuple] = []
    worker.devices_off_done.connect(lambda tag, off, failed: done.append((tag, list(off), list(failed))))
    worker.devices_off_tagged(3, [awg_id], "Abbruch")
    check("Abschalten beim Abbruch (PI-79 a)", done and done[-1] == (3, [awg_id], []) and not sim.output, str(done))
    # Verbindung weg -> getrennt
    removed: list[tuple[str, str]] = []
    worker.device_removed.connect(lambda kind, d: removed.append((kind, d)))
    sim.dead = True
    worker._poll_fgs()
    check("Verbindung weg: Generator getrennt", spin(2.0, lambda: ("fg", awg_id) in removed)
          and awg_id not in worker._fgs, str(removed))
    sim.dead = False

    # Erkennung ueber ein (simuliertes) UNI-T-Oszilloskop
    print("worker -- Erkennung ueber das Oszilloskop")
    added: list[tuple[str, str]] = []
    worker.device_added.connect(lambda kind, d: added.append((kind, d)))
    scope_id = "scpiscope:10.9.8.7"
    worker._scpi_resources[scope_id] = "SIM"
    worker._scpi_present[scope_id] = True
    worker._on_scpi_probe_done(scope_id, True, "MSO1154HD-S", "AHD000")
    new_id = "fg:10.9.8.7:AWG1"
    check("UNI-T erreichbar -> Generator fg:<Adresse>:AWG1", spin(3.0, lambda: ("fg", new_id) in added),
          str(added))
    check("... mit vollem Zustand", any(d == new_id and s.get("profile") == "unit_mso_awg" for d, s in states))
    worker._on_scpi_probe_done(scope_id, True, "MSO1154HD-S", "AHD000")
    spin(0.3)
    check("weitere Erkennung: kein zweiter Generator", [a for a in added if a[1] == new_id] == [("fg", new_id)])
    worker._on_scpi_probe_done(scope_id, False, "", "")
    check("Oszilloskop weg -> Generator getrennt", ("fg", new_id) in removed and new_id not in worker._fgs)
    # Modell ohne Generator: einmal fragen, dann in Ruhe lassen
    original_open = UnitMsoAwg.open
    opened: list[str] = []

    def open_without_awg(resource, timeout_s=5.0):
        opened.append(resource)
        return UnitMsoAwg(SimulatedAwg(has_awg=False))
    UnitMsoAwg.open = staticmethod(open_without_awg)
    try:
        other = "scpiscope:10.9.8.8"
        worker._scpi_resources[other] = "SIM"
        worker._scpi_present[other] = True
        worker._on_scpi_probe_done(other, True, "MSO1104HD", "X")
        spin(1.0, lambda: other in worker._awg_rejected)
        worker._on_scpi_probe_done(other, True, "MSO1104HD", "X")
        spin(0.3)
        check("ohne Generator: abgelehnt und nur einmal gefragt", other in worker._awg_rejected and len(opened) == 1
              and "fg:10.9.8.8:AWG1" not in worker._fgs, str(opened))
        worker._scpi_resources["scpiscope:10.9.8.9"] = "SIM"
        worker._scpi_present["scpiscope:10.9.8.9"] = True
        worker._on_scpi_probe_done("scpiscope:10.9.8.9", True, "RTB2004", "X")
        spin(0.3)
        check("R&S-Oszilloskop: gar nicht gefragt", len(opened) == 1)
    finally:
        UnitMsoAwg.open = original_open
    worker._remove_mock_scpiscope()
    check("Simulation aus: Generator weg", dw.SIM_AWG_ID not in worker._fgs)


# -- Ablauf-Modell, Fernsteuerung, Bericht ---------------------------------------------------------------

def section_model() -> None:
    print("model -- Aktionen, statische Pruefung, Bericht")
    import remote_actions
    from fg_profiles import FG_PARAM_ACTIONS
    from testcase_model import (ACTION_VALUE_RANGE, FG_ACTIONS, HIL_CHANNEL_COUNTS, VALUELESS_ACTIONS, TestStep,
                                fg_waveform_refusal)
    check("alle Modulations-/Lastaktionen im Ablauf-Editor und in der Fernsteuerung",
          all(c in FG_ACTIONS and c in remote_actions.REMOTE_ACTIONS["fg"] for c in FG_PARAM_ACTIONS))
    check("Kanal 1 bei Modulation/Last", all(HIL_CHANNEL_COUNTS[c] == 1 for c in FG_PARAM_ACTIONS))
    check("FG_LOAD: Ohm 50..1000000", ACTION_VALUE_RANGE["FG_LOAD"] == ("Ω", 50.0, 1_000_000.0)
          and "FG_LOAD" not in VALUELESS_ACTIONS and "FG_MODE_AM" in VALUELESS_ACTIONS)
    profile = awg.PROFILE
    definition, error, _ = remote_actions.validate("fg", "FG_LOAD", 10.0, 1, None, profile)
    check("Fernsteuerung FG_LOAD 10 Ω: ausserhalb", error == "value_out_of_range", error)
    definition, error, _ = remote_actions.validate("fg", "FG_MODE_AM", None, 2, None, profile)
    check("Fernsteuerung FG_MODE_AM Kanal 2: ausserhalb", error == "channel_out_of_range", error)
    definition, error, _ = remote_actions.validate("fg", "FG_FREQ", 1000.0, 2, None, profile)
    check("Fernsteuerung UNI-T FG_FREQ Kanal 2: ausserhalb (ein Kanal)", error == "channel_out_of_range", error)
    definition, error, _ = remote_actions.validate("fg", "FG_FREQ", 20e6, 1, None, profile)
    check("Fernsteuerung FG_FREQ 20 MHz im Rahmen (UNI-T bis 25 MHz)", error == "", error)
    definition, error, detail = remote_actions.validate("fg", "FG_PHASE", 90.0, 1, None, profile)
    check("Fernsteuerung UNI-T FG_PHASE: unbekannt", error == "unknown_action" and "FG_LOAD" in detail["allowed"]
          and "FG_PHASE" not in detail["allowed"], error)
    definition, error, _ = remote_actions.validate("fg", "FG_LOAD", 50.0, 1)
    check("Fernsteuerung JDS (ohne Profil) FG_LOAD: unbekannt", error == "unknown_action", error)
    listed = {a["action"]: a for a in remote_actions.describe("fg", None, profile)}
    check("Aktionsliste UNI-T: Kanal 1, Modulation/Last, keine Phase", listed["FG_FREQ"]["channels"] == 1
          and "FG_MODE_SWEEP" in listed and "FG_PHASE" not in listed
          and listed["FG_LOAD"]["value"] == {"unit": "Ω", "min": 50.0, "max": 1_000_000.0}, str(listed.get("FG_LOAD")))
    listed = {a["action"]: a for a in remote_actions.describe("fg")}
    check("Aktionsliste JDS unveraendert (2 Kanaele, Phase, keine Modulation)", listed["FG_FREQ"]["channels"] == 2
          and "FG_PHASE" in listed and "FG_LOAD" not in listed)

    def fg(action, value=0.0, device_id="fg:192.168.1.5:AWG1"):
        return TestStep(device_kind="fg", device_id=device_id, action=action, value=value, hil_channel=1)
    check("UNI-T: Rechteck, dann Tastgrad -> ok", fg_waveform_refusal([fg("FG_WAVE_SQUARE"), fg("FG_DUTY", 10)]) is None)
    got = fg_waveform_refusal([fg("FG_WAVE_TRIANGLE"), fg("FG_DUTY", 10)])
    check("UNI-T: Rampe, dann Tastgrad -> abgelehnt", got is not None and got[0] == 1 and "Rechteck und Puls" in got[1]
          and "Rampe" in got[1], str(got))
    got = fg_waveform_refusal([fg("FG_WAVE_SQUARE", device_id="fg:COM5"), fg("FG_DUTY", 10, device_id="fg:COM5")])
    check("JDS unveraendert: Rechteck + Tastgrad abgelehnt", got is not None and "Puls" in got[1], str(got))
    got = fg_waveform_refusal([fg("FG_WAVE_SQUARE", device_id="@pi/fg:10.0.0.2:AWG1"),
                               fg("FG_DUTY", 10, device_id="@pi/fg:10.0.0.2:AWG1")])
    check("Partnergeraet erkannt", got is None, str(got))
    from types import SimpleNamespace
    from run_report import _step_started_text
    record = SimpleNamespace(steps=[fg("FG_LOAD", 50.0), fg("FG_MODE_AM")], device_meta={})
    text = _step_started_text(record, 0) + " | " + _step_started_text(record, 1)
    check("Bericht: Wert mit Einheit, Aktion ohne Wert ohne Zahl", "50 Ω" in text and "Kanal 1:" not in
          text.split("|")[1], text)


# -- Kachel und Uebersicht ----------------------------------------------------------------------------------

def awg_state(**overrides) -> dict:
    sim = UnitMsoAwg(SimulatedAwg())
    state = awg.state_dict(sim)
    state.update(overrides)
    return state


def section_tile() -> None:
    print("tile -- Kachel und Uebersicht")
    from control_tab import FgControlGroup
    tile = FgControlGroup("fg:SIM:AWG1", "Generator")
    tile.show()
    before = tile.modulation_rows()
    check("vor der ersten Meldung: JDS-Kachel (2 Kanaele, Phase, keine Modulation)",
          before["channel2"] and before["phase"] and not before["visible"], str(before))
    tile.set_state(awg_state())
    rows = tile.modulation_rows()
    check("UNI-T: ein Kanal, keine Phase, Modulation/Last sichtbar", not rows["channel2"] and not rows["phase"]
          and rows["visible"], str(rows))
    check("BASe: nur Art und Last", rows["rows"] == ["mode", "load"], str(rows["rows"]))
    check("Werte uebernommen (Last 1 MΩ)", rows["values"]["load"] == 1e6 and rows["values"]["mode"] == "BASE")
    combo = tile._boxes[1].wave_combo
    check("Formen des UNI-T (Rampe, Rauschen, kein CMOS)",
          [combo.itemText(i) for i in range(combo.count())] == ["Sinus", "Rechteck", "Puls", "Rampe", "Rauschen", "DC"],
          str([combo.itemText(i) for i in range(combo.count())]))
    check("Rampe: Tastgrad gesperrt", not tile.channel_rows(1)["duty_enabled"])
    tile.set_state(awg_state(channels=[{"waveform": awg.WAVE_SQUARE, "frequency": 1e3, "amplitude": 1.0,
                                        "offset": 0.0, "duty": 50.0}]))
    check("Rechteck: Tastgrad bedienbar (UNI-T)", tile.channel_rows(1)["duty_enabled"])
    for mode, expect in (("AM", ["mode", "mod_wave", "mod_freq", "am_depth", "load"]),
                         ("FM", ["mode", "mod_wave", "mod_freq", "fm_dev", "load"]),
                         ("ASK", ["mode", "fsk_rate", "load"]),
                         ("FSK", ["mode", "fsk_rate", "fsk_freq", "load"]),
                         ("SWEEP", ["mode", "sweep_type", "sweep_start", "sweep_stop", "sweep_time", "load"])):
        tile.set_state(awg_state(mode=mode))
        got = tile.modulation_rows()["rows"]
        check(f"Art {mode}: Zeilen {expect[1:-1]}", got == expect, str(got))
    sent: list[tuple] = []
    tile.fg_action.connect(lambda d, code, value, ch: sent.append((d, code, value, ch)))
    box = tile._modulation
    box.widgets["mode"].setCurrentIndex(box.widgets["mode"].findData("FSK"))
    box.rows["mode"].findChildren(type(tile._phase_button))[0].click()
    box.widgets["load"].setValue(50.0)
    box.rows["load"].findChildren(type(tile._phase_button))[0].click()
    box.rows["load"].findChildren(type(tile._phase_button))[1].click()
    check("Knoepfe senden Aktionscodes (Art, Last, hochohmig)",
          sent == [("fg:SIM:AWG1", "FG_MODE_FSK", 0.0, 1), ("fg:SIM:AWG1", "FG_LOAD", 50.0, 1),
                   ("fg:SIM:AWG1", "FG_LOAD", 1e6, 1)], str(sent))
    # Preset
    tile.set_state(awg_state(mode="AM", am_depth=60.0, load=75.0))
    preset = tile.capture_state()
    check("Preset enthaelt Profil, einen Kanal, Modulation und Last", preset.get("profile") == "unit_mso_awg"
          and len(preset["channels"]) == 1 and preset["mode"] == "AM" and preset["load"] == 75.0, str(preset))
    sent.clear()
    waves: list = []
    tile.set_phase.connect(lambda *a: waves.append(("phase",) + a))
    tile.set_output.connect(lambda *a: waves.append(("out",) + a))
    tile.apply_state(preset)
    codes = [s[1] for s in sent]
    check("Preset: Last zuerst, Art zuletzt, keine Phase", codes and codes[0] == "FG_LOAD" and codes[-1] == "FG_MODE_AM"
          and "FG_AM_DEPTH" in codes and not any(w[0] == "phase" for w in waves), str(codes))
    # Uebersicht
    from dashboard import DashboardWidget
    dash = DashboardWidget()
    dash.on_device_known("fg", "fg:SIM:AWG1", "Generator")
    dash.update_fg("fg:SIM:AWG1", awg_state(mode="AM"))
    panel = dash._panels["fg:SIM:AWG1"]
    text = panel._value_labels["fg_ch1"].text()
    check("Uebersicht: Zeile Kanal 2 ausgeblendet", not panel._form.isRowVisible(panel._value_rows["fg_ch2"]))
    check("Uebersicht: Art vor der Form", text.startswith("AM · Rampe"), text)


# -- Verbund-Partner ---------------------------------------------------------------------------------------

def section_partner() -> None:
    print("partner -- Felder und Rueckweg")
    from live_state import LiveState
    from partner_devices import PartnerDevices, fg_state_from
    live = LiveState()
    live.on_device_known("fg", "fg:SIM:AWG1", "Generator") if hasattr(live, "on_device_known") else None
    state = awg_state(mode="FSK", fsk_rate=20.0, load=50.0)
    live.on_fg_state("fg:SIM:AWG1", state)
    snap = live.snapshot()
    import share_api
    from partner_devices import fields_of
    tile = share_api._tile_detail("fg:SIM:AWG1", snap["devices"]["fg:SIM:AWG1"], snap)
    fields = fields_of(tile)
    check("LiveState: Profil, ein Kanal, keine Phase, Modulation/Last als Felder",
          fields.get("profile") == "unit_mso_awg" and "out1" in fields and "out2" not in fields
          and "phase" not in fields and fields.get("mode") == "FSK" and fields.get("load") == 50.0
          and fields.get("wave1") == "Rampe", str(fields))
    back = fg_state_from(fields)
    check("Partner: Rueckweg ergibt denselben Zustand", back["outputs"] == state["outputs"]
          and back["channels"][0]["waveform"] == awg.WAVE_RAMP and back["phase"] is None
          and back["mode"] == "FSK" and back["fsk_rate"] == 20.0 and back.get("profile") == "unit_mso_awg",
          str(back))
    jds_back = fg_state_from({"out1": 1, "wave1": "Sinus", "out2": 0, "wave2": "Puls", "phase": 90})
    check("Partner: JDS unveraendert (2 Kanaele, Phase)", len(jds_back["channels"]) == 2 and jds_back["phase"] == 90
          and "profile" not in jds_back, str(jds_back))

    class Link:
        from PySide6.QtCore import QObject, Signal as _Signal

        class _L(QObject):
            from PySide6.QtCore import Signal
            snapshot_changed = Signal(object)
            action_finished = Signal(int, str, str, bool, str)
        def __init__(self):
            self.obj = self._L()
            self.snapshot_changed = self.obj.snapshot_changed
            self.action_finished = self.obj.action_finished
            self.sent: list[tuple] = []

        def name(self):
            return "PC"

        def send_action(self, tile_id, code, value, channel, label):
            self.sent.append((tile_id, code, value, channel))
            return len(self.sent)

    link = Link()
    partner = PartnerDevices(link)
    partner._control["@pi/fg:10.0.0.2:AWG1"] = True
    partner._known["@pi/fg:10.0.0.2:AWG1"] = ("fg", "Generator")
    from control_tab import FgControlGroup
    section = FgControlGroup("@pi/fg:10.0.0.2:AWG1", "Generator")
    partner.wire_section("fg", "@pi/fg:10.0.0.2:AWG1", section)
    section.fg_action.emit("@pi/fg:10.0.0.2:AWG1", "FG_LOAD", 50.0, 1)
    section.fg_action.emit("@pi/fg:10.0.0.2:AWG1", "FG_MODE_AM", 0.0, 1)
    check("Partnerkachel: Last mit Wert, Art ohne Wert an den Besitzer",
          link.sent == [("fg:10.0.0.2:AWG1", "FG_LOAD", 50.0, 1), ("fg:10.0.0.2:AWG1", "FG_MODE_AM", None, 1)],
          str(link.sent))


# -- Hardware ----------------------------------------------------------------------------------------------

def section_hardware(address: str) -> None:
    print(f"hardware -- MSO1000HD-S unter {address} (Generator an CH2)")
    import numpy as np
    from scope_api import analysis
    from scope_api.base import AcquireRequest
    from scope_api.unit_mso_adapter import open_unit_mso
    from unit_mso1000hd.driver import normalize_resource
    resource = normalize_resource(address)
    gen = UnitMsoAwg.open(resource)
    check("probe am Geraet", gen.probe())
    print("   ", gen.identify())
    found = awg.state_dict(gen)
    print("    vorgefunden:", found)
    scope = open_unit_mso(resource)

    def measure(duration=0.005, range_v=2.0):
        cap = scope.acquire(AcquireRequest.from_dict({
            "channels": [{"name": "CH2", "range_v": range_v, "coupling": "DC", "probe": 1}],
            "timing": {"duration_s": duration}, "trigger": {"mode": "auto", "source": "CH2", "level_v": 0.0}}))
        c = cap.channels["CH2"]
        t = (np.arange(c.volts.size) - cap.trigger_index) * cap.sample_interval_s
        return analysis.measure(t, c.volts, ["vpp", "vmean", "frequency", "duty"])[0]

    try:
        gen.set_load(1, awg.HIGH_Z_OHM)
        gen.set_mode(1, "BASE")
        awg.set_waveform_checked(gen, 1, awg.WAVE_SQUARE)
        gen.set_frequency(1, 2000.0)
        gen.set_amplitude(1, 1.0)
        gen.set_offset(1, 0.2)
        awg.set_duty_checked(gen, 1, 25.0)
        gen.set_output(1, True)
        m = measure()
        check("Rechteck 2 kHz 1 Vss Offset 0,2 V Tastgrad 25 %: gemessen",
              abs(m["frequency"] - 2000) < 20 and abs(m["vpp"] - 1.0) < 0.08 and abs(m["duty"] - 25) < 1.5,
              str(m))
        awg.set_waveform_checked(gen, 1, awg.WAVE_SINE)
        gen.set_offset(1, 0.0)
        gen.set_frequency(1, 1_000_000.0)
        gen.set_amplitude(1, 2.0)
        m = measure(duration=2e-5, range_v=4.0)
        check("Sinus 1 MHz 2 Vss", abs(m["frequency"] - 1e6) < 1e4 and abs(m["vpp"] - 2.0) < 0.2, str(m))
        gen.set_frequency(1, 1000.0)
        gen.set_amplitude(1, 1.0)
        awg.set_waveform_checked(gen, 1, awg.WAVE_DC)
        awg.set_offset_checked(gen, 1, 0.5)
        m = measure()
        check("DC 0,5 V", abs(m["vmean"] - 0.5) < 0.03 and m["vpp"] < 0.08, str(m))
        check("DC: Amplitude abgelehnt", refused(lambda: awg.set_amplitude_checked(gen, 1, 1.0)) != "")
        gen.set_output(1, False)
        m = measure()
        check("Ausgang AUS: ~0 V", abs(m["vmean"]) < 0.03 and m["vpp"] < 0.08, str(m))
        gen.set_output(1, True)
        awg.set_waveform_checked(gen, 1, awg.WAVE_SINE)
        awg.set_offset_checked(gen, 1, 0.0)
        gen.set_amplitude(1, 0.5)
        gen.set_load(1, 50)
        check("Last 50 Ω: Amplitude zaehlt an der Last (0,25)", abs(gen.get_amplitude(1) - 0.25) < 1e-6
              and gen.get_load() == 50.0)
        m = measure()
        check("Last 50 Ω: am hochohmigen Eingang unveraendert ~0,5 Vss", abs(m["vpp"] - 0.5) < 0.06, str(m))
        gen.set_load(1, awg.HIGH_Z_OHM)
        check("Last zurueck auf hochohmig", gen.get_load() == 1e6 and abs(gen.get_amplitude(1) - 0.5) < 1e-6)
        gen.set_amplitude(1, 1.0)
        gen.set_frequency(1, 2000.0)
        gen.set_param(1, "mod_freq", 100.0)
        gen.set_param(1, "am_depth", 50.0)
        gen.set_mode(1, "AM")
        m = measure(duration=0.02)
        check("AM 50 % (100 Hz) gesetzt und gelesen", gen.get_mode() == "AM" and m["vpp"] > 0.9, str(m))
        gen.set_param(1, "fsk_freq", 500.0)
        gen.set_param(1, "fsk_rate", 100.0)
        gen.set_mode(1, "FSK")
        check("FSK gesetzt", gen.get_mode() == "FSK")
        gen.set_mode(1, "BASE")
        check("zurueck auf Grundsignal", gen.get_mode() == "BASE")
        message = refused(lambda: gen._write("AWG1:FOO 1"))
        check("unbekannter Befehl am Geraet: Wertfehler -113, Verbindung lebt", "-113" in message
              and gen.get_outputs() in ((True,), (False,)), message)
    finally:
        restore(gen, found)
        scope.close()
    final = awg.state_dict(gen)
    check("vorgefundener Zustand wiederhergestellt", _same(final, found), f"{final} != {found}")
    print("    Endzustand:", final)
    gen.close()


def section_hardware_worker(address: str) -> None:
    """Am Geraet ueber den DeviceWorker: Erkennung, Abfrage im Thread waehrend Erfassungen, Ablauf-Aktion,
    ALLE AUS. Danach vorgefundener Zustand (Ausgang) wieder hergestellt."""
    print(f"hardware -- DeviceWorker mit Oszilloskop {address}")
    import numpy as np
    import no_hardware
    no_hardware.block()   # nur das eingetragene Oszilloskop, keine seriellen Geraete, kein PicoScope
    import device_worker as dw
    from scope_api import analysis
    from scope_api.base import AcquireRequest
    from scope_api.unit_mso_adapter import open_unit_mso
    from unit_mso1000hd.driver import device_id_for, normalize_resource
    resource = normalize_resource(address)
    worker = dw.DeviceWorker(simulation_mode=False, scpi_scopes=[address])
    awg_id = awg.device_id_for(device_id_for(resource))
    states: list[dict] = []
    worker.fg_state.connect(lambda d, st: d == awg_id and states.append(st))
    removed: list[str] = []
    worker.device_removed.connect(lambda kind, d: removed.append(d))
    worker._probe_scpiscopes()
    check(f"Erkennung: {awg_id}", spin(15.0, lambda: awg_id in worker._fgs and states), str(list(worker._fgs)))
    if awg_id not in worker._fgs:
        return
    found_on = states[-1]["outputs"][0]
    print("    vorgefunden (Worker):", states[-1])

    # Abfrage im Thread waehrend Erfassungen im selben Prozess (ScopeService-Weg: eigene Verbindung)
    stop = threading.Event()
    results: list = []

    def acquire_loop() -> None:
        scope = open_unit_mso(resource)
        try:
            for duration in (0.005, 0.008, 0.005):
                cap = scope.acquire(AcquireRequest.from_dict({
                    "channels": [{"name": "CH2", "range_v": 2, "coupling": "DC", "probe": 1}],
                    "timing": {"duration_s": duration}, "trigger": {"mode": "auto", "source": "CH2", "level_v": 0.0}}))
                c = cap.channels["CH2"]
                t = (np.arange(c.volts.size) - cap.trigger_index) * cap.sample_interval_s
                results.append(analysis.measure(t, c.volts, ["vpp", "frequency"])[0])
        except Exception as exc:  # noqa: BLE001
            results.append(exc)
        finally:
            scope.close()
            stop.set()

    acquire = threading.Thread(target=acquire_loop)
    t0 = time.monotonic()
    count0, slow = len(states), 0.0
    acquire.start()
    last = time.monotonic()
    while not stop.is_set():
        t_poll = time.monotonic()
        worker._poll_fgs()
        slow = max(slow, time.monotonic() - t_poll)
        spin(0.5)
        last = time.monotonic()
    acquire.join()
    polls = len(states) - count0
    expect_hz = states[-1]["channels"][0]["frequency"]
    check(f"3 Erfassungen waehrend der Abfrage: Kurve unverfaelscht ({expect_hz:g} Hz an CH2)",
          len(results) == 3 and all(isinstance(r, dict) and r.get("frequency")
                                    and abs(r["frequency"] - expect_hz) < expect_hz * 0.02 for r in results),
          str(results))
    check(f"Abfrage lief weiter ({polls} Zustaende in {last - t0:.1f} s), kein Trennen",
          polls >= (last - t0) / 0.5 * 0.5 and awg_id not in removed, f"{polls}, entfernt {removed}")
    check(f"Abfrage blockiert den Worker nicht (laengster Aufruf {slow * 1000:.0f} ms)", slow < 0.05)

    done: list[tuple[bool, str]] = []
    worker.action_completed.connect(lambda ok, message, _v: done.append((ok, message)))
    worker.execute_action(awg_id, "fg", "FG_OUT_ON", 0.0, 1)
    spin(5.0, lambda: done)
    check("Ablauf FG_OUT_ON am Geraet", done and done[-1] == (True, "")
          and spin(3.0, lambda: states[-1]["outputs"] == [True]), str(done))
    finished: list[str] = []
    worker.all_off_finished.connect(finished.append)
    t_off = time.monotonic()
    worker.all_outputs_off("check_unit_mso_awg")
    took = time.monotonic() - t_off
    gen_check = UnitMsoAwg.open(resource)
    try:
        off = gen_check.get_outputs() == (False,)
        check(f"ALLE AUS: Generator aus ({took * 1000:.0f} ms), Rueckmeldung ohne Fehler", off and finished == [""],
              str(finished))
        if found_on:
            gen_check.set_output(1, True)
        check("Ausgang wie vorgefunden", gen_check.get_outputs() == (found_on,))
    finally:
        gen_check.close()
    worker._drop_fg(awg_id, "Pruefung beendet", quiet=True)
    worker._fg_io.shutdown(wait=True)


def restore(gen, state: dict) -> None:
    gen.set_mode(1, "BASE")
    gen.set_load(1, state["load"])
    for field in ("mod_wave", "mod_freq", "am_depth", "fm_dev", "fsk_rate", "fsk_freq", "sweep_type", "sweep_start",
                  "sweep_stop", "sweep_time"):
        if state[field] != "ARB":
            gen.set_param(1, field, state[field])
    channel = state["channels"][0]
    if channel["waveform"] in (awg.WAVE_SQUARE, awg.WAVE_PULSE):
        gen.set_waveform(1, channel["waveform"])
        gen.set_duty(1, channel["duty"])
    else:   # Tastgrad nur in Rechteck setzen (Eigenheit), dann die Form
        gen.set_waveform(1, awg.WAVE_SQUARE)
        gen.set_duty(1, channel["duty"])
    gen.set_waveform(1, channel["waveform"]) if channel["waveform"] in awg.WAVEFORMS else None
    gen.set_frequency(1, channel["frequency"])
    gen.set_amplitude(1, channel["amplitude"])
    gen.set_offset(1, channel["offset"])
    gen.set_mode(1, state["mode"])
    gen.set_output(1, state["outputs"][0])


def _same(a: dict, b: dict) -> bool:
    def norm(state):
        flat = {k: v for k, v in state.items() if k != "channels"}
        flat.update({f"ch_{k}": v for k, v in state["channels"][0].items()})
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in flat.items()}
    return norm(a) == norm(b)


SECTIONS = {"driver": section_driver, "lock": section_lock, "worker": section_worker, "model": section_model,
            "tile": section_tile, "partner": section_partner}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sections", nargs="*")
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--address", default="")
    args = parser.parse_args()
    wanted = [s for s in args.sections if s in SECTIONS] or list(SECTIONS)
    for name in wanted:
        SECTIONS[name]()
    if args.hardware:
        if not args.address:
            print("--hardware braucht --address")
            return 2
        section_hardware(args.address)
        section_hardware_worker(args.address)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FEHLER: " + "; ".join(FAILURES))
        return 1
    print("Alle Pruefungen bestanden." + ("" if args.hardware else " (nur Simulator)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
