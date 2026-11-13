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

from run_record import RunRecord
from run_session import exit_code
from testcase_model import COND_FIELD_UNITS, action_label, check_summary
from version import __version__

RESULT_FORMAT = "labcontrol-run-result"
RESULT_VERSION = 1


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) if ts else ""


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
            "unit": COND_FIELD_UNITS.get(step.check_field, ""),
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
    }


def write_json(record: RunRecord, path: Path, interrupted: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result_dict(record, interrupted), indent=2, ensure_ascii=False), encoding="utf-8")


def junit_tree(record: RunRecord, interrupted: bool = False) -> ET.ElementTree:
    rows = check_rows(record)
    name = record.testcase_name or "labcontrol"
    duration = max(0.0, record.ended_at - record.started_at) if record.ended_at else 0.0
    failures = sum(1 for r in rows if r["verdict"] == "failed")
    skipped = sum(1 for r in rows if r["verdict"] == "not_run")
    aborted = interrupted or record.outcome in ("error", "stopped")
    errors = 1 if aborted and not (record.outcome == "error" and exit_code(record) == 1) else 0
    suites = ET.Element("testsuites", name="LabControl", tests=str(len(rows) + errors),
                        failures=str(failures), errors=str(errors), time=f"{duration:.3f}")
    suite = ET.SubElement(suites, "testsuite", name=name, tests=str(len(rows) + errors),
                          failures=str(failures), errors=str(errors), skipped=str(skipped),
                          time=f"{duration:.3f}", timestamp=_iso(record.started_at),
                          hostname=socket.gethostname())
    props = ET.SubElement(suite, "properties")
    ET.SubElement(props, "property", name="labcontrol.version", value=__version__)
    for req in record.meta.get("requirements", []):
        ET.SubElement(props, "property", name="requirement", value=req)
    if record.meta.get("source_sink"):
        ET.SubElement(props, "property", name="interlock", value="released")
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
    if errors:
        case = ET.SubElement(suite, "testcase", classname=name, name="Lauf", time=f"{duration:.3f}")
        if interrupted:
            message = "unterbrochen"
        elif record.outcome == "stopped":
            message = "gestoppt"
        else:
            message = f"Schritt {record.error_index + 1}: {record.error_message}"
        ET.SubElement(case, "error", type=record.outcome if not interrupted else "interrupted", message=message)
    ET.indent(suites)
    return ET.ElementTree(suites)


def write_junit(record: RunRecord, path: Path, interrupted: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    junit_tree(record, interrupted).write(path, encoding="utf-8", xml_declaration=True)
