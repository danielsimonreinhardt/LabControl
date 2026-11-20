"""Maschinenlesbare Laufergebnisse (0.58.0, FT-4): run-result.json und JUnit-XML fuer CI.

run-result.json ist die Grundlage fuer die Anforderungs-Abdeckung (FT-3) und spaeter Trends (FT-11):
ein Eintrag je Pruefschritt mit allen Ausfuehrungen, dazu Ergebnis, Geraete und Ablauf-Eigenschaften.
JUnit: eine <testsuite> je Lauf, ein <testcase> je Pruefschritt; ein Abbruch/Fehler ausserhalb einer
Pruefung erscheint als eigener <testcase> mit <error>. Anforderungs-IDs stehen als <property>.
"""
from __future__ import annotations

import json
import socket
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requirements
from run_record import RunRecord
from run_session import exit_code
from testcase_model import COND_FIELD_UNITS, action_label, check_summary, check_unit, monitor_summary
from version import __version__

RESULT_FORMAT = "labcontrol-run-result"
RESULT_VERSION = 1


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) if ts else ""


# Zielaktion eines Arbitraersignals -> Messgroesse fuer Soll gegen Ist (RES hat keine passende Messung)
SETPOINT_FIELDS = {"PSU_VOLT": "voltage", "VOLT": "voltage", "PSU_CURR": "current", "CURR": "current",
                   "POW": "power"}


def setpoint_runs(record: RunRecord) -> list[dict]:
    """Je Arbitraersignal-Schritt (0.62.0, FT-2): gesendete Sollwerte und tatsaechliche Abstaende (Senden zu
    Senden, nur innerhalb einer Ausfuehrung). "executions" sind die (t, Wert)-Listen je Ausfuehrung."""
    starts = sorted((e.t, e.index) for e in record.events if e.kind == "started")
    by_index: dict[int, dict] = {}
    for t, index, device_id, target, value in record.setpoints:
        item = by_index.setdefault(index, {"index": index, "device_id": device_id, "target": target,
                                           "executions": [], "_last_start": None})
        # zugehoerige Ausfuehrung = letzter "started" dieses Schritts vor t
        started = max((ts for ts, i in starts if i == index and ts <= t + 1e-6), default=None)
        if started != item["_last_start"] or not item["executions"]:
            item["executions"].append([])
            item["_last_start"] = started
        item["executions"][-1].append((t, value))
    result = []
    for index in sorted(by_index):
        item = by_index[index]
        item.pop("_last_start")
        gaps = [(b[0] - a[0]) * 1000.0 for run in item["executions"] for a, b in zip(run, run[1:])]
        values = [v for run in item["executions"] for _t, v in run]
        item.update(count=len(values), first=values[0], last=values[-1],
                    interval_ms={"min": round(min(gaps), 1), "mean": round(sum(gaps) / len(gaps), 1),
                                 "max": round(max(gaps), 1)} if gaps else None)
        result.append(item)
    return result


def monitor_unit(result: dict, step) -> str:
    """Einheit des Messwerts einer Zeitpruefung: band = Einheit der Quelle, reach = s, can_gap = ms."""
    mode = result["config"]["mode"]
    if mode == "reach":
        return "s"
    if mode == "can_gap":
        return "ms"
    return COND_FIELD_UNITS.get(getattr(step, "cond_field", ""), "") if step is not None else ""


def timing_rows(record: RunRecord) -> list[dict]:
    """Zeitpruefungen (0.63.0, FT-7) fuer run-result.json und JUnit, ohne Verlauf."""
    rows = []
    labels = {device_id: label for device_id, (_kind, label) in record.device_meta.items()}
    for result in record.monitors:
        index = result["index"]
        step = record.steps[index] if 0 <= index < len(record.steps) else None
        rows.append({"step": index + 1, "mode": result["config"]["mode"],
                     "check": monitor_summary(step, labels) if step is not None else "",
                     "passed": result["passed"], "value": round(result["value"], 6),
                     "unit": monitor_unit(result, step), "message": result["message"],
                     "resolution_ms": round(result["resolution_s"] * 1000.0, 1)
                     if result.get("resolution_s") is not None else None,
                     "samples": result["samples"], "source": result["source"]})
    return rows


def setpoint_summary(record: RunRecord) -> list[dict]:
    """setpoint_runs ohne die Einzelwerte (fuer run-result.json)."""
    return [{"step": item["index"] + 1, "device_id": item["device_id"], "target": item["target"],
             "executions": len(item["executions"]), "setpoints": item["count"], "first": item["first"],
             "last": item["last"], "interval_ms": item["interval_ms"]} for item in setpoint_runs(record)]


