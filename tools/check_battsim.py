"""Prueft den Batterie-Simulator-Treiber (device-driver/battsim) samt CAN-Knoten-
Schicht (can_bus/node.py).

Ohne Argumente: gegen den nachgebildeten Simulator (battsim.mock.SimulatedBattSim)
an einem MockCanBus -- der echte Treiber- und Scheduler-Code laeuft mit.

Mit --hardware: gegen das echte Geraet an einem CAN-Interface. Standard ist nur
LESEN (Status und Istwerte, es wird nichts gesendet). Erst --remote schaltet die
Fernsteuerung ein und faehrt eine kurze Folge (Zellen auf --volts, Ausgang ein,
Zelle 5 aus, Ausgang aus, Fernsteuerung aus) -- nur mit Freigabe des Nutzers und
ohne angeschlossenen Pruefling oder mit einem, der das vertraegt.

    python tools/check_battsim.py
    python tools/check_battsim.py --hardware --interface vector --channel 59177:1
    python tools/check_battsim.py --hardware --interface vector --channel 59177:1 --remote --volts 3.7 --hold 15

--hold <s> haelt jede Stufe der Folge (alle Zellen ein, Zelle 5 aus) zum Ablesen am Multimeter.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device-driver"))

from battsim.driver import (  # noqa: E402
    CELL_COUNT, CONTROL_ID, CELL_CONTROL_IDS, STATUS_FAIL, STATUS_REMOTE, BattSim, BattSimValueError,
)
from battsim.mock import SimulatedBattSim  # noqa: E402
from can_bus.mock import MockCanBus  # noqa: E402
from can_bus.node import CanScheduler  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'}    {label}")
    if not cond:
        FAILS.append(label)


def pump(bus, node: BattSim, seconds: float) -> None:
    """Empfang wie im DeviceWorker-Poll: recv() leeren, Frames an den Knoten."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while True:
            frame = bus.recv(timeout=0.0)
            if frame is None:
                break
            if frame.arbitration_id in node.rx_ids:
                node.on_frame(frame)
        time.sleep(0.02)


def sent_ids(bus: MockCanBus, since: float) -> list:
    return [f for f in list(bus.sent_frames) if f.timestamp >= since]


