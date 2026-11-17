"""Prueft PI-81 a (1.0.1): ALLE AUS meldet nicht erreichbare Geraete, Hinweis "beim Verbinden EIN" am Netzteil.

  * ALLE AUS mit einem vorher getrennten Netzteil (zuletzt Ausgang EIN): WARNING im Log, all_off_unreachable,
    Antwort von POST /all-off mit "unreachable", im Lauf (Art all_off) Eintrag im Bericht
  * Abschalten einzelner Geraete (devices_off_tagged, Verbund): getrenntes Geraet -> WARNING "Ausgang unbekannt"
  * Verbinden mit Ausgang EIN (12 V / 0,5 A) -> psu_found_on + Chip in der Kachel
  * Uebergabezustand beim Beenden (1 V / 0 A, Ausgang EIN, _release_psu) -> KEIN Chip
  * Schalten ueber LabControl (Sollwert, Ausgang, Fernsteuerung) bzw. Ausgang AUS gemeldet -> Chip weg
  * Partner: Feld found_on im Kachel-Status, PartnerDevices meldet den Chip weiter

Nur Mock (MockHCS34xx, offscreen), keine Hardware.  Aufruf:  python tools/check_psu_found_on.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LABCONTROL_DATA_DIR"] = tempfile.mkdtemp(prefix="lc-found-on-check-")
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import QObject, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import device_worker as dw  # noqa: E402
from hcs34xx.driver import PowerSupplyError  # noqa: E402
from hcs34xx.mock import MockHCS34xx  # noqa: E402

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


class LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


LOG = LogCapture()
logging.getLogger("device_worker").addHandler(LOG)
logging.getLogger("device_worker").setLevel(logging.INFO)

dw.KoradKEL102.discover_ports = staticmethod(lambda: [])
dw.HCS34xx.discover_ports = staticmethod(lambda: [])
dw.MicroHIL.discover = staticmethod(lambda: None)
dw.JDS66xx.discover_ports = staticmethod(lambda: [])
dw.picoscope_usb_present = lambda: False


def mock_psu(volts: float, amps: float, on: bool) -> MockHCS34xx:
    psu = MockHCS34xx()
    psu.set_voltage(volts)
    psu.set_current(amps)
    psu.set_output(on)
    return psu


class Probe:
    """Sammelt psu_found_on/all_off_unreachable eines Workers."""

    def __init__(self, worker) -> None:
        self.found: list[tuple[str, object]] = []
        self.unreachable: list[list[str]] = []
        worker.psu_found_on.connect(lambda d, info: self.found.append((d, info)))
        worker.all_off_unreachable.connect(lambda ids: self.unreachable.append(list(ids)))


def connect(worker, device_id: str, psu: MockHCS34xx) -> None:
    """Wie ein erfolgreicher Verbindungsversuch aus dem Probe-Thread (_psu_connect_job)."""
    worker._on_reconnect_done("psu", device_id, psu, "")


def disconnect_by_poll(worker, device_id: str) -> None:
    """GETD-Timeout im Poll -> 'Netzteil ... getrennt' (wie im Pi-Log 19:58:14)."""
    psu = worker._psus[device_id]

    def fail():
        raise PowerSupplyError("Timeout (GETD)")
    psu.get_display = fail
    worker._psu_next_poll[device_id] = 0.0
    worker._poll_devices({})


# -- Hinweis beim Verbinden -----------------------------------------------------------------------------

def section_found_on() -> None:
    print("found_on -- Netzteil beim Verbinden mit Ausgang EIN")
    from control_tab import PsuControlGroup

    worker = dw.DeviceWorker(simulation_mode=False)
    probe = Probe(worker)
    tile = PsuControlGroup("psu:T1", "Netzteil 12 V")
    worker.psu_found_on.connect(lambda d, info: tile.set_found_on(info) if d == "psu:T1" else None)

    LOG.lines.clear()
    connect(worker, "psu:T1", mock_psu(12.0, 0.5, True))
    info = probe.found[-1][1] if probe.found else None
    check("Ausgang EIN, 12 V / 0,5 A -> psu_found_on mit Sollwerten",
          probe.found and probe.found[-1][0] == "psu:T1" and isinstance(info, dict)
          and abs(info["voltage"] - 12.0) < 1e-6 and abs(info["current"] - 0.5) < 1e-6, str(probe.found))
    check("... Chip in der Kachel sichtbar, Text kurz", tile.found_on_visible()
          and tile._found_on_chip.text() == "beim Verbinden EIN", tile._found_on_chip.text())
    check("... Tooltip nennt die Sollwerte", "12 V / 0.5 A" in tile._found_on_chip.toolTip(),
          tile._found_on_chip.toolTip())
    check("... WARNING im Log, nichts geschaltet", any("psu:T1" in l and "vorgefunden" in l for l in LOG.lines)
          and worker._psus["psu:T1"].get_output() is True and worker._psus["psu:T1"].get_setpoint() == (12.0, 0.5),
          str(LOG.lines))

    # Schalten ueber LabControl: Sollwert aus der Kachel
    probe.found.clear()
    worker.set_psu_voltage("psu:T1", 11.0)
    check("Sollwert ueber LabControl -> Chip weg", probe.found == [("psu:T1", None)] and not tile.found_on_visible(),
          str(probe.found))

    # Uebergabezustand beim Beenden: 1 V / 0 A, Ausgang EIN
    probe.found.clear()
    handover = MockHCS34xx()
    worker._release_psu(handover)
    check("Uebergabezustand des Mocks wie beim Beenden", handover.get_output() is True
          and handover.get_setpoint() == (dw.PSU_MIN_VOLTAGE, 0.0), str(handover.get_setpoint()))
    tile2 = PsuControlGroup("psu:T2", "Netzteil 24 V")
    worker.psu_found_on.connect(lambda d, i: tile2.set_found_on(i) if d == "psu:T2" else None)
    connect(worker, "psu:T2", handover)
    check("Uebergabezustand 1 V / 0 A EIN -> kein Chip", not [f for f in probe.found if f[0] == "psu:T2"]
          and not tile2.found_on_visible(), str(probe.found))
    # Sollspannung hochgedreht, Strom weiter 0 A: liefert nichts -> auch kein Chip
    connect(worker, "psu:T3", mock_psu(24.0, 0.0, True))
    check("24 V / 0 A EIN -> kein Chip (Strombegrenzung entscheidet)",
          not [f for f in probe.found if f[0] == "psu:T3"], str(probe.found))
    # Ausgang AUS beim Verbinden
    connect(worker, "psu:T4", mock_psu(12.0, 2.0, False))
    check("Ausgang AUS beim Verbinden -> kein Chip", not [f for f in probe.found if f[0] == "psu:T4"],
          str(probe.found))

    # Ausgang AUS gemeldet (z. B. am Geraet/Abgleich) -> Chip weg
    connect(worker, "psu:T5", mock_psu(5.0, 1.0, True))
    check("psu:T5 mit Chip", ("psu:T5", {"voltage": 5.0, "current": 1.0}) in probe.found, str(probe.found))
    probe.found.clear()
    worker._emit_psu_output("psu:T5", False)
    check("Ausgang AUS gemeldet -> Chip weg", probe.found == [("psu:T5", None)], str(probe.found))

    # Fernsteuerung (MCP/Verbund) zaehlt als Schalten ueber LabControl
    connect(worker, "psu:T6", mock_psu(5.0, 1.0, True))
    probe.found.clear()
    worker.execute_remote_action(1, "psu:T6", "psu", "PSU_OUT_ON", 0.0, 0)
    check("PSU_OUT_ON ueber die Fernsteuerung -> Chip weg", probe.found == [("psu:T6", None)], str(probe.found))

    # Rueckwechsel aus der Handbedienung (LabControl hat selbst uebergeben) -> kein Chip
    connect(worker, "psu:T7", mock_psu(5.0, 1.0, False))
    worker.set_psu_manual("psu:T7", True)
    worker._psus["psu:T7"].set_current(2.0)   # von Hand aufgedreht
    probe.found.clear()
    worker.set_psu_manual("psu:T7", False)
    check("Rueckwechsel aus der Handbedienung -> kein Chip", not probe.found, str(probe.found))

    # Wiederverbinden nach Trennung: Chip erneut
    connect(worker, "psu:T8", mock_psu(12.0, 0.5, True))
    disconnect_by_poll(worker, "psu:T8")
    check("Trennung -> Chip weg", ("psu:T8", None) in probe.found, str(probe.found))
    probe.found.clear()
    connect(worker, "psu:T8", mock_psu(12.0, 0.5, True))
    check("Wiederverbinden mit Ausgang EIN -> Chip erneut", probe.found and probe.found[-1][0] == "psu:T8"
          and probe.found[-1][1] is not None, str(probe.found))

    # Chip vor der Kachel gemeldet (ControlTab haelt ihn)
    from control_tab import ControlTab
    from presets import PresetStore
    tab = ControlTab(PresetStore())
    tab.set_psu_found_on("psu:T9", {"voltage": 12.0, "current": 0.5})
    tab.on_device_known("psu", "psu:T9", "Netzteil T9")
    section = tab._sections.get("psu:T9")
    check("ControlTab: vor der Kachel gemeldeter Hinweis erscheint in der neuen Kachel",
          section is not None and section._found_on is not None)
    tab.set_psu_found_on("psu:T9", None)
    check("ControlTab: Hinweis weg", section is not None and section._found_on is None
          and "psu:T9" not in tab._psu_found_on)


# -- Partner (Verbund-Kachel) -------------------------------------------------------------------------------

def section_partner() -> None:
    print("partner -- Feld found_on im Kachel-Status, Chip beim Verbund-Partner")
    import share_api
    from live_state import LiveState
    from partner_devices import PartnerDevices

    live = LiveState()
    live.on_device_known("psu", "psu:0001", "Netzteil")
    live.on_psu_setpoints("psu:0001", 12.0, 0.5)
    live.on_psu_output_state("psu:0001", True)
    live.on_psu_found_on("psu:0001", {"voltage": 12.0, "current": 0.5})
    snap = live.snapshot()
    tile = share_api._tile_detail("psu:0001", snap["devices"]["psu:0001"], snap)
    fields = {f["key"]: f.get("value", f.get("text")) for f in tile["fields"]}
    check("Kachel-Status enthaelt found_on=1", fields.get("found_on") == 1, str(fields))

    class FakeLink(QObject):
        snapshot_changed = Signal(object)
        action_finished = Signal(int, str, str, bool, str)

        def name(self) -> str:
            return "pi"

    devices = PartnerDevices(FakeLink())
    seen: list[tuple[str, object]] = []
    devices.psu_found_on.connect(lambda rid, info: seen.append((rid, info)))

    def partner_snapshot(tile_json):
        return types.SimpleNamespace(host="raspberrypi", remote_effective=True, lock="free", tiles=[tile_json])

    devices.on_snapshot(partner_snapshot(tile))
    check("Partner: Chip mit Sollwerten", seen and seen[-1][0] == "@raspberrypi/psu:0001"
          and seen[-1][1] == {"voltage": 12.0, "current": 0.5}, str(seen))
    live.on_psu_found_on("psu:0001", None)
    snap = live.snapshot()
    seen.clear()
    devices.on_snapshot(partner_snapshot(share_api._tile_detail("psu:0001", snap["devices"]["psu:0001"], snap)))
    check("Partner: Chip weg, wenn der Besitzer found_on=0 meldet", seen == [("@raspberrypi/psu:0001", None)],
          str(seen))
    seen.clear()
    devices.on_snapshot(partner_snapshot(share_api._tile_detail("psu:0001", snap["devices"]["psu:0001"], snap)))
    check("Partner: found_on=0 nicht jede Sekunde neu gemeldet", seen == [], str(seen))
    old = dict(tile)
    old["fields"] = [f for f in tile["fields"] if f["key"] != "found_on"]
    devices.on_snapshot(partner_snapshot(old))
    check("Partner: aelterer Besitzer ohne Feld -> nichts", seen == [], str(seen))


# -- ALLE AUS mit getrenntem Netzteil ---------------------------------------------------------------------

def section_all_off() -> None:
    print("all_off -- ALLE AUS meldet nicht erreichbare Geraete")
    import share_api
    from share_remote import RemoteBridge

    worker = dw.DeviceWorker(simulation_mode=False)
    probe = Probe(worker)
    connect(worker, "psu:A", mock_psu(12.0, 1.0, False))
    connect(worker, "psu:B", mock_psu(24.0, 1.0, False))
    worker.set_psu_output("psu:B", True)   # per Verbund/Kachel EIN
    LOG.lines.clear()
    disconnect_by_poll(worker, "psu:B")
    check("Trennung mit Ausgang EIN: WARNING im Log", any("psu:B" in l and "mit Ausgang EIN getrennt" in l
                                                           for l in LOG.lines), str(LOG.lines))
    lost = worker.session_lost_devices()
    check("Worker merkt sich: psu:B zuletzt EIN getrennt", lost.get("psu:B", {}).get("output_on") is True, str(lost))

    LOG.lines.clear()
    finished: list[str] = []
    worker.all_off_finished.connect(finished.append)
    worker.all_outputs_off("Fenster geschlossen")
    check("ALLE AUS: psu:A aus", worker._psus["psu:A"].get_output() is False)
    check("ALLE AUS: all_off_unreachable nennt psu:B", probe.unreachable == [["psu:B"]], str(probe.unreachable))
    check("ALLE AUS: WARNING 'Ausgang unbekannt' mit psu:B und 'zuletzt Ausgang EIN'",
          any("psu:B" in l and "Ausgang unbekannt" in l and "zuletzt Ausgang EIN" in l for l in LOG.lines),
          str(LOG.lines))
    check("ALLE AUS: all_off_finished ohne Fehlschlag (verbundene Geraete aus)", finished == [""], str(finished))

    # Antwort von POST /all-off ueber die RemoteBridge (alles in einem Thread: direkte Verbindungen, die Antwort
    # liegt vor, bevor submit_all_off wartet)
    import json
    bridge = RemoteBridge()
    worker.all_off_unreachable.connect(bridge.on_all_off_unreachable)
    worker.all_off_finished.connect(bridge.on_all_off_finished)
    bridge.all_off_requested.connect(lambda _id, _client: worker.all_outputs_off("remote"))
    response = share_api._all_off(bridge, "test")
    body = json.loads(response.body)
    check("POST /all-off: ok mit 'unreachable'", response.status == 200 and body.get("ok") is True
          and body.get("unreachable") == ["psu:B"], f"{response.status} {body}")

    # Abschalten einzelner Geraete (Verbund /devices-off, Abbruch eines Laufs)
    LOG.lines.clear()
    done: list[tuple[list, list]] = []
    worker.devices_off_done.connect(lambda _tag, off, failed: done.append((off, failed)))
    worker.devices_off_tagged(7, ["psu:A", "psu:B"], "remote devices-off (pc)")
    check("devices-off: psu:B nicht erreichbar", done and done[-1] == (["psu:A"], ["psu:B"]), str(done))
    check("devices-off: WARNING 'Ausgang unbekannt' fuer psu:B",
          any("psu:B" in l and "Ausgang unbekannt" in l for l in LOG.lines), str(LOG.lines))

    # Wieder verbunden -> nicht mehr 'nicht erreichbar'
    connect(worker, "psu:B", mock_psu(24.0, 1.0, False))
    probe.unreachable.clear()
    worker.all_outputs_off("test")
    check("wieder verbunden: ALLE AUS meldet nichts", probe.unreachable == [[]], str(probe.unreachable))

    # Simulation abgeschaltet: absichtlich entfernt, kein 'Ausgang unbekannt'
    worker._add_mock_psu()
    worker._remove_mock_psu()
    probe.unreachable.clear()
    worker.all_outputs_off("test")
    check("Simulation entfernt: kein 'Ausgang unbekannt'", probe.unreachable == [[]], str(probe.unreachable))


def section_report() -> None:
    print("report -- Lauf mit ALLE AUS: Bericht nennt das getrennte Netzteil")
    import run_report
    import run_results
    from run_record import RunRecorder
    from run_session import DeviceDirectory, Interlock, RunSession
    from safety import SafetyMonitor
    from testcase_model import TestStep

    worker = dw.DeviceWorker(simulation_mode=False)
    connect(worker, "psu:A", mock_psu(12.0, 1.0, False))
    connect(worker, "psu:B", mock_psu(24.0, 1.0, True))
    directory = DeviceDirectory()
    recorder = RunRecorder()
    for device_id, label in (("psu:A", "Netzteil 12 V"), ("psu:B", "Netzteil 24 V Endstufe")):
        directory.set_online("psu", device_id, True)
        recorder.on_device_known("psu", device_id, label)
    session = RunSession(worker, SafetyMonitor({}), recorder, directory, Interlock([], directory.is_online))
    runner = session.runner
    steps = [TestStep(device_kind="psu", device_id="psu:A", action="PSU_VOLT", value=12.0),
             TestStep(device_kind="psu", device_id="psu:A", action="PSU_OUT_ON"),
             TestStep(step_type="wait", duration=5.0)]

    for variant in ("worker_after", "worker_before"):
        connect(worker, "psu:B", mock_psu(24.0, 1.0, True))
        session.start(steps, "pi81")
        spin(2.0, lambda: worker._psus["psu:A"].get_output() is True)
        disconnect_by_poll(worker, "psu:B")
        # wie MainWindow._safe_stop: ALLE AUS an den Worker (dort eigener Thread -> Rueckmeldung spaeter), dann
        # expect_all_off, dann Abbruch
        session.expect_all_off()
        if variant == "worker_after":
            QTimer.singleShot(50, lambda: worker.all_outputs_off("ALLE AUS"))
        else:
            worker.all_outputs_off("ALLE AUS")   # Rueckmeldung schon da, bevor die Abschaltung beginnt
        runner.abort("ALLE AUS – Ablauf abgebrochen", mode="all_off")
        spin(5.0, lambda: not runner.is_running())
        record = recorder.record()
        shutdown = record.shutdown or {}
        check(f"{variant}: Lauf zu Ende, Art all_off, psu:B nicht erreichbar", not runner.is_running()
              and shutdown.get("mode") == "all_off" and shutdown.get("unreachable") == ["psu:B"], str(shutdown))
        html = run_report.build_html(record, [])
        check(f"{variant}: Bericht nennt 'Nicht erreichbar (Ausgang unbekannt)' mit Namen",
              "Nicht erreichbar (Ausgang unbekannt)" in html and "Netzteil 24 V Endstufe" in html, html[-700:])
        result = run_results.result_dict(record)
        check(f"{variant}: run-result.json mit unreachable", (result.get("shutdown") or {}).get("unreachable")
              == ["psu:B"], str(result.get("shutdown")))

    # Ohne ALLE-AUS-Anforderung (Kommandozeile): sofort fertig, kein Warten
    session.start(steps, "pi81")
    spin(2.0, lambda: worker._psus["psu:A"].get_output() is True)
    session._all_off_requested = 0.0
    t0 = time.monotonic()
    runner.stop(mode="all_off")
    spin(3.0, lambda: not runner.is_running())
    check("ohne ALLE-AUS-Anforderung: kein Warten", not runner.is_running() and time.monotonic() - t0 < 2.0,
          f"{time.monotonic() - t0:.1f} s")


SECTIONS = {"found_on": section_found_on, "partner": section_partner, "all_off": section_all_off,
            "report": section_report}


def main() -> int:
    wanted = [a for a in sys.argv[1:] if a in SECTIONS] or list(SECTIONS)
    for name in wanted:
        SECTIONS[name]()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FEHLER: " + "; ".join(FAILURES))
        return 1
    print("Alle Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
