"""Prueft den Verbund Pi + PC, Stufe V2 (PI-24 a, PI-25 a, PI-26 a; 0.47.0) mit ZWEI Hauptfenstern:

  B = "Pi" (Besitzer): Simulation, Freigabe auf einem freien Port, Hauptschalter an
  A = "PC" (Leitstand): Simulation, B als Verbund-Partner

- lease    -- Belegungstabelle allein (Frist, Konflikt, Verlaengern, Freigeben)
- editor   -- Partnergeraete in der Geraeteauswahl, Speichern mit Rechnernamen, Laden loest auf
- run      -- Ablauf mit Partner-Netzteil: Belegung beim Besitzer, Kachel dort gesperrt, fremde
              Bedienung abgewiesen, Pruefung mit Partner-Messwert, nach dem Ende freigegeben
- refuse   -- nicht unterstuetzte Aktion, Partner getrennt: Lauf startet nicht
- alloff   -- ALLE AUS am Leitstand bricht den Lauf ab und gibt die Belegung frei
- lost     -- Partner nicht mehr erreichbar: Leitstand bricht nach ~3 s ab, Besitzer schaltet die
              belegten Geraete nach 3 s ohne Lebenszeichen ab (Ausgang AUS)

Offscreen, Simulation, keine Hardware.  Aufruf:  python tools/check_partner_v2.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "device-driver"), str(ROOT / "lab_gui"), str(ROOT / "tools")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
DATA = Path(tempfile.mkdtemp(prefix="lc-partner2-check-"))
os.environ["LABCONTROL_DATA_DIR"] = str(DATA)
os.environ.pop("LABCONTROL_NETWORK_DIR", None)
(DATA / "settings.json").write_text(json.dumps({"simulation_mode": True}), encoding="utf-8")

from PySide6.QtWidgets import QApplication  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAILURES: list[str] = []
TOKEN_B = "token-B"
HOST_B = "pi-sim"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def spin(app, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.02)
    return bool(until()) if until is not None else False


def section_lease() -> None:
    print("lease -- Belegungstabelle")
    from lease_table import LeaseConflict, LeaseTable
    now = [100.0]
    table = LeaseTable(clock=lambda: now[0])
    lease = table.acquire(["psu:1", "load:1"], "10.0.0.2 Verbund:PC", "Wecktest", 3.0)
    check("belegt, Halter lesbar", table.holder_of("psu:1") is lease and lease.holder_name() == "PC")
    try:
        table.acquire(["load:1"], "x", "y")
        conflict = False
    except LeaseConflict:
        conflict = True
    check("zweite Belegung desselben Geraets -> Konflikt", conflict)
    now[0] += 2.5
    check("Verlaengern innerhalb der Frist", table.renew(lease.lease_id) is not None)
    now[0] += 2.9
    check("noch gueltig 2,9 s nach dem Verlaengern", not table.pop_expired())
    now[0] += 0.2
    expired = table.pop_expired()
    check("nach 3 s ohne Lebenszeichen abgelaufen", [e.lease_id for e in expired] == [lease.lease_id])
    check("abgelaufen: Verlaengern geht nicht mehr", table.renew(lease.lease_id) is None)
    second = table.acquire(["psu:1"], "h", "r", 99)
    check("Frist auf 10 s begrenzt", second.ttl_s == 10.0)
    check("Freigeben", table.release(second.lease_id) is not None and table.holder_of("psu:1") is None)


def make_window(settings_data: dict):
    from main_window import MainWindow
    from settings import Settings
    settings = Settings()
    settings._data = {"simulation_mode": True, **settings_data}
    window = MainWindow(settings)
    window.resize(1280, 800)
    window.show()
    return window


def post(port: int, path: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                     method="POST", headers={"Authorization": f"Bearer {TOKEN_B}", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> int:
    app = QApplication.instance() or QApplication([])
    section_lease()

    import no_hardware
    no_hardware.block()
    import device_worker as dw
    from testcase_model import TestStep, load_steps, save_steps
    import verbund_ids

    port = free_port()
    window_b = make_window({
        "share_enabled": True, "share_bind": "127.0.0.1", "share_port": port, "share_token": TOKEN_B,
        "share_devices": {dw.SIM_PSU_ID: {"read": True, "control": True},
                          dw.SIM_LOAD_ID: {"read": True, "control": True}},
    })
    # Beide Fenster laufen auf demselben Rechner: B gibt sich als "pi-sim" aus
    server_b = window_b._share_server
    server_b._app_info = {**server_b._app_info, "host": HOST_B}
    if server_b._server is not None:
        server_b._server.share_app_info = server_b._app_info
    window_b._live_state.set_remote_control(True, 3600.0)   # Hauptschalter "Fernsteuerung" an
    window_a = make_window({"partner_enabled": True, "partner_address": f"127.0.0.1:{port}",
                            "partner_token": TOKEN_B, "partner_name": "Pi"})
    partner_psu = verbund_ids.qualify(HOST_B, dw.SIM_PSU_ID)
    ready = spin(app, 15.0, lambda: window_a._partner.is_connected()
                 and window_a.testcase_tab.knows_device(partner_psu, "psu", "Pi · Netzteil 1")
                 and dw.SIM_PSU_ID in window_b._worker._psus)
    check("A verbunden, Partner-Netzteil in der Geraeteauswahl", ready, str(window_a.testcase_tab._known_devices))
    mock_b = window_b._worker._psus.get(dw.SIM_PSU_ID)
    tab_a = window_a.testcase_tab

    print("editor -- Speichern/Laden mit Rechnernamen")
    import app_logging
    own = app_logging.host_name()
    folder = DATA / "testcases"
    folder.mkdir(exist_ok=True)
    path = folder / "Verbund.json"
    steps = [TestStep(device_kind="psu", device_id=partner_psu, action="PSU_VOLT", value=5.0, duration=0.2),
             TestStep(device_kind="psu", device_id=partner_psu, action="PSU_OUT_ON", duration=1.5,
                      check_enabled=True, check_field="voltage", check_min=4.5, check_max=5.5),
             TestStep(device_kind="psu", device_id=dw.SIM_PSU_ID, action="PSU_VOLT", value=3.0, duration=0.1),
             TestStep(device_kind="psu", device_id=partner_psu, action="PSU_OUT_OFF", duration=0.2)]
    save_steps(steps, path)
    check("Laden klappt", tab_a._load_path(path))
    file_steps, known = tab_a._steps_for_file(tab_a.steps())
    ids = [s.device_id for s in file_steps]
    check("Speichern: eigenes Geraet mit eigenem Rechnernamen, Partnergeraet mit seinem",
          ids[2] == verbund_ids.qualify(own, dw.SIM_PSU_ID) and ids[0] == partner_psu, str(ids))
    check("Speichern: Namen fuer beide in der Datei", verbund_ids.qualify(own, dw.SIM_PSU_ID) in known
          and partner_psu in known)
    save_steps(file_steps, path, known)
    tab_a._load_path(path)
    check("Laden: eigener Rechnername aufgeloest, Partner bleibt",
          [s.device_id for s in tab_a.steps()] == [partner_psu, partner_psu, dw.SIM_PSU_ID, partner_psu],
          str([s.device_id for s in tab_a.steps()]))

    print("run -- Ablauf mit Partner-Netzteil")
    leases_b = window_b._live_state.leases
    seen = {"lease": False, "banner": "", "foreign": 0}

    def watch() -> bool:
        lease = leases_b.holder_of(dw.SIM_PSU_ID)
        if lease is not None and not seen["lease"]:
            seen["lease"] = True
            seen["holder"] = lease.holder_name()
            # Ein anderer Client (ohne Belegungs-ID) will das belegte Netzteil bedienen
            seen["foreign"], _ = post(port, f"/api/v1/tiles/{dw.SIM_PSU_ID}/actions", {"action": "PSU_OUT_OFF"})
        if lease is not None and not seen["banner"]:
            seen["banner"] = window_b.control_tab.lease_text(dw.SIM_PSU_ID)
            section = window_b.control_tab._sections[dw.SIM_PSU_ID]
            seen["widgets"] = (section._voltage_spin.isEnabled(), section._output_on_button.isEnabled(),
                               section._manual_button.isEnabled())
        return False

    results: list[tuple[int, bool, float]] = []
    window_a._test_runner.step_result.connect(lambda i, ok, v: results.append((i, ok, v)))
    failed: list[str] = []
    window_a._test_runner.step_failed.connect(lambda _i, m: failed.append(m))
    finished: list[int] = []
    window_a._test_runner.run_finished.connect(lambda: finished.append(1))
    window_a._on_run_requested()
    spin(app, 20.0, lambda: watch() or bool(finished) or bool(failed))
    check("Lauf beendet ohne Fehler", finished and not failed, str(failed))
    check("Belegung beim Besitzer waehrend des Laufs", seen["lease"] and seen.get("holder") == own,
          str(seen.get("holder")))
    check("Kachel beim Besitzer gesperrt mit Hinweis", "belegt durch" in seen["banner"], seen["banner"])
    check("... Sollwert und Ausgang gesperrt, Handbedienung frei", seen.get("widgets") == (False, False, True),
          str(seen.get("widgets")))
    section_b = window_b.control_tab._sections[dw.SIM_PSU_ID]
    check("fremde Bedienung des belegten Geraets -> 409", seen["foreign"] == 409, str(seen["foreign"]))
    check("Pruefung mit Partner-Messwert bestanden", results and results[0][1], str(results))
    check("Sollwert beim Besitzer angekommen", mock_b is not None and mock_b.get_setpoint()[0] == 5.0,
          str(mock_b.get_setpoint() if mock_b else None))
    check("eigenes Netzteil des Leitstands bedient", window_a._worker._psus[dw.SIM_PSU_ID].get_setpoint()[0] == 3.0)
    released = spin(app, 3.0, lambda: leases_b.holder_of(dw.SIM_PSU_ID) is None)
    check("nach dem Ende freigegeben", released)
    spin(app, 0.5)
    check("Kachel beim Besitzer wieder frei", window_b.control_tab.lease_text(dw.SIM_PSU_ID) == ""
          and section_b._voltage_spin.isEnabled() and section_b._output_on_button.isEnabled())

    print("refuse -- Lauf startet nicht")
    save_steps([TestStep(device_kind="psu", device_id=partner_psu, action="PSU_ARB", duration=1.0)], path)
    tab_a._load_path(path)
    finished.clear()
    window_a._on_run_requested()
    spin(app, 1.0)
    check("Arbitraersignal am Partnergeraet -> nicht gestartet",
          not window_a._test_runner.is_running() and "Verbund nicht" in tab_a._status_label.text(),
          tab_a._status_label.text())

    print("alloff -- ALLE AUS am Leitstand waehrend des Laufs")
    long_steps = [TestStep(device_kind="psu", device_id=partner_psu, action="PSU_OUT_ON", duration=0.2),
                  TestStep(step_type="wait", duration=30.0)]
    save_steps(long_steps, path)
    tab_a._load_path(path)
    failed.clear()
    window_a._on_run_requested()
    spin(app, 10.0, lambda: window_a._test_runner.is_running() and leases_b.holder_of(dw.SIM_PSU_ID) is not None)
    window_a._all_off_button.click()
    spin(app, 3.0, lambda: bool(failed))
    check("Lauf abgebrochen mit 'ALLE AUS'", failed and "ALLE AUS" in failed[-1], str(failed))
    check("Belegung freigegeben", spin(app, 3.0, lambda: leases_b.holder_of(dw.SIM_PSU_ID) is None))

    print("lost -- Partner nicht mehr erreichbar")
    failed.clear()
    window_a._on_run_requested()
    started = spin(app, 10.0, lambda: window_a._test_runner.is_running()
                   and leases_b.holder_of(dw.SIM_PSU_ID) is not None and mock_b.get_output() is True)
    check("Lauf laeuft, Ausgang beim Besitzer EIN", started, str(mock_b.get_output()))
    t0 = time.monotonic()
    server_b.stop()   # Netz weg: weder Lebenszeichen noch Freigabe kommen durch
    off = spin(app, 8.0, lambda: mock_b.get_output() is False)
    took_off = time.monotonic() - t0
    check("Besitzer schaltet nach ~3 s ohne Lebenszeichen ab", off and 2.5 <= took_off <= 5.0, f"{took_off:.1f} s")
    aborted = spin(app, 8.0, lambda: bool(failed))
    took_abort = time.monotonic() - t0
    check("Leitstand bricht den Lauf ab", aborted and "verloren" in failed[-1], str(failed))
    check("... nach etwa 3 s", aborted and took_abort <= 6.0, f"{took_abort:.1f} s")
    check("Belegung beim Besitzer abgeraeumt", leases_b.holder_of(dw.SIM_PSU_ID) is None)
    spin(app, 0.5)
    check("Kachel beim Besitzer wieder frei", window_b.control_tab.lease_text(dw.SIM_PSU_ID) == "")

    for window in (window_a, window_b):
        window.close()
    app.processEvents()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
        return 1
    print("Alles ok.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
