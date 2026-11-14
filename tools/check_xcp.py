"""Prueft FT-10 XCP Grundlage (0.64.0): A2L-Leser, XCP-Master, Nachbildung, Dienst mit EPK-Abgleich, Ablaufschritte.

- a2l     -- Nachbildungs-A2L und ein Ausschnitt im a2lgen-Format mit 29-Bit-IDs und Motorola-Daten: Objekte,
             Umrechnung (RAT_FUNC, TAB_VERB), Grenzen beim Kodieren, EPK, IDs
- client  -- XcpClient gegen SimXcpEcu (ueber einen nachgebildeten Worker): CONNECT, SHORT_UPLOAD, UPLOAD in
             mehreren Frames, DOWNLOAD, ERR_WRITE_PROTECTED, keine Antwort ohne CONNECT
- service -- XcpService: lesen, schreiben mit Rueckleseprobe, Grenzen, nur lesbar, EPK passt nicht -> abgelehnt
- steps   -- xcp_steps.command_for/summary
- cli     -- `main.py run` im Simulationsmodus: XCP_READ mit Pruefung, XCP_WRITE + Rueckleseprobe -> Exit 0;
             Wert ausserhalb der A2L-Grenzen -> Exit 2

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_xcp.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-xcp-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []

SNIPPET = """ASAP2_VERSION 1 61
/begin PROJECT p ""
  /begin MODULE testmod "x"
    /begin MOD_PAR "" EPK "abc" ADDR_EPK 0x100 /end MOD_PAR
    /begin MOD_COMMON "" BYTE_ORDER MSB_FIRST /end MOD_COMMON
    /begin IF_DATA XCP
      /begin PROTOCOL_LAYER 0x0100 100 200 200 200 200 200 200 8 8 BYTE_ORDER_MSB_LAST /end PROTOCOL_LAYER
      /begin XCP_ON_CAN 0x0100 CAN_ID_MASTER 0x98FF10F3 CAN_ID_SLAVE 0x98FF1131 BAUDRATE 250000 /end XCP_ON_CAN
    /end IF_DATA
    /begin COMPU_METHOD CM_LIN "" LINEAR "%6.2" "V" COEFFS_LINEAR 0.01 -1 /end COMPU_METHOD
    /begin RECORD_LAYOUT RL_SW FNC_VALUES 1 SWORD ROW_DIR DIRECT /end RECORD_LAYOUT
    /begin MEASUREMENT Volt "x" UWORD CM_LIN 0 0 -1 654.35 ECU_ADDRESS 0x10 /end MEASUREMENT
    /begin MEASUREMENT Arr "x" UBYTE NO_COMPU_METHOD 0 0 0 255 ECU_ADDRESS 0x20 ARRAY_SIZE 3 /end MEASUREMENT
    /begin CHARACTERISTIC Par "x" VALUE 0x30 RL_SW 0 NO_COMPU_METHOD -100 100 READ_ONLY /end CHARACTERISTIC
  /end MODULE
