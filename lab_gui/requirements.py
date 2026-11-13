"""Anforderungen (FT-3, 0.59.3): Register, Urteil je Anforderung, Abdeckung ueber Laeufe.

Register (FP-22 d): StrictDoc-Dateien (.sdoc, z. B. neXtBSW doc/requirements/safety/ssr.sdoc) direkt, dazu
einfache Tabellen fuer andere Projekte: CSV (Spalten ID/UID und Titel, sonst die ersten beiden) und Markdown
(Tabellenzeilen, erste Spalte ID, zweite Titel). Das Register liefert nur IDs und Titel fuer die
Vervollstaendigung und den Bericht; Anforderungen ohne Registereintrag gehen trotzdem.

Urteil (FP-23 a): bestanden nur, wenn mindestens eine Pruefung lief und alle bestanden; eine verletzte ->
nicht bestanden; keine gelaufene Pruefung -> nicht geprueft (eine uebersprungene Pruefung erzeugt keinen
Nachweis). Anforderungen des ganzen Ablaufs (meta) gelten fuer alle Pruefungen des Laufs; sie sind nur
bestanden, wenn der Lauf vollstaendig durchlief.

Abdeckung (FP-24 a): `labcontrol coverage` liest run_*.json (run_results.py) und schreibt CSV oder Markdown.

Qt-frei.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

PASSED, FAILED, NOT_TESTED = "passed", "failed", "not_tested"
VERDICT_DE = {PASSED: "bestanden", FAILED: "nicht bestanden", NOT_TESTED: "nicht geprüft"}
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*[0-9][A-Za-z0-9_.()-]*$")


# -- Register ----------------------------------------------------------------------

def parse_sdoc(text: str, source: str = "") -> dict[str, dict]:
    """StrictDoc: jeder Knoten [NAME] mit UID; Titel aus TITLE, sonst erste Zeile von STATEMENT."""
    result: dict[str, dict] = {}
    node: dict | None = None
    in_block = None
    for line in text.splitlines():
        if in_block is not None:
            if line.strip() == "<<<":
                in_block = None
            elif in_block == "STATEMENT" and node is not None and not node.get("statement"):
                node["statement"] = line.strip()
            continue
        if re.match(r"^\[[A-Z_]+\]$", line) and line not in ("[DOCUMENT]", "[GRAMMAR]", "[TEXT]"):
            node = {"kind": line.strip("[]")}
            continue
        if line.startswith("[[") or line in ("[DOCUMENT]", "[GRAMMAR]", "[TEXT]"):
            node = None
            continue
        if node is None:
            continue
        match = re.match(r"^([A-Z_]+):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        if value == ">>>":
            in_block = key
            continue
        if key == "UID":
            node["id"] = value.strip()
            result[node["id"]] = node
        elif key == "TITLE":
            node["title"] = value.strip()
        elif key == "STATUS":
            node["status"] = value.strip()
    return {uid: {"title": n.get("title") or n.get("statement", "")[:120], "kind": n.get("kind", ""),
                  "status": n.get("status", ""), "source": source} for uid, n in result.items()}


def parse_table(text: str, source: str = "", markdown: bool = False) -> dict[str, dict]:
    rows: list[list[str]] = []
    if markdown:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("|") and not re.match(r"^\|[\s:|-]+\|?$", line):
                rows.append([c.strip() for c in line.strip("|").split("|")])
    else:
        dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t") if text.strip() else csv.excel
        rows = [r for r in csv.reader(io.StringIO(text), dialect) if r]
    if not rows:
        return {}
    header = [c.strip().lower() for c in rows[0]]
    id_col = next((i for i, h in enumerate(header) if h in ("id", "uid", "anforderung", "requirement")), None)
    title_col = next((i for i, h in enumerate(header) if h in ("title", "titel", "name", "beschreibung")), None)
    body = rows[1:] if id_col is not None else rows
    id_col = id_col if id_col is not None else 0
    title_col = title_col if title_col is not None else 1
    result = {}
    for row in body:
        if len(row) <= id_col:
            continue
        rid = row[id_col].strip().strip("`*")
        if not _ID_RE.match(rid):
            continue
        title = row[title_col].strip() if len(row) > title_col else ""
        result[rid] = {"title": title, "kind": "", "status": "", "source": source}
    return result


def load_register(paths) -> dict[str, dict]:
    register: dict[str, dict] = {}
    for raw in paths or []:
        path = Path(str(raw)).expanduser()
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            logger.warning("Anforderungsregister %s nicht lesbar: %s", path, exc)
            continue
        suffix = path.suffix.lower()
        try:
            if suffix == ".sdoc":
                entries = parse_sdoc(text, path.name)
            elif suffix in (".md", ".markdown"):
                entries = parse_table(text, path.name, markdown=True)
            else:
                entries = parse_table(text, path.name)
        except (csv.Error, ValueError) as exc:
            logger.warning("Anforderungsregister %s nicht auswertbar: %s", path, exc)
            continue
        for rid, entry in entries.items():
            register.setdefault(rid, entry)
    return register


# -- Urteil je Lauf -------------------------------------------------------------------

def verdicts(record, register: dict | None = None) -> list[dict]:
    """Je Anforderungs-ID des Laufs: Urteil, Pruefungen (gelaufen/verletzt), Schritte."""
    register = register or {}
    per_id: dict[str, dict] = {}

    def entry(rid: str) -> dict:
        return per_id.setdefault(rid, {"id": rid, "title": register.get(rid, {}).get("title", ""),
                                       "executions": 0, "failed": 0, "steps": [], "scope": "step"})

    results_by_step: dict[int, list] = {}
    for event in record.events:
        if event.kind == "result":
            results_by_step.setdefault(event.index, []).append(event.passed)
    for index, step in enumerate(record.steps):
        for rid in step.requirements:
            item = entry(rid)
            item["steps"].append(index + 1)
            runs = results_by_step.get(index, []) if step.check_enabled else []
            item["executions"] += len(runs)
            item["failed"] += sum(1 for ok in runs if not ok)
    all_runs = [ok for runs in results_by_step.values() for ok in runs]
    for rid in record.meta.get("requirements", []):
        item = entry(rid)
        item["scope"] = "testcase" if not item["steps"] else "both"
        item["executions"] += len(all_runs)
        item["failed"] += sum(1 for ok in all_runs if not ok)
        item["complete_run_needed"] = True
    for item in per_id.values():
        needs_complete_run = item.pop("complete_run_needed", False)
        if item["failed"]:
            item["verdict"] = FAILED
        elif item["executions"] and (not needs_complete_run or record.outcome == "passed"):
            item["verdict"] = PASSED
        else:
            item["verdict"] = NOT_TESTED
    return sorted(per_id.values(), key=lambda i: i["id"])


# -- Abdeckung ueber Laeufe (labcontrol coverage) ----------------------------------------

def collect_results(folders) -> list[dict]:
    results = []
    for folder in folders:
        for path in sorted(Path(folder).glob("run_*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("format") == "labcontrol-run-result":
                data["_file"] = str(path)
                results.append(data)
    return sorted(results, key=lambda r: r.get("started") or "")


def coverage(results: list[dict], register: dict | None = None) -> list[dict]:
    """Je ID: letztes Urteil (juengster Lauf, der sie nennt), Anzahl Laeufe/bestanden/nicht bestanden.
    Registereintraege ohne Lauf erscheinen als nicht geprueft."""
    register = register or {}
    rows: dict[str, dict] = {}
    for result in results:
        for item in result.get("requirements", []):
            row = rows.setdefault(item["id"], {"id": item["id"], "title": item.get("title", ""), "runs": 0,
                                               "passed": 0, "failed": 0})
            row["runs"] += 1
            row["passed"] += item["verdict"] == PASSED
            row["failed"] += item["verdict"] == FAILED
            row.update(verdict=item["verdict"], last_run=result.get("started", ""),
                       testcase=result.get("testcase", ""), host=result.get("host", ""))
            row["title"] = row["title"] or item.get("title", "")
    for rid, entry in register.items():
        row = rows.setdefault(rid, {"id": rid, "title": entry.get("title", ""), "runs": 0, "passed": 0,
                                    "failed": 0, "verdict": NOT_TESTED, "last_run": "", "testcase": "",
                                    "host": ""})
        row["title"] = row["title"] or entry.get("title", "")
    return sorted(rows.values(), key=lambda r: r["id"])


COVERAGE_COLUMNS = ("id", "title", "verdict", "last_run", "testcase", "host", "runs", "passed", "failed")


def coverage_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(COVERAGE_COLUMNS)
    for row in rows:
        writer.writerow([row.get(c, "") for c in COVERAGE_COLUMNS])
    return out.getvalue()


def coverage_markdown(rows: list[dict]) -> str:
    def cell(value) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| ID | Titel | Urteil | Letzter Lauf | Ablauf | Läufe (bestanden/nicht bestanden) |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {cell(row['id'])} | {cell(row.get('title', ''))} | "
                     f"{VERDICT_DE.get(row.get('verdict', NOT_TESTED), row.get('verdict', ''))} | "
                     f"{cell(row.get('last_run', ''))} | {cell(row.get('testcase', ''))} | "
                     f"{row.get('runs', 0)} ({row.get('passed', 0)}/{row.get('failed', 0)}) |")
    return "\n".join(lines) + "\n"
