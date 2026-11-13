"""Prueft die Delta-Ansicht der CAN-Kachel im Control-Reiter (CanControlGroup): eine Zeile je ID
mit dem Zeitabstand zum vorigen Frame derselben ID und dem aktuellen Payload.
Offscreen, ohne Hardware.

Aufruf:  python tools/check_can_delta.py [--shot DATEI.png]   (aus dem Repo-Stamm)
"""
from __future__ import annotations

import os
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "device-driver"))
sys.path.insert(0, str(ROOT / "lab_gui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
import control_tab as ct  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FEHL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def cells(group: "ct.CanControlGroup") -> list[tuple[str, str, str, str]]:
    t = group._delta_table
    return [tuple(t.item(r, c).text() for c in range(4)) for r in range(t.rowCount())]


def decoded(**values: float):
    return SimpleNamespace(signals=[SimpleNamespace(name=n, value=v, unit="V") for n, v in values.items()])


group = ct.CanControlGroup("can:test", "CAN-Bus 1")
group.resize(520, 420)

print("Umschalter (Icon-Button)")
check("Verlauf ist Standard", group._table_stack.currentWidget() is group._traffic_table)
group._delta_button.setChecked(True)
check("Haken zeigt die Delta-Tabelle", group._table_stack.currentWidget() is group._delta_table)
group._delta_button.setChecked(False)
check("Haken weg zeigt wieder den Verlauf", group._table_stack.currentWidget() is group._traffic_table)

print("Eine Zeile je ID, Delta und aktueller Payload")
group.append_frame(0x130, "01 02", False, 10.000)
group.append_frame(0x120, "AA", False, 10.003)
group.append_frame(0x130, "03 04", False, 10.010)
group.append_frame(0x130, "05 06", False, 10.030)
rows = cells(group)
check("zwei IDs, zwei Zeilen", len(rows) == 2, str(rows))
check("nach ID sortiert (0x120 vor 0x130)", [r[0] for r in rows] == ["0x120", "0x130"], str(rows))
check("erster Frame einer ID: kein Delta", rows[0][1] == "–", rows[0][1])
check("Delta 0x130 = 20,0 ms (zwischen den letzten beiden)", rows[1][1] == "20.0 ms", rows[1][1])
check("Payload ist der aktuelle", rows[1][2] == "05 06" and rows[0][2] == "AA", str(rows))
check("Verlauf-Tabelle bleibt vollstaendig (4 Zeilen)", group._traffic_table.rowCount() == 4)

print("Anzeige des Deltas")
check("unter 1 s in ms", ct._format_can_delta(0.0125) == "12.5 ms", ct._format_can_delta(0.0125))
check("ab 1 s in s", ct._format_can_delta(1.5) == "1.500 s", ct._format_can_delta(1.5))
check("kein Vorgaenger", ct._format_can_delta(None) == "–")

print("Standard- und Extended-ID mit gleichem Zahlenwert sind verschieden")
group.append_frame(0x120, "BB", True, 10.050)
rows = cells(group)
check("drei Zeilen", len(rows) == 3, str(rows))
check("sortiert: Standard vor Extended bei gleicher ID", [r[0] for r in rows] == ["0x120", "0x120", "0x130"], str(rows))
check("Standard-0x120 behaelt seinen Payload", rows[0][2] == "AA" and rows[1][2] == "BB", str(rows))

print("Zeitstempel springt zurueck (Neustart der Zeitbasis): kein negatives Delta")
group.append_frame(0x130, "07", False, 1.0)
check("Delta '–'", cells(group)[2][1] == "–", str(cells(group)))

print("DBC-Signale")
group.append_frame(0x121, "00", False, 20.0)
group.append_decoded_signals(0x121, decoded(Spannung=3.7))
row = [r for r in cells(group) if r[0] == "0x121"][0]
check("Signale in der Delta-Zeile", "Spannung=" in row[3], str(row))
group.append_frame(0x121, "01", False, 20.1)
row = [r for r in cells(group) if r[0] == "0x121"][0]
check("neuer Frame ohne Decodierung loescht die alte Signalspalte", row[3] == "", str(row))
check("Delta 100,0 ms", row[1] == "100.0 ms", row[1])

print("Leeren")
group.clear_traffic()
check("beide Tabellen leer", group._delta_table.rowCount() == 0 and group._traffic_table.rowCount() == 0)
group.append_frame(0x130, "01", False, 30.0)
check("nach dem Leeren wieder erster Frame ohne Delta", cells(group) == [("0x130", "–", "01", "")], str(cells(group)))

print("Viele Frames")
group.clear_traffic()
for i in range(5000):
    group.append_frame(0x100 + i % 20, "00 11 22 33", False, i * 0.001)
check("20 IDs = 20 Zeilen", group._delta_table.rowCount() == 20)
check("Verlauf bleibt begrenzt", group._traffic_table.rowCount() == ct.CAN_TRAFFIC_ROW_LIMIT)
check("Delta 20 ms bei jeder 20. Wiederholung", cells(group)[0][1] == "20.0 ms", cells(group)[0][1])

print("Spaltenbreite springt nicht (Nutzerfeedback 13.11.2026: 99 ms <-> 101 ms)")
group.clear_traffic()
header = group._delta_table.horizontalHeader()
t = 50.0
group.append_frame(0x130, "01 02 03 04 05 06 07 08", False, t)
widths = []
for step in (0.0998, 0.1012, 0.0995, 0.1009, 0.0091, 0.1003):
    t += step
    group.append_frame(0x130, "01 02 03 04 05 06 07 08", False, t)
    widths.append(tuple(header.sectionSize(c) for c in range(3)))
check("ID/Δt/Daten gleich breit bei 99,8/101,2/9,1 ms", len(set(widths)) == 1, str(widths))
group.append_frame(0x130, "01", False, t + 0.1)
check("kuerzere Daten machen die Spalte nicht schmaler", header.sectionSize(2) == widths[-1][2])
group.append_frame(0x131, "01", False, t + 0.2)
group.append_frame(0x131, "01", False, t + 12.5)
check("langer Abstand (s) passt hinein", header.sectionSize(1) >= group._delta_table.fontMetrics().horizontalAdvance("12.300 s"))

if "--shot" in sys.argv:
    group.clear_traffic()
    for i, (cid, data) in enumerate([(0x120, "02 00 00 00"), (0x121, "05 01 FF 00"), (0x130, "01"), (0x300, "DE AD BE EF")]):
        for k in range(3):
            group.append_frame(cid, data, False, 100.0 + i * 0.002 + k * (0.01 * (i + 1)))
    group._delta_button.setChecked(True)
    group.show()
    app.processEvents()
    group.grab().save(sys.argv[sys.argv.index("--shot") + 1])

print()
if FAILURES:
    print(f"{len(FAILURES)} PRUEFUNG(EN) FEHLGESCHLAGEN: {FAILURES}")
    sys.exit(1)
print("Alles ok.")
