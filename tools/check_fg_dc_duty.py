"""Prueft PI-75 a / PI-80 a (1.0.1): Funktionsgenerator JDS2915 in DC und Tastgrad nur bei Puls.

Grundlage ist die Messung am echten JDS2915 vom 17.11.2026 (CH1 ohne Last, PicoScope 10:1), die der Mock
(device-driver/jds66xx/mock.py) jetzt nachbildet:
  * DC: Ausgang = Offset + Amplitude/2; bei Amplitude genau 0 kommt der Offset nur zu ~1/10 an
  * Tastgrad wirkt nur bei Puls (Rechteck bleibt 50 %)

Geprueft wird (nur Mock, offscreen, keine Hardware):
  driver   -- set_*_checked: DC setzt Amplitude 0,01 V, Offset = Spannung, Amplitude in DC und Tastgrad
              ausserhalb Puls abgelehnt, keine stille Formumschaltung
  worker   -- Testablauf (execute_action), Kachel (set_fg_*), Fernsteuerung (execute_remote_action)
  static   -- Ablauf mit Tastgrad bei Rechteck bzw. Amplitude in DC wird vor dem Start abgelehnt, soweit erkennbar
  runtime  -- nicht statisch erkennbar: Schritt scheitert zur Laufzeit
  tile     -- Kachel: in DC nur "Spannung", Frequenz/Tastverhaeltnis gesperrt; Tastverhaeltnis nur bei Puls;
              Preset schickt keine Amplitude in DC und kein Tastverhaeltnis ausserhalb Puls

Aufruf:  python tools/check_fg_dc_duty.py [driver|worker|static|runtime|tile]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LABCONTROL_DATA_DIR"] = tempfile.mkdtemp(prefix="lc-fg-dc-check-")
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from jds66xx import driver as jd  # noqa: E402
from jds66xx.driver import WAVE_DC, WAVE_PULSE, WAVE_SINE, WAVE_SQUARE, FunctionGeneratorValueError  # noqa: E402
from jds66xx.mock import MockJDS66xx  # noqa: E402

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


def raises(call) -> str:
    try:
        call()
    except FunctionGeneratorValueError as exc:
        return str(exc)
    return ""


# -- Mock bildet die Messung nach, Treiber-Helfer ------------------------------------------------------------

def section_driver() -> None:
    print("driver -- Messung im Mock, set_*_checked")
    gen = MockJDS66xx()
    gen.set_waveform(1, WAVE_DC)
    for ampl, offs, expect in ((0.0, 0.0, 0.0), (1.0, 0.0, 0.5), (2.5, 0.0, 1.25), (5.0, 0.0, 2.5),
                               (2.5, 1.0, 2.25), (2.5, 2.5, 3.75), (0.01, 2.5, 2.505), (0.1, 2.5, 2.55)):
        gen.set_amplitude(1, ampl)
        gen.set_offset(1, offs)
        got = gen.output_dc_voltage(1)
        check(f"Mock DC: Amplitude {ampl:g} V, Offset {offs:g} V -> {expect:g} V (Offset + Amplitude/2)",
              abs(got - expect) < 1e-6, str(got))
    gen.set_amplitude(1, 0.0)
    gen.set_offset(1, 2.5)
    check("Mock DC Sonderfall Amplitude 0: Offset 2,5 V -> etwa 0,22 V", abs(gen.output_dc_voltage(1) - 0.22) < 1e-3,
          str(gen.output_dc_voltage(1)))
    gen.set_waveform(1, WAVE_SQUARE)
    gen.set_duty(1, 10.0)
    check("Mock: Rechteck mit 10 % -> 50 % am Ausgang, Register 10 % angenommen",
          gen.effective_duty(1) == 50.0 and gen.get_duty(1) == 10.0, str(gen.effective_duty(1)))
    gen.set_waveform(1, WAVE_PULSE)
    gen.set_duty(1, 99.0)
    check("Mock: Puls 99 % -> 99 %", gen.effective_duty(1) == 99.0)

    gen = MockJDS66xx()
    gen.set_amplitude(1, 2.5)
    jd.set_waveform_checked(gen, 1, WAVE_DC)
    check("set_waveform_checked(DC): Amplitude 0,01 V (nicht 0)", gen.get_amplitude(1) == jd.DC_AMPLITUDE_V == 0.01,
          str(gen.get_amplitude(1)))
    jd.set_offset_checked(gen, 1, 3.3)
    check("set_offset_checked in DC: Ausgang = Spannung + 5 mV", abs(gen.output_dc_voltage(1) - 3.305) < 1e-6
          and jd.DC_RESIDUAL_V == 0.005, str(gen.output_dc_voltage(1)))
    gen.set_amplitude(1, 2.5)   # am Geraet verstellt
    jd.set_offset_checked(gen, 1, 5.0)
    check("set_offset_checked in DC: verstellte Amplitude wird auf 0,01 V zurueckgesetzt",
          gen.get_amplitude(1) == 0.01 and abs(gen.output_dc_voltage(1) - 5.005) < 1e-6, str(gen.get_amplitude(1)))
    message = raises(lambda: jd.set_amplitude_checked(gen, 1, 2.0))
    check("set_amplitude_checked in DC abgelehnt, Amplitude bleibt", "DC" in message and gen.get_amplitude(1) == 0.01,
          message)
    gen.set_waveform(1, WAVE_SQUARE)
    gen.set_duty(1, 50.0)
    message = raises(lambda: jd.set_duty_checked(gen, 1, 10.0))
    check("set_duty_checked bei Rechteck abgelehnt, Form bleibt Rechteck, Register unveraendert",
          "Puls" in message and "Rechteck" in message and gen.get_waveform(1) == WAVE_SQUARE
          and gen.get_duty(1) == 50.0, message)
    jd.set_waveform_checked(gen, 1, WAVE_PULSE)
    jd.set_duty_checked(gen, 1, 10.0)
    check("set_duty_checked bei Puls: wirkt", gen.effective_duty(1) == 10.0)
    jd.set_amplitude_checked(gen, 1, 3.0)
    check("set_amplitude_checked ausserhalb DC: geht", gen.get_amplitude(1) == 3.0)
    check("Kanal 2 unabhaengig (Sinus): Tastgrad abgelehnt", "Kanal 2" in raises(lambda: jd.set_duty_checked(gen, 2, 20)))


# -- Worker: Ablauf, Kachel, Fernsteuerung -----------------------------------------------------------------------

def make_worker():
    import device_worker as dw
    dw.KoradKEL102.discover_ports = staticmethod(lambda: [])
    dw.HCS34xx.discover_ports = staticmethod(lambda: [])
    dw.MicroHIL.discover = staticmethod(lambda: None)
    dw.JDS66xx.discover_ports = staticmethod(lambda: [])
    dw.picoscope_usb_present = lambda: False
    worker = dw.DeviceWorker(simulation_mode=False)
    worker._add_mock_fg()
    return worker, dw.SIM_FG_ID, worker._fgs[dw.SIM_FG_ID]


def section_worker() -> None:
    print("worker -- Testablauf, Kachel, Fernsteuerung")
    worker, fg_id, gen = make_worker()
    results: list[tuple[bool, str]] = []
    remote: list[tuple[int, bool, str]] = []
    worker.action_completed.connect(lambda ok, message, _v: results.append((ok, message)))
    worker.remote_action_completed.connect(lambda rid, ok, message, _v: remote.append((rid, ok, message)))

    def run(action: str, value: float = 0.0, channel: int = 1) -> tuple[bool, str]:
        worker.execute_action(fg_id, "fg", action, value, channel)
        return results[-1]

    gen.set_amplitude(1, 2.5)
    check("Ablauf FG_WAVE_DC ok", run("FG_WAVE_DC") == (True, ""), str(results[-1]))
    check("... Amplitude automatisch 0,01 V", gen.get_amplitude(1) == 0.01, str(gen.get_amplitude(1)))
    check("Ablauf FG_OFFS 3,3 V in DC: Ausgang 3,305 V", run("FG_OFFS", 3.3) == (True, "")
          and abs(gen.output_dc_voltage(1) - 3.305) < 1e-6, str(gen.output_dc_voltage(1)))
    ok, message = run("FG_AMPL", 2.0)
    check("Ablauf FG_AMPL in DC: Schritt FAIL mit Meldung, Amplitude bleibt", not ok and "DC" in message
          and gen.get_amplitude(1) == 0.01, message)
    states: list[dict] = []
    worker.fg_state.connect(lambda _d, st: states.append(st))
    run("FG_WAVE_SQUARE")
    ok, message = run("FG_DUTY", 10.0)
    check("Ablauf FG_DUTY bei Rechteck: FAIL mit Meldung, keine stille Formumschaltung",
          not ok and "Puls" in message and gen.get_waveform(1) == WAVE_SQUARE, message)
    run("FG_WAVE_PULSE")
    check("Ablauf FG_DUTY bei Puls: ok, wirkt", run("FG_DUTY", 25.0) == (True, "") and gen.effective_duty(1) == 25.0,
          str(results[-1]))

    # Kachel
    worker.set_fg_waveform(fg_id, 2, WAVE_SQUARE)
    worker.set_fg_duty(fg_id, 2, 80.0)
    check("Kachel: Tastverhaeltnis bei Rechteck wirkungslos (abgelehnt)", gen.get_duty(2) != 80.0
          and gen.get_waveform(2) == WAVE_SQUARE, str(gen.get_duty(2)))
    worker.set_fg_waveform(fg_id, 2, WAVE_DC)
    check("Kachel: Form DC -> Amplitude 0,01 V", gen.get_amplitude(2) == 0.01, str(gen.get_amplitude(2)))
    worker.set_fg_amplitude(fg_id, 2, 4.0)
    check("Kachel: Amplitude in DC abgelehnt", gen.get_amplitude(2) == 0.01)
    worker.set_fg_offset(fg_id, 2, -1.5)
    check("Kachel: Spannung -1,5 V in DC", abs(gen.output_dc_voltage(2) - (-1.495)) < 1e-6,
          str(gen.output_dc_voltage(2)))
    check("Rueckmeldung nach der Form enthaelt die Amplitude", states and states[-1]["channels"][1]["amplitude"]
          == 0.01, str(states[-1] if states else None))

    # Fernsteuerung (MCP/REST/Verbund)
    worker.set_fg_waveform(fg_id, 1, WAVE_SINE)
    worker.execute_remote_action(7, fg_id, "fg", "FG_DUTY", 30.0, 1)
    check("Fernsteuerung FG_DUTY bei Sinus: Fehler mit Meldung", remote and remote[-1][0] == 7
          and not remote[-1][1] and "Puls" in remote[-1][2], str(remote))

    # REST-Antwort: share_remote macht daraus device_error mit Meldung
    from share_remote import RemoteBridge
    bridge = RemoteBridge()
    pending = bridge._pending

    class Waiter:
        result = None

        class event:
            @staticmethod
            def set():
                pass
    pending[42] = Waiter()
    bridge.on_worker_result(42, False, remote[-1][2], 0.0)
    result = pending[42].result
    check("REST/MCP: device_error mit der Meldung", result.status == "error" and result.code == "device_error"
          and "Puls" in result.message, f"{result.status} {result.code} {result.message}")


# -- Statisch vor dem Start ------------------------------------------------------------------------------------

def fg(action: str, channel: int = 1, value: float = 0.0, device_id: str = "", **kw):
    from testcase_model import TestStep
    return TestStep(device_kind="fg", device_id=device_id, action=action, value=value, hil_channel=channel, **kw)


def section_static() -> None:
    print("static -- Ablauf vor dem Start pruefen")
    from testcase_model import TestStep, fg_waveform_refusal

    def ctl(step_type, **kw):
        return TestStep(step_type=step_type, **kw)

    cases = [
        ("Rechteck, dann Tastgrad -> abgelehnt", [fg("FG_WAVE_SQUARE"), fg("FG_DUTY", value=10)], 1),
        ("Puls, dann Tastgrad -> ok", [fg("FG_WAVE_PULSE"), fg("FG_DUTY", value=10)], None),
        ("Tastgrad ohne bekannte Form -> erst zur Laufzeit", [fg("FG_DUTY", value=10)], None),
        ("Sinus, Puls, Tastgrad -> ok", [fg("FG_WAVE_SINE"), fg("FG_WAVE_PULSE"), fg("FG_DUTY")], None),
        ("Puls, Rechteck, Tastgrad -> abgelehnt", [fg("FG_WAVE_PULSE"), fg("FG_WAVE_SQUARE"), fg("FG_DUTY")], 2),
        ("anderer Kanal zaehlt nicht", [fg("FG_WAVE_SQUARE", 2), fg("FG_DUTY", 1)], None),
        ("anderes Geraet zaehlt nicht", [fg("FG_WAVE_SQUARE", device_id="fg:A"), fg("FG_DUTY", device_id="fg:B")],
         None),
        ("DC, dann Amplitude -> abgelehnt", [fg("FG_WAVE_DC"), fg("FG_OFFS", value=2.5), fg("FG_AMPL", value=1)], 2),
        ("Amplitude vor DC -> ok", [fg("FG_AMPL", value=1), fg("FG_WAVE_DC"), fg("FG_OFFS", value=2.5)], None),
        ("Blockgrenze loescht das Wissen (Schleife)",
         [fg("FG_WAVE_SQUARE"), ctl("loop", loop_count=2), fg("FG_DUTY"), fg("FG_WAVE_PULSE"), ctl("end")], None),
        ("innerhalb eines Blocks erkennbar",
         [ctl("loop", loop_count=2), fg("FG_WAVE_SQUARE"), fg("FG_DUTY"), ctl("end")], 2),
        ("deaktivierter Schritt zaehlt nicht", [fg("FG_WAVE_SQUARE", enabled=False), fg("FG_DUTY")], None),
        ("deaktivierter Block zaehlt nicht",
         [ctl("loop", loop_count=2, enabled=False), fg("FG_WAVE_SQUARE"), fg("FG_DUTY"), ctl("end")], None),
    ]
    for name, steps, expect in cases:
        got = fg_waveform_refusal(steps)
        check(name, (got is None) if expect is None else (got is not None and got[0] == expect), str(got))
    got = fg_waveform_refusal([fg("FG_WAVE_SQUARE"), fg("FG_DUTY", value=10)])
    check("Meldung nennt Schritt, Puls und die Form", got and "Schritt 2" in got[1] and "Puls" in got[1]
          and "Rechteck" in got[1], str(got))

    # ueber RunSession.refusal (Fenster, Netzwerk, Kommandozeile)
    session, _worker = make_session()
    message = session.refusal([fg("FG_WAVE_SQUARE"), fg("FG_DUTY", value=10)])
    check("RunSession.refusal lehnt ab", "Puls" in message, message)
    check("RunSession.refusal: Puls ok", session.refusal([fg("FG_WAVE_PULSE"), fg("FG_DUTY", value=10)]) == "")


def make_session():
    from run_record import RunRecorder
    from run_session import DeviceDirectory, Interlock, RunSession
    from safety import SafetyMonitor
    worker, fg_id, _gen = make_worker()
    directory = DeviceDirectory()
    directory.set_online("fg", fg_id, True)
    recorder = RunRecorder()
    recorder.on_device_known("fg", fg_id, "Generator")
    session = RunSession(worker, SafetyMonitor({}), recorder, directory, Interlock([], directory.is_online))
    return session, worker


def section_runtime() -> None:
    print("runtime -- nicht statisch erkennbar: Schritt scheitert im Lauf")
    from testcase_model import TestStep
    session, worker = make_session()
    gen = next(iter(worker._fgs.values()))
    gen.set_waveform(1, WAVE_SQUARE)
    failed: list[tuple[int, str]] = []
    session.runner.step_failed.connect(lambda index, message: failed.append((index, message)))
    steps = [TestStep(step_type="wait", duration=0.05), fg("FG_DUTY", value=10.0),
             TestStep(step_type="wait", duration=0.05)]
    check("statisch nicht erkennbar -> Start erlaubt", session.refusal(steps) == "", session.refusal(steps))
    session.start(steps, "tastgrad")
    spin(5.0, lambda: not session.runner.is_running())
    check("Lauf: FG_DUTY bei Rechteck -> Schritt 2 FAIL mit Meldung", failed and failed[0][0] == 1
          and "Puls" in failed[0][1], str(failed))
    check("Form bleibt Rechteck", gen.get_waveform(1) == WAVE_SQUARE)


# -- Kachel ------------------------------------------------------------------------------------------------------

def section_tile() -> None:
    print("tile -- Kachel: DC nur Spannung, Tastverhaeltnis nur bei Puls")
    from control_tab import ControlTab, FgControlGroup
    from presets import PresetStore

    def state(code1: int, code2: int = WAVE_SINE) -> dict:
        ch = {"frequency": 1000.0, "amplitude": 1.0, "offset": 0.0, "duty": 50.0}
        return {"outputs": [False, False], "phase": 0.0,
                "channels": [dict(ch, waveform=code1), dict(ch, waveform=code2)]}

    tile = FgControlGroup("fg:1", "Generator")
    before = tile.channel_rows(1)
    check("unbekannte Form: alles wie gehabt", before["amplitude_visible"] and before["duty_enabled"]
          and before["frequency_enabled"], str(before))
    tile.set_state(state(WAVE_DC))
    rows = tile.channel_rows(1)
    check("DC: Amplitude ausgeblendet, Offset heisst 'Spannung:'", not rows["amplitude_visible"]
          and rows["offset_label"] == "Spannung:", str(rows))
    check("DC: Frequenz und Tastverhaeltnis gesperrt", not rows["frequency_enabled"] and not rows["duty_enabled"],
          str(rows))
    check("Kanal 2 (Sinus) unberuehrt bis auf Tastverhaeltnis", tile.channel_rows(2)["amplitude_visible"]
          and not tile.channel_rows(2)["duty_enabled"], str(tile.channel_rows(2)))
    tile.set_state(state(WAVE_SQUARE))
    rows = tile.channel_rows(1)
    check("Rechteck: Amplitude/Offset wieder da, Tastverhaeltnis gesperrt", rows["amplitude_visible"]
          and rows["offset_label"] == "Offset:" and not rows["duty_enabled"] and rows["frequency_enabled"], str(rows))
    tile.set_state(state(WAVE_PULSE))
    check("Puls: Tastverhaeltnis bedienbar", tile.channel_rows(1)["duty_enabled"])

    # Sperre durch den Verbund-Leitstand und Aufheben: Tastverhaeltnis folgt danach wieder der Form
    tab = ControlTab(PresetStore())
    tab.on_device_known("fg", "fg:2", "Generator 2")
    section = tab._sections["fg:2"]
    section.set_state(state(WAVE_PULSE))
    tab.set_lease("fg:2", "belegt durch PC")
    section.set_state(state(WAVE_SQUARE))
    tab.set_lease("fg:2", "")
    check("nach Aufheben der Sperre: Tastverhaeltnis bei Rechteck gesperrt",
          not section.channel_rows(1)["duty_enabled"], str(section.channel_rows(1)))

    # Preset: keine Amplitude in DC, kein Tastverhaeltnis ausserhalb Puls
    sent: list[tuple[str, int]] = []
    tile.set_amplitude.connect(lambda _d, ch, _v: sent.append(("ampl", ch)))
    tile.set_duty.connect(lambda _d, ch, _v: sent.append(("duty", ch)))
    tile.set_offset.connect(lambda _d, ch, _v: sent.append(("offs", ch)))
    channel = {"frequency": 1000.0, "amplitude": 2.0, "offset": 1.0, "duty": 20.0, "output_on": None}
    tile.apply_state({"phase": 0.0, "channels": [dict(channel, waveform=WAVE_DC), dict(channel, waveform=WAVE_SQUARE)]})
    check("Preset: DC ohne Amplitude, mit Spannung; Rechteck ohne Tastverhaeltnis",
          ("ampl", 1) not in sent and ("offs", 1) in sent and ("duty", 2) not in sent and ("ampl", 2) in sent
          and ("duty", 1) not in sent, str(sent))
    sent.clear()
    tile.apply_state({"phase": 0.0, "channels": [dict(channel, waveform=WAVE_PULSE)]})
    check("Preset: Puls mit Tastverhaeltnis", ("duty", 1) in sent, str(sent))

    # Uebersicht: DC zeigt die Spannung
    from dashboard import DashboardWidget
    dash = DashboardWidget()
    dash.on_device_known("fg", "fg:1", "Generator")
    st = state(WAVE_DC)
    st["outputs"] = [True, False]
    st["channels"][0]["offset"] = 3.3
    dash.update_fg("fg:1", st)
    text = dash._panels["fg:1"]._value_labels["fg_ch1"].text()
    check("Uebersicht: DC zeigt die Spannung", text == "DC  3.30 V", text)


SECTIONS = {"driver": section_driver, "worker": section_worker, "static": section_static,
            "runtime": section_runtime, "tile": section_tile}


def main() -> int:
    wanted = [a for a in sys.argv[1:] if a in SECTIONS] or list(SECTIONS)
    for name in wanted:
        SECTIONS[name]()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FEHLER: " + "; ".join(FAILURES))
        return 1
    print("Alle Pruefungen bestanden. (nur Mock -- der Geraetebefund selbst ist am JDS2915 gemessen)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