def run_mock() -> None:
    print("Batterie-Simulator gegen Nachbildung")
    bus = MockCanBus()
    sim = SimulatedBattSim(seed=1)
    bus.attach_peer(sim)
    node = BattSim()
    sched = CanScheduler(bus, name="test")
    sched.attach(node)

    pump(bus, node, 0.6)   # > LISTEN_BEFORE_REMOTE_S (PI-28)
    st = node.get_state()
    check(st.online, "online, sobald 0x120 kommt")
    check(st.status == 1, f"Startzustand STANDBY (ist {st.status_name})")
    check(not bus.sent_frames, "ohne Fernsteuerung wird nichts gesendet")

    node.set_remote(True)
    pump(bus, node, 0.5)
    st = node.get_state()
    check(sim.state == STATUS_REMOTE and sim.sub == "STOP", "Fernsteuerung ein -> REMOTE/STOP")
    check(st.status == STATUS_REMOTE and st.output_on is False, "Treiber sieht REMOTE, Ausgang aus")

    node.set_all_voltages(3.7)
    node.set_all_enabled(True)
    node.set_output(True)
    pump(bus, node, 0.6)
    st = node.get_state()
    check(sim.sub == "RUN" and st.output_on is True, "Ausgang ein -> RUN")
    volts = [c.voltage_v for c in st.cells]
    check(all(v is not None and abs(v - 3.7) < 0.002 for v in volts), f"14 Zellen bei 3,7 V ({volts[0]})")
    check(all(c.enabled for c in st.cells), "Zellstatus 0 wird als 'ein' gelesen")
    check(st.stack_voltage_v is not None and abs(st.stack_voltage_v - 14 * 3.7) < 0.03,
          f"Stapelspannung {st.stack_voltage_v}")

    node.set_cell_enabled(5, False)
    node.set_cell_voltage(6, 2.5)
    pump(bus, node, 0.4)
    st = node.get_state()
    check(st.cells[4].enabled is False and st.cells[4].voltage_v == 0.0, "Zelle 5 aus -> 0 V, Status aus")
    check(sim._enable_raw[4] == 0, "Zelle aus wird als 0 gesendet (nicht 1, siehe can-verhalten.md)")
    check(abs(st.cells[5].voltage_v - 2.5) < 0.002, "Zelle 6 auf 2,5 V")

    try:
        node.set_cell_voltage(1, 4.4)
        check(False, "4,4 V ueber Grenzwert 4,30 V abgelehnt")
    except BattSimValueError:
        check(True, "4,4 V ueber Grenzwert 4,30 V abgelehnt")
    node.set_voltage_limit(4.5)
    node.set_cell_voltage(1, 4.4)
    check(True, "nach Grenzwert 4,5 V sind 4,4 V erlaubt")
    for bad in (0, CELL_COUNT + 1):
        try:
            node.set_cell_voltage(bad, 1.0)
            check(False, f"Zelle {bad} abgelehnt")
        except BattSimValueError:
            check(True, f"Zelle {bad} abgelehnt")

    # Takt und Alive-Zaehler von 0x130 und 0x100
    t0 = bus.sent_frames[-1].timestamp
    pump(bus, node, 1.6)
    ctrl = [f for f in sent_ids(bus, t0) if f.arbitration_id == CONTROL_ID]
    gaps = [b.timestamp - a.timestamp for a, b in zip(ctrl, ctrl[1:])]
    check(len(ctrl) >= 13 and max(gaps) < 0.16, f"0x130 etwa alle 100 ms ({len(ctrl)} in 1,6 s, max {max(gaps):.3f} s)")
    alive = [f.data[0] & 0x0F for f in ctrl]
    check(all(1 <= a <= 14 for a in alive), f"Alive 0x130 nur 1..14 ({sorted(set(alive))})")
    check(all(b == a % 14 + 1 for a, b in zip(alive, alive[1:])), "Alive 0x130 steigt um 1, nach 14 folgt 1")
    cells = [f for f in sent_ids(bus, t0) if f.arbitration_id == CELL_CONTROL_IDS[0]]
    check(1 <= len(cells) <= 3, f"0x100 etwa jede Sekunde ({len(cells)} in 1,6 s)")

    # Absturz von LabControl: Senden endet ohne Abschiedsframes
    sched.stop(send_shutdown=False)
    t_stop = time.monotonic()
    while time.monotonic() - t_stop < 8.0 and sim.state != STATUS_FAIL:
        pump(bus, node, 0.2)
    dt = time.monotonic() - t_stop
    check(sim.state == STATUS_FAIL, f"ohne 0x130 -> FAIL nach {dt:.1f} s (Firmware 4,5-6 s)")
    pump(bus, node, 0.3)   # naechste Statusbotschaft abwarten
    st = node.get_state()
    check(st.failed and st.output_on is False, "Treiber sieht FAIL, Ausgang aus")
    check(all(c.voltage_v == 0.0 for c in st.cells), "alle Zellen 0 V in FAIL")

    sched = CanScheduler(bus, name="test2")
    sched.attach(node)
    node.reset_fail()
    pump(bus, node, 1.2)
    check(sim.state == STATUS_REMOTE and sim.sub == "STOP", "Zuruecksetzen -> REMOTE/STOP")
    node.set_output(True)
    pump(bus, node, 0.4)
    check(sim.sub == "RUN", "danach Ausgang wieder ein")

    node.set_remote(False)
    pump(bus, node, 1.0)
    check(sim.state == 1, "Fernsteuerung aus -> STANDBY")
    t1 = time.monotonic() - bus._start
    pump(bus, node, 0.5)
    check(not [f for f in sent_ids(bus, t1)], "danach sendet der Treiber nichts mehr")
    try:
        node.set_output(True)
        check(False, "Ausgang ohne Fernsteuerung abgelehnt")
    except BattSimValueError:
        check(True, "Ausgang ohne Fernsteuerung abgelehnt")

    node.set_remote(True)
    node.set_output(True)
    pump(bus, node, 0.5)
    sched.stop(send_shutdown=True)
    pump(bus, node, 0.3)
    check(sim.state == 1 and not sim.pwm_enabled, "geordnetes Ende (Shutdown-Frames) -> STANDBY")

    sim.responding = False
    pump(bus, node, 1.3)
    check(not node.get_state().online, "Geraet antwortet nicht -> offline nach 1 s")