def scope_capture_rows(record: RunRecord) -> list[dict]:
    """Oszilloskop-Erfassungen ohne Bild (fuer run-result.json und den Bericht)."""
    keys = ("capture_id", "device_id", "csv_path", "triggered", "measurement", "channel", "value", "unit",
            "summary", "warnings")
    return [{"step": entry["index"] + 1, "arm_step": entry.get("arm_index", -1) + 1,
             **{k: entry.get(k) for k in keys}} for entry in getattr(record, "scope_captures", [])]


def check_rows(record: RunRecord) -> list[dict]:
    """Je Schritt mit Pruefung: Ausfuehrungen, Fehlschlaege, letzter Messwert, Anforderungen."""
    rows = []
    for index, step in enumerate(record.steps):
        if step.step_type != "action" or not step.check_enabled:
            continue
        results = [e for e in record.events if e.kind == "result" and e.index == index]
        rows.append({
            "index": index + 1,
            "device_id": step.device_id,
            "device": record.device_meta.get(step.device_id, ("", step.device_id))[1] if step.device_id else "",
            "kind": step.device_kind,
            "action": step.action,
            "action_label": action_label(step.device_kind, step.action),
            "check": check_summary(step),
            "field": step.check_field,
            "unit": check_unit(step),
            "min": step.check_min,
            "max": step.check_max,
            "executions": len(results),
            "failed": sum(1 for e in results if not e.passed),
            "values": [e.measured for e in results],
            "verdict": ("not_run" if not results else
                        "failed" if any(not e.passed for e in results) else "passed"),
            "requirements": list(step.requirements),
        })
    return rows


def shutdown_dict(record: RunRecord) -> dict | None:
    """Abschaltung beim Abbruch fuer run-result.json und get_run: mode ("block" = Abbruch-Block, "standard" =
    geschaltete Geraete und Ausloeser, "watchdog" = Sicherheitsabschaltung, "all_off" = ALLE AUS), fallback,
    off/unreachable (Kennungen), block (Schritte mit Ergebnis), labels."""
    shutdown = getattr(record, "shutdown", None)
    if not shutdown:
        return None
    keys = ("mode", "cause", "message", "trigger", "fallback", "timed_out", "devices", "off", "unreachable",
            "block", "labels", "escalated")
    return {key: shutdown[key] for key in keys if key in shutdown}


def result_dict(record: RunRecord, interrupted: bool = False) -> dict:
    used = sorted({s.device_id for s in record.samples} | {st.device_id for st in record.steps if st.device_id})
    return {
        "format": RESULT_FORMAT,
        "version": RESULT_VERSION,
        "labcontrol": __version__,
        "host": socket.gethostname(),
        "testcase": record.testcase_name,
        "meta": dict(record.meta),
        "started": _iso(record.started_at),
        "ended": _iso(record.ended_at),
        "duration_s": round(max(0.0, record.ended_at - record.started_at), 3) if record.ended_at else None,
        "outcome": "interrupted" if interrupted else record.outcome,
        "exit_code": exit_code(record, interrupted),
        "error": ({"step": record.error_index + 1, "message": record.error_message}
                  if record.outcome == "error" else None),
        "checks_total": record.checks_total,
        "checks_failed": record.checks_failed,
        "checks": check_rows(record),
        "devices": {d: {"kind": record.device_meta.get(d, ("", ""))[0],
                        "label": record.device_meta.get(d, ("", d))[1]} for d in used},
        "interlock": {"groups": record.interlock_groups,
                      "released": bool(record.meta.get("source_sink"))},
        "instruments": list(record.instruments),
        "requirements": requirements.verdicts(record, record.requirement_register),
        "setpoints": setpoint_summary(record),
        "timing_checks": timing_rows(record),
        # Oszilloskop-Erfassungen der Warteschritte (1.0.1, PI-72 a); Kurve als CSV unter csv_path
        "scope_captures": scope_capture_rows(record),
        # Abschaltung beim Abbruch (1.0.1, PI-76 a/PI-79 a), None = normal beendet
        "shutdown": shutdown_dict(record),
        "warnings": [f"Kalibrierung abgelaufen: {r['label']} (fällig {r['due']})"
                     for r in record.instruments if r.get("expired")],
    }


