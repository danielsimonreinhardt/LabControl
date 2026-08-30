"""Export einer Recorder-Aufzeichnung (siehe recording.py) als CSV oder MF4.

CSV im Long-Format (eine Zeile je Messwert: Zeitstempel, Geraet, Kanal, Wert)
statt einer Geraete-x-Kanal-Pivot-Tabelle -- so bleibt der Export verlustfrei
und einfach, auch wenn Geraete unterschiedlich schnell/unregelmaessig
antworten (z.B. nach einem kurzen Verbindungsabbruch) und ihre Zeitstempel
dadurch nicht exakt uebereinstimmen.

MF4 (ASAM MDF4, ueber die optionale asammdf-Abhaengigkeit) bildet jeden
Geraet+Kanal als eigenes Signal mit eigenem Zeitvektor ab -- passt ohne
Resampling zum Long-Format und ist das native Format fuer Mess-/Log-Tools
(z.B. CANape, Vector-Toolchain), falls die Aufzeichnung dort weiterverarbeitet
werden soll.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from recording import FIELD_INFO, Sample


def export_csv(path: Path, samples: list[Sample], device_meta: dict[str, tuple[str, str]]) -> None:
    t0 = samples[0].t if samples else 0.0
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            ["Zeitstempel", "Sekunden seit Start", "Geraet-ID", "Geraet", "Geraeteart", "Kanal", "Einheit", "Wert"]
        )
        for s in samples:
            kind, label = device_meta.get(s.device_id, ("", s.device_id))
            name, unit = FIELD_INFO.get((kind, s.field), (s.field, ""))
            writer.writerow(
                [
                    datetime.fromtimestamp(s.t).isoformat(sep=" ", timespec="milliseconds"),
                    f"{s.t - t0:.3f}",
                    s.device_id,
                    label,
                    kind,
                    name,
                    unit,
                    repr(s.value),
                ]
            )


def export_mf4(path: Path, samples: list[Sample], device_meta: dict[str, tuple[str, str]]) -> None:
    if not samples:
        raise ValueError("Keine Messwerte zum Exportieren vorhanden")

    # Import hier statt am Modulkopf: asammdf ist eine optionale, recht
    # schwere Abhaengigkeit (zieht u.a. numpy/pandas mit) -- CSV-Export soll
    # auch funktionieren, wenn sie mal nicht installiert ist.
    import numpy as np
    from asammdf import MDF, Signal as MdfSignal

    t0 = samples[0].t
    grouped: dict[tuple[str, str], list[Sample]] = {}
    for s in samples:
        grouped.setdefault((s.device_id, s.field), []).append(s)

    used_names: set[str] = set()
    signals = []
    for (device_id, field), items in grouped.items():
        kind, label = device_meta.get(device_id, ("", device_id))
        name, unit = FIELD_INFO.get((kind, field), (field, ""))
        channel_name = _unique_channel_name(f"{label}_{name}", used_names)
        timestamps = np.array([it.t - t0 for it in items], dtype=np.float64)
        values = np.array([it.value for it in items], dtype=np.float64)
        signals.append(MdfSignal(samples=values, timestamps=timestamps, name=channel_name, unit=unit))

    mdf = MDF()
    try:
        mdf.append(signals)
        mdf.header.start_time = datetime.fromtimestamp(t0)
        mdf.save(str(path), overwrite=True)
    finally:
        mdf.close()


def _unique_channel_name(base: str, used: set[str]) -> str:
    # MF4-Kanalnamen muessen innerhalb einer Datei eindeutig sein; zwei
    # gleich benannte Geraete (bevor der Nutzer sie umbenennt) wuerden sonst
    # denselben Kanalnamen liefern.
    safe = base.replace(" ", "_")
    name = safe
    suffix = 2
    while name in used:
        name = f"{safe}_{suffix}"
        suffix += 1
    used.add(name)
    return name