def run_mock_v2() -> None:
    """Neue Firmware (BattSimFW, BattSimV2.dbc): 0x121 mit FAIL-Grund, Regler, Strom, Version; Kanalstatus 4."""
    print()
    print("Batterie-Simulator, neue Firmware (V2) gegen Nachbildung")
    bus = MockCanBus()
    sim = SimulatedBattSim(seed=2, firmware="v2")
    bus.attach_peer(sim)
    node = BattSim()
    sched = CanScheduler(bus, name="testv2")
    sched.attach(node)

    pump(bus, node, 0.6)
    st = node.get_state()
    check(st.online and st.fail_reason == 0 and st.fail_reason_name == "keiner", "0x121 kommt, FAIL-Grund 0 'keiner'")
    check(st.controller_on is True, "Regler ein aus 0x121")
    check(st.fw_version == "0.3.1", f"Firmware-Version aus 0x121 ({st.fw_version})")
    check(st.supply_current_a is not None and st.supply_current_a < 0.2 and st.overload is False and st.limit_active is False,
          f"Versorgungsstrom {st.supply_current_a} A, keine Ueberlast, kein LIMIT")

    node.set_remote(True)
    node.set_voltage_limit(5.0)
    node.set_all_voltages(3.7)
    node.set_cell_voltage(1, 0.5)     # unter dem Pegelboden 0,7 V
    node.set_cell_voltage(2, 0.7)     # genau der Boden: erreichbar
    node.set_all_enabled(True)
    node.set_output(True)
    pump(bus, node, 0.8)
    st = node.get_state()
    c1, c2, c3 = st.cells[0], st.cells[1], st.cells[2]
    check(c1.status == 4 and c1.status_name == "OL" and c1.below_floor and c1.enabled is True and not c1.setpoint_error,
          f"Zelle 1 (0,5 V): Status 4 '{c1.status_name}' (OL), freigegeben, kein Sollwertfehler")
    check(abs(c1.voltage_v - 0.7) < 0.002, f"Zelle 1 liegt bei ihrem Boden ({c1.voltage_v} V)")
    check(c2.status == 2 and not c2.below_floor and c3.status == 2 and c3.status_name == "ein",
          "Zelle 2 (0,7 V) und 3 (3,7 V): Status 2 'ein'")
    check(sim.sub == "RUN" and st.fail_reason == 0, "kein FAIL durch den Kanalstatus 4")
    node.set_cell_enabled(3, False)
    pump(bus, node, 0.5)
    c3 = node.get_state().cells[2]
    check(c3.status == 1 and c3.enabled is False and c3.status_name == "aus", "Zelle 3 aus: Status 1 'aus'")

    # FAIL-Gruende 5 und 6 (die Firmware loest sie aus, die Nachbildung auf Anforderung), Anzeige und Rueckweg
    for reason, name in ((5, "Strommessung ungültig"), (6, "Überspannung Zelle")):
        sim.inject_fail(reason)
        pump(bus, node, 0.5)
        st = node.get_state()
        check(st.failed and st.fail_reason == reason and st.fail_reason_name == name and st.output_on is False,
              f"FAIL-Grund {reason}: '{st.fail_reason_name}', Ausgang aus")
        check(all(c.voltage_v == 0.0 for c in st.cells), f"Grund {reason}: alle Zellen 0 V")
        node.reset_fail()
        pump(bus, node, 1.2)
        st = node.get_state()
        check(st.status == STATUS_REMOTE and st.fail_reason == 0, f"Zuruecksetzen nach Grund {reason} -> REMOTE/STOP, Grund 0")
        node.set_output(True)
        pump(bus, node, 0.5)

    # Ueberlast: FAIL Grund 3 aus jedem Zustand, Zuruecksetzen erst ohne Ueberlast
    sim.overload = True
    pump(bus, node, 0.5)
    st = node.get_state()
    check(st.failed and st.fail_reason == 3 and st.overload is True and st.fail_reason_name == "Überlast 48 V",
          "Ueberlast: FAIL Grund 3, 'overload' aus 0x121")
    sim.overload = False
    node.reset_fail()
    pump(bus, node, 1.2)
    check(node.get_state().fail_reason == 0, "ohne Ueberlast: zurueckgesetzt")
    node.set_output(True)
    pump(bus, node, 0.5)

    # Ausfall von 0x130: FAIL Grund 1 nach 300 ms (neue Firmware)
    sched.stop(send_shutdown=False)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 3.0 and sim.state != STATUS_FAIL:
        pump(bus, node, 0.05)
    check(sim.state == STATUS_FAIL and sim.fail_reason == 1, f"ohne 0x130: FAIL Grund 1 nach {time.monotonic() - t0:.1f} s")
    pump(bus, node, 0.3)
    st = node.get_state()
    check(st.failed and st.fail_reason == 1 and st.fail_reason_name == "Timeout 0x130", "Treiber zeigt 'Timeout 0x130'")

    # Gegenprobe alte Firmware: kein 0x121, die Felder bleiben leer
    bus2 = MockCanBus()
    old = SimulatedBattSim(seed=3)
    bus2.attach_peer(old)
    node2 = BattSim()
    sched2 = CanScheduler(bus2, name="testv1")
    sched2.attach(node2)
    pump(bus2, node2, 0.5)
    st2 = node2.get_state()
    check(st2.online and st2.fail_reason is None and st2.controller_on is None and st2.fw_version is None
          and st2.supply_current_a is None, "alte Firmware: keine 0x121-Felder (None)")
    sched2.stop(send_shutdown=False)