def write_json(record: RunRecord, path: Path, interrupted: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result_dict(record, interrupted), indent=2, ensure_ascii=False), encoding="utf-8")


def junit_tree(record: RunRecord, interrupted: bool = False) -> ET.ElementTree:
    rows = check_rows(record)
    timing = timing_rows(record)
    name = record.testcase_name or "labcontrol"
    duration = max(0.0, record.ended_at - record.started_at) if record.ended_at else 0.0
    failures = sum(1 for r in rows if r["verdict"] == "failed") + sum(1 for r in timing if not r["passed"])
    skipped = sum(1 for r in rows if r["verdict"] == "not_run")
    aborted = interrupted or record.outcome in ("error", "stopped")
    errors = 1 if aborted and not (record.outcome == "error" and exit_code(record) == 1) else 0
    tests = len(rows) + len(timing) + errors
    suites = ET.Element("testsuites", name="LabControl", tests=str(tests),
                        failures=str(failures), errors=str(errors), time=f"{duration:.3f}")
    suite = ET.SubElement(suites, "testsuite", name=name, tests=str(tests),
                          failures=str(failures), errors=str(errors), skipped=str(skipped),
                          time=f"{duration:.3f}", timestamp=_iso(record.started_at),
                          hostname=socket.gethostname())
    props = ET.SubElement(suite, "properties")
    ET.SubElement(props, "property", name="labcontrol.version", value=__version__)
    for req in record.meta.get("requirements", []):
        ET.SubElement(props, "property", name="requirement", value=req)
    for item in requirements.verdicts(record, record.requirement_register):   # Urteil je Anforderung (FT-3)
        ET.SubElement(props, "property", name=f"requirement.{item['id']}", value=item["verdict"])
    if record.meta.get("source_sink"):
        ET.SubElement(props, "property", name="interlock", value="released")
    for row in record.instruments:   # Messmittel (FT-8)
        ET.SubElement(props, "property", name="instrument",
                      value=" ".join(v for v in (row["device_id"], row.get("model", ""), row.get("serial", ""),
                                                 f"fällig {row['due']}" if row.get("due") else "") if v))
    expired = [r for r in record.instruments if r.get("expired")]
    if expired:
        ET.SubElement(props, "property", name="calibration.expired",
                      value=", ".join(r["device_id"] for r in expired))
    for row in rows:
        case = ET.SubElement(suite, "testcase", classname=name,
                             name=f"{row['index']:03d} {row['action_label']} {row['check']}".strip(), time="0")
        if row["requirements"]:
            case_props = ET.SubElement(case, "properties")
            for req in row["requirements"]:
                ET.SubElement(case_props, "property", name="requirement", value=req)
        if row["verdict"] == "failed":
            values = ", ".join(f"{v:g}" for v in row["values"] if v is not None)
            failure = ET.SubElement(case, "failure", type="check",
                                    message=f"{row['failed']}/{row['executions']} außerhalb {row['check']}")
            failure.text = values
        elif row["verdict"] == "not_run":
            ET.SubElement(case, "skipped", message="nicht ausgeführt")
    for row in timing:   # Zeitpruefungen (FT-7): je Ausfuehrung eines Blocks "Ueberwachen" ein Testfall
        case = ET.SubElement(suite, "testcase", classname=name,
                             name=f"{row['step']:03d} Überwachen {row['check']}".strip(), time="0")
        if not row["passed"]:
            failure = ET.SubElement(case, "failure", type="timing", message=row["message"] or row["check"])
            failure.text = f"{row['value']:g} {row['unit']}".strip()
    if errors:
        case = ET.SubElement(suite, "testcase", classname=name, name="Lauf", time=f"{duration:.3f}")
        if interrupted:
            message = "unterbrochen"
        elif record.outcome == "stopped":
            message = "gestoppt"
        else:
            message = f"Schritt {record.error_index + 1}: {record.error_message}"
        ET.SubElement(case, "error", type=record.outcome if not interrupted else "interrupted", message=message)
    if expired:   # Warnung, kein Fehler (FP-21 a)
        err = ET.SubElement(suite, "system-err")
        err.text = "\n".join(f"WARNUNG: Kalibrierung abgelaufen: {r['label']} ({r['device_id']}, fällig {r['due']})"
                             for r in expired)
    ET.indent(suites)
    return ET.ElementTree(suites)


def write_junit(record: RunRecord, path: Path, interrupted: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    junit_tree(record, interrupted).write(path, encoding="utf-8", xml_declaration=True)