/end PROJECT
"""


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def child_main() -> int:
    import no_hardware
    no_hardware.block()
    import headless
    return headless.main(["labcontrol", *sys.argv[2:]])


class FakeWorker:
    """Statt DeviceWorker: CanTap wird direkt aus der Nachbildung gefuettert."""

    def __init__(self, sim) -> None:
        self.sim = sim
        self.taps = []

    def add_can_tap(self, _can_id, tap) -> None:
        self.taps.append(tap)

    def remove_can_tap(self, _can_id, tap) -> None:
        if tap in self.taps:
            self.taps.remove(tap)

    def can_send_threadsafe(self, _can_id, arbitration_id, data, extended) -> None:
        self.sim.on_frame(SimpleNamespace(arbitration_id=arbitration_id, data=bytes(data), extended=extended))
        for arb, payload in self.sim.poll(0.0):
            for tap in self.taps:
                tap.feed(SimpleNamespace(arbitration_id=arb, data=payload, extended=False))


def section_a2l() -> None:
    print("a2l")
    import xcp_a2l
    from xcp_sim import sim_a2l_text
    a2l = xcp_a2l.parse(sim_a2l_text())
    check("Nachbildung: 4 Messgroessen, 2 Parameter, IDs 0x7F0/0x7F1, EPK",
          len(a2l.names("measurement")) == 4 and len(a2l.names("characteristic")) == 2
          and (a2l.master_id, a2l.slave_id) == (0x7F0, 0x7F1) and a2l.epk.startswith("labcontrol_sim"))
    temp = a2l.get("Sim.Temperature")
    check("RAT_FUNC 1/10: 235 -> 23,5", abs(temp.compu.to_phys(235) - 23.5) < 1e-9
          and abs(temp.compu.to_raw(23.5) - 235) < 1e-9)
    check("TAB_VERB: 1 -> RUN", a2l.get("Sim.State").compu.text(1) == "RUN")
    table = a2l.get("Sim.Table")
    check("VAL_BLK NUMBER 4", table.count == 4 and table.size == 8 and table.writable)
    try:
        a2l.encode(a2l.get("Sim.Gain"), [11.0])
        check("Grenze 0–10: 11 abgelehnt", False)
    except xcp_a2l.A2lError as exc:
        check("Grenze 0–10: 11 abgelehnt", "außerhalb" in str(exc), str(exc))
    other = xcp_a2l.parse(SNIPPET)
    check("29-Bit-IDs (Bit 31), Motorola, T1 100 ms", other.extended_ids and other.master_id == 0x18FF10F3
          and not other.little_endian and abs(other.t1_s - 0.1) < 1e-9)
    volt = other.get("Volt")
    check("LINEAR und Motorola-Dekodierung", abs(other.decode(volt, b"\x01\x2C")[0] - 2.0) < 1e-9)
    check("ARRAY_SIZE 3, READ_ONLY-Parameter nicht schreibbar", other.get("Arr").count == 3
          and not other.get("Par").writable)


def section_client() -> None:
    print("client")
    import xcp_a2l
    from xcp_client import XcpClient, XcpError
    from xcp_sim import ADDR_EPK, ADDR_TABLE, EPK, SimXcpEcu, sim_a2l_text
    sim = SimXcpEcu()
    client = XcpClient(FakeWorker(sim), "can:FAKE", 0x7F0, 0x7F1, timeout_s=0.2)
    try:
        client.read(ADDR_EPK, 4)
        check("ohne CONNECT keine Antwort", False)
    except XcpError as exc:
        check("ohne CONNECT keine Antwort", exc.code is None, str(exc))
    info = client.connect()
    check("CONNECT: MAX_CTO 8, Intel", info["max_cto"] == 8 and info["byte_order"] == "intel")
    check("UPLOAD ueber mehrere Frames (EPK)", client.read(ADDR_EPK, len(EPK)).decode() == EPK)
    a2l = xcp_a2l.parse(sim_a2l_text())
    table = a2l.get("Sim.Table")
    client.write(table.address, a2l.encode(table, [1, 2, 3, 4]))
    check("DOWNLOAD in zwei Frames, zurueckgelesen", a2l.decode(table, client.read(ADDR_TABLE, 8)) == [1, 2, 3, 4])
    try:
        client.write(a2l.get("Sim.Supply_mV").address, b"\x00\x00")
        check("Messgroesse schreiben -> ERR_WRITE_PROTECTED", False)
    except XcpError as exc:
        check("Messgroesse schreiben -> ERR_WRITE_PROTECTED", exc.code == 0x23, str(exc))
    client.close()
    check("Abgriff wieder entfernt", True)


def section_service() -> None:
    print("service")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from xcp_service import XcpService
    from xcp_sim import ADDR_EPK, SimXcpEcu
    sim = SimXcpEcu()
    service = XcpService(FakeWorker(sim))
    service.configure({"a2l": "builtin:sim", "can_device": "can:FAKE"}, simulation=False)
    check("A2L der Nachbildung geladen", service.configured())
    result = service.call("read", {"name": "Sim.Supply_mV"})
    check("lesen: Versorgung 13800 mV", result.ok and result.values == [13800.0], result.describe())
    result = service.call("read", {"name": "Sim.State"})
    check("lesen mit Werttabelle: '1 (RUN)'", result.ok and "RUN" in result.text, result.describe())
    result = service.call("write", {"name": "Sim.Gain", "values": [2.5]})
    check("schreiben mit Rueckleseprobe", result.ok and result.values == [2.5], result.describe())
    result = service.call("write", {"name": "Sim.Gain", "values": [50]})
    check("ausserhalb der A2L-Grenzen abgelehnt", not result.ok and "außerhalb" in result.error, result.describe())
    result = service.call("write", {"name": "Sim.Counter", "values": [1]})
    check("Messgroesse nicht schreibbar", not result.ok and "nicht schreibbar" in result.error, result.describe())
    from xcp_sim import sim_a2l_text
    stale = DATA / "alt.a2l"
    stale.write_text(sim_a2l_text().replace(f"ADDR_EPK 0x{ADDR_EPK:08X}", "ADDR_EPK 0x0003A2E4"), encoding="utf-8")
    service.configure({"a2l": str(stale), "can_device": "can:FAKE"}, simulation=False)
    result = service.call("read", {"name": "Sim.Supply_mV"})
    check("EPK-Adresse nicht lesbar -> 'A2L passt nicht zur Firmware'", not result.ok
          and "passt nicht zur Firmware" in result.error, result.describe())
    service.configure({"a2l": "builtin:sim", "can_device": "can:FAKE"}, simulation=False)
    sim.memory[ADDR_EPK - 0x20000000] = ord("X")
    service.on_can_connected("can:FAKE", False)            # Verbindung neu aufbauen
    result = service.call("read", {"name": "Sim.Supply_mV"})
    check("EPK passt nicht -> abgelehnt", not result.ok and "EPK passt nicht" in result.error, result.describe())
    service.shutdown()


def section_steps() -> None:
    print("steps")
    import xcp_steps
    check("XCP_READ -> read", xcp_steps.command_for("XCP_READ", {"name": "A", "index": 2})
          == ("read", {"name": "A", "index": 2}))
    check("XCP_WRITE '1,5 2' -> [1.5, 2.0]", xcp_steps.command_for("XCP_WRITE", {"name": "A", "values": "1,5 2"})
          == ("write", {"name": "A", "values": [1.5, 2.0]}))
    check("Kurzbeschreibung", xcp_steps.summary("XCP_WRITE", {"name": "G", "values": [2.5]}) == "G = 2.5")


def run_cli(*args: str, timeout: float = 90.0) -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, __file__, "--child", *args], env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def section_cli() -> None:
    print("cli")
    import testcase_model as tm
    (DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")
    x = dict(device_kind="xcp", device_id="xcp:dut")
    good = DATA / "xcp_gut.json"
    tm.save_steps([tm.TestStep(**x, action="XCP_READ", params={"name": "Sim.Supply_mV"}, check_enabled=True,
                               check_field="xcp_value", check_min=13700, check_max=13900, duration=0.1),
                   tm.TestStep(**x, action="XCP_WRITE", params={"name": "Sim.Gain", "values": [3.25]}, duration=0.1),
                   tm.TestStep(**x, action="XCP_READ", params={"name": "Sim.Gain"}, check_enabled=True,
                               check_field="xcp_value", check_min=3.25, check_max=3.25, duration=0.1)], good)
    out = DATA / "xcp.json"
    code, text = run_cli("run", str(good), "--simulation", "--wait-devices", "20", "--json", str(out))
    try:
        checks = json.loads(out.read_text(encoding="utf-8"))["checks"]
    except (OSError, ValueError, KeyError):
        checks = []
    check("Kommandozeile: lesen, schreiben, zuruecklesen -> Exit 0", code == 0 and len(checks) == 2
          and checks[1]["values"] == [3.25], f"{code} {checks} {text[-400:]}")
    bad = DATA / "xcp_grenze.json"
    tm.save_steps([tm.TestStep(**x, action="XCP_WRITE", params={"name": "Sim.Gain", "values": [99]})], bad)
    code, text = run_cli("run", str(bad), "--simulation", "--wait-devices", "20")
    check("Kommandozeile: Wert ausserhalb der A2L-Grenzen -> Exit 2", code == 2 and "außerhalb" in text,
          f"{code}: {text[-300:]}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return child_main()
    import no_hardware
    no_hardware.block()
    section_a2l()
    section_client()
    section_service()
    section_steps()
    section_cli()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} Fehler:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