def run_single_sender() -> None:
    """PI-28: nur ein Sender fuer 0x130. Ein fremdes 0x130 (z. B. LabControl am anderen Rechner) sperrt
    "Fernsteuerung EIN"; vorher wird LISTEN_BEFORE_REMOTE_S lang mitgehoert."""
    print()
    print("Nur ein 0x130-Sender (PI-28)")
    from battsim.driver import FOREIGN_SENDER_HOLD_S
    from can_bus.driver import CanFrame
    bus = MockCanBus()
    sim = SimulatedBattSim(seed=4, firmware="v2")
    bus.attach_peer(sim)
    node = BattSim()
    sched = CanScheduler(bus, name="pi28")
    sched.attach(node)
    pump(bus, node, 0.2)

    def refused(fragment: str) -> tuple[bool, str]:
        try:
            node.set_remote(True)
        except BattSimValueError as exc:
            return fragment in str(exc), str(exc)
        return False, "angenommen"

    ok, msg = refused("mitgehört")
    check(ok and node.get_state().remote_requested is False, f"nach 0,2 s noch abgelehnt: {msg}")

    def foreign_traffic(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            bus._rx_queue.append(CanFrame(CONTROL_ID, bytes([0x21, 0x0A]), False, 0.0))
            pump(bus, node, 0.1)

    foreign_traffic(0.6)
    st = node.get_state()
    check(st.foreign_sender, "fremdes 0x130 erkannt (foreign_sender)")
    ok, msg = refused("anderer Teilnehmer")
    check(ok, f"Fernsteuerung EIN bei fremdem Sender abgelehnt: {msg}")
    check(not [f for f in bus.sent_frames if f.arbitration_id == CONTROL_ID], "dabei kein eigenes 0x130 gesendet")
    pump(bus, node, FOREIGN_SENDER_HOLD_S + 0.2)
    check(not node.get_state().foreign_sender, "fremder Sender still -> nach 1 s wieder frei")
    node.set_remote(True)
    pump(bus, node, 0.4)
    check(sim.state == STATUS_REMOTE, "danach Fernsteuerung EIN angenommen")
    # Fremder Sender taucht WAEHREND der eigenen Fernsteuerung auf: angezeigt, eigenes Senden bleibt
    foreign_traffic(0.3)
    st = node.get_state()
    check(st.foreign_sender and st.remote_requested, "fremder Sender waehrend eigener Fernsteuerung wird angezeigt")
    node.set_remote(True)   # erneutes EIN bei schon eingeschalteter Fernsteuerung: keine Ablehnung
    check(True, "erneutes EIN bei eigener Fernsteuerung bleibt erlaubt")
    sched.stop(send_shutdown=True)


def run_worker() -> None:
    """Derselbe Weg wie in der App: DeviceWorker im Simulationsmodus, der
    nachgebildete Simulator haengt am simulierten CAN-Bus."""
    print()
    print("DeviceWorker im Simulationsmodus")
    sys.path.insert(0, str(ROOT / "lab_gui"))
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    import device_worker as dw

    worker = dw.DeviceWorker(simulation_mode=True)
    # Keine echte Hardware anfassen: start() sucht sonst auch Netzteile/Lasten an
    # den COM-Ports -- und ein frisch verbundenes Netzteil wird auf 0 A gesetzt
    # (Sicherheits-Fix in _reconnect_psus). Nur der simulierte CAN-Bus bleibt.
    for name in ("_reconnect_loads", "_reconnect_psus", "_reconnect_hils", "_reconnect_fgs",
                 "_reconnect_picoscope", "_probe_scpiscopes"):
        setattr(worker, name, lambda: None)
    seen: dict = {"online": None, "state": None, "added": []}
    worker.battsim_connected.connect(lambda d, on: seen.__setitem__("online", (d, on)))
    worker.battsim_state.connect(lambda d, st: seen.__setitem__("state", st))
    worker.device_added.connect(lambda kind, d: seen["added"].append((kind, d)))

    def spin(seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    worker.start()
    spin(1.0)
    sid = dw.SIM_BATTSIM_ID
    check(seen["online"] == (sid, True), f"{sid} meldet sich als verbunden")
    check(("battsim", sid) in seen["added"], "device_added('battsim', ...) fuer die Registry")

    def act(action, value=0.0, channel=0):
        return worker._dispatch_action(sid, "battsim", action, value, channel)

    check(act("BS_OUT_ON")[0] is False, "Ausgang ohne Fernsteuerung abgelehnt (Testablauf-Weg)")
    check(act("BS_REMOTE_ON")[0], "BS_REMOTE_ON")
    check(act("BS_ALL_VOLT", 3.6)[0] and act("BS_ALL_ON")[0] and act("BS_OUT_ON")[0], "Alle 3,6 V, ein, Ausgang ein")
    spin(1.0)
    st = seen["state"]
    check(st is not None and st["output_on"] is True and st["status_name"] == "Fernsteuerung",
          f"Zustand {st and st['status_name']}, Ausgang {st and st['output_on']}")
    check(st["fail_reason"] == 0 and st["controller_on"] is True and st["fw_version"] == "0.3.1"
          and st["cells"][0]["status_name"] == "ein", "Zustands-Dict: FAIL-Grund, Regler, Firmware, Kanalstatus")
    # Weg bis zur Live-Anzeige (Netzwerk-Freigabe, ESP32-Display) und zur Kachel
    from live_state import LiveState
    live = LiveState()
    live.on_battsim_state(sid, st)
    fields = live.snapshot()["devices"][sid]["fields"]
    check(fields.get("fail_reason") == "keiner" and fields.get("controller") == 1
          and fields.get("cell1_status") == "ein" and fields.get("fw_version") == "0.3.1",
          f"LiveState: FAIL-Grund, Regler, Kanalstatus, Firmware ({fields.get('fail_reason')})")
    ok, msg, volts = act("BS_CELL_READ", channel=7)
    check(ok and abs(volts - 3.6) < 0.002, f"BS_CELL_READ Zelle 7 = {volts:.4f} V")
    ok, msg, stack = act("BS_STACK_READ")
    check(ok and abs(stack - 14 * 3.6) < 0.03, f"BS_STACK_READ = {stack:.3f} V")
    ok, msg, _ = act("BS_CELL_VOLT", 4.5, 3)
    check(not ok and "Grenzwert" in msg, f"4,5 V ueber Grenzwert abgelehnt ({msg})")
    worker.set_battsim_voltage_limit(sid, 4.6)
    check(act("BS_CELL_VOLT", 4.5, 3)[0], "nach Grenzwert 4,6 V angenommen")
    check(not act("BS_CELL_VOLT", 1.0, 15)[0], "Zelle 15 abgelehnt")

    # PI-28: 0x130 von Hand / aus einem Ablauf ist gesperrt, solange der Treiber am Bus haengt bzw. der
    # Verbund-Partner einen Simulator fuehrt; andere IDs gehen weiter
    ok, msg = worker._send_can(dw.SIM_CAN_ID, 0x130, "01 02", False)
    check(not ok and "0x130" in msg, f"0x130 von Hand abgelehnt ({msg})")
    check(worker._send_can(dw.SIM_CAN_ID, 0x131, "01", False)[0], "0x131 geht")
    check(worker._send_can(dw.SIM_CAN_ID, 0x130, "01", True)[0], "Extended-ID 0x130 geht (anderer Frame)")
    check(st.get("foreign_sender") is False, "Zustands-Dict: foreign_sender False")

    worker.all_outputs_off("check")
    spin(0.6)
    st = seen["state"]
    check(st["output_on"] is False and st["remote"] is True, "ALLE AUS: Ausgang aus, Fernsteuerung bleibt")
    spin(6.5)
    check(seen["state"]["status_name"] == "Fernsteuerung", "nach ALLE AUS kein FAIL (es wird weiter gesendet)")

    sim = worker._sim_battsim
    worker.shutdown_can_nodes()
    spin(0.5)
    check(sim.state == 1, "shutdown_can_nodes -> Simulator in STANDBY")
    worker.set_simulation_mode(False)
    spin(0.2)
    check(seen["online"] == (sid, False), "Simulationsmodus aus -> Simulator getrennt")

    # Beenden (MainWindow.closeEvent: shutdown_can_nodes, dann stop_timers) schliesst auch den Bus selbst --
    # sonst meldete python-can bei jedem Beenden "SocketcanBus was not properly shut down" (Pi, 11.11.2026).
    worker.set_simulation_mode(True)
    spin(0.3)
    bus = worker._can_buses.get(dw.SIM_CAN_ID)
    closed: list[int] = []
    if bus is not None:
        real_close = bus.close
        bus.close = lambda: (closed.append(1), real_close())
    worker.shutdown_can_nodes()
    worker.stop_timers()
    check(bus is not None and closed == [1] and not worker._can_buses, "Beenden schliesst den CAN-Bus")

    # Ohne eigenen Simulator, aber Partner mit Simulator (Verbund): 0x130 ebenfalls gesperrt
    mock = MockCanBus()
    worker._can_buses["can:x"] = mock
    worker._can_stats["can:x"] = [0, 0]
    check(worker._send_can("can:x", 0x130, "01", False)[0], "ohne Simulator am Bus und ohne Partner: 0x130 geht")
    worker.set_partner_battsim(True)
    ok, msg = worker._send_can("can:x", 0x130, "01", False)
    check(not ok and "Partner" in msg, f"Partner fuehrt Simulator: 0x130 abgelehnt ({msg})")
    worker.set_partner_battsim(False)
    del worker._can_buses["can:x"]


def run_hardware(args) -> None:
    from can_bus.driver import CanBus
    print(f"Batterie-Simulator an {args.interface}:{args.channel} ({args.bitrate} bit/s)")
    bus = CanBus(args.interface, args.channel, args.bitrate)
    node = BattSim()
    try:
        pump(bus, node, 1.0)
        st = node.get_state()
        check(st.online, f"Status 0x120 empfangen ({st.status_name})")
        for c in st.cells:
            print(f"      Zelle {c.index:2d}: {c.voltage_v} V  {'ein' if c.enabled else 'aus'}")
        if not args.remote:
            return
        sched = CanScheduler(bus, name="hw")
        sched.attach(node)
        try:
            node.set_remote(True)
            pump(bus, node, 1.0)
            check(node.get_state().status == STATUS_REMOTE, "Fernsteuerung ein -> REMOTE")
            node.set_all_voltages(args.volts)
            node.set_all_enabled(True)
            node.set_output(True)
            pump(bus, node, 2.0)
            st = node.get_state()
            check(st.output_on is True, "Ausgang ein")
            for c in st.cells:
                print(f"      Zelle {c.index:2d}: soll {c.setpoint_v:.3f} V, ist {c.voltage_v} V, "
                      f"{'ein' if c.enabled else 'aus'}"
                      + (f" ({c.status_name})" if c.status in (3, 4) else ""))
            if st.fw_version is not None:
                print(f"      0x121: Firmware {st.fw_version}, FAIL-Grund {st.fail_reason} ({st.fail_reason_name}), "
                      f"Regler {'ein' if st.controller_on else 'aus'}, 48 V {st.supply_current_a:.3f} A")
            if args.hold:
                print(f"      Halte {args.hold:.0f} s: alle Zellen ein (Multimeter ablesen)", flush=True)
                pump(bus, node, args.hold)
            node.set_cell_enabled(5, False)
            pump(bus, node, 1.5)
            st = node.get_state()
            check(st.cells[4].enabled is False, f"Zelle 5 aus (Istwert {st.cells[4].voltage_v} V)")
            if args.hold:
                print(f"      Halte {args.hold:.0f} s: Zelle 5 aus, Rest ein (Multimeter ablesen)", flush=True)
                pump(bus, node, args.hold)
            node.set_output(False)
            pump(bus, node, 1.0)
            check(node.get_state().output_on is False, "Ausgang aus")
            node.set_remote(False)
            pump(bus, node, 1.0)
            check(node.get_state().status == 1, "Fernsteuerung aus -> STANDBY")
        finally:
            sched.stop(send_shutdown=True)
    finally:
        bus.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hardware", action="store_true")
    parser.add_argument("--interface", default="vector")
    parser.add_argument("--channel", default="")
    parser.add_argument("--bitrate", type=int, default=500_000)
    parser.add_argument("--remote", action="store_true", help="Fernsteuer-Folge am Geraet fahren")
    parser.add_argument("--volts", type=float, default=3.7)
    parser.add_argument("--hold", type=float, default=0.0,
                        help="Sekunden, die jede Stufe der Folge gehalten wird (zum Ablesen am Multimeter)")
    args = parser.parse_args()
    if args.hardware:
        run_hardware(args)
    else:
        run_mock()
        run_mock_v2()
        run_single_sender()
        run_worker()
    print()
    if FAILS:
        print(f"{len(FAILS)} Pruefung(en) fehlgeschlagen.")
        return 1
    print("Alles ok." + ("" if args.hardware else " (nur Nachbildung, keine Hardware geprueft)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
