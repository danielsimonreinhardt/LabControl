"""Nachlauf-Report: baut aus einem RunRecord (siehe run_record.py) einen
selbst-enthaltenen HTML-Report (Inline-CSS, Diagramme als base64-PNG) bzw.
exportiert ihn als PDF.

Bewusst kein externes Stylesheet und keine data:-URIs im PDF-Pfad (siehe
export_pdf) -- QTextDocument versteht nur ein Subset von HTML/CSS, Tabellen
mit Inline-Styles funktionieren dort zuverlaessig in beiden Faellen (Browser
und QTextDocument). Der Report ist bewusst theme-neutral (fester heller
Print-Look), damit er unabhaengig vom gerade aktiven App-Theme aussieht und
gut druckbar bleibt.
"""
from __future__ import annotations

import base64
import html
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QSizeF, QUrl
from PySide6.QtGui import QImage, QPageSize, QPdfWriter, QTextDocument

from i18n import tr
from paths import app_dir
from report_chart import fmt_elapsed, render_field_chart
from run_record import RunRecord
from testcase_model import (
    COND_FIELD_LABELS,
    COND_FIELD_UNITS,
    TestStep,
    action_label,
    check_summary,
    kind_label,
)
from version import __version__

REPORTS_DIR = app_dir() / "reports"

_FIELDS = [("voltage", "V"), ("current", "A"), ("power", "W")]

_COLOR_PASS = "#16a34a"
_COLOR_FAIL = "#dc2626"
_COLOR_STOPPED = "#d97706"
_BG_PASS = "#dcfce7"
_BG_FAIL = "#fee2e2"
_TEXT_MUTED = "#6b7280"
_BORDER = "#e5e7eb"

_IMG_WIDTH = 650


def _slugify(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return safe.strip("_") or "testablauf"


def _fmt_hms(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_wall(ts: float) -> str:
    if not ts:
        return "–"
    return time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(ts))


def _png_data_uri(image: QImage) -> str:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _render_charts(record: RunRecord) -> list[tuple[str, QImage]]:
    t0 = record.started_at
    t1 = record.ended_at if record.ended_at else time.time()
    step_marks = [
        (e.t, e.index + 1)
        for e in record.events
        if e.kind == "started" and 0 <= e.index < len(record.steps)
        and record.steps[e.index].step_type == "action"
    ]
    charts: list[tuple[str, QImage]] = []
    for field, unit in _FIELDS:
        if not any(s.field == field for s in record.samples):
            continue
        image = render_field_chart(field, unit, record.samples, record.device_meta, t0, t1, step_marks)
        title = tr(COND_FIELD_LABELS.get(field, field))
        charts.append((title, image))
    return charts


def _device_display(record: RunRecord, kind: str, device_id: str) -> str:
    if device_id:
        return record.device_meta.get(device_id, ("", device_id))[1]
    return tr("{kind} (automatisch)", kind=kind_label(kind))


def _verdict(record: RunRecord) -> tuple[str, str]:
    if record.outcome == "passed":
        return _COLOR_PASS, tr("BESTANDEN ({total} Prüfungen)", total=record.checks_total)
    if record.outcome == "failed_checks":
        return _COLOR_FAIL, tr(
            "NICHT BESTANDEN ({failed}/{total} Prüfungen fehlgeschlagen)",
            failed=record.checks_failed, total=record.checks_total,
        )
    if record.outcome == "stopped":
        return _COLOR_STOPPED, tr("ABGEBROCHEN (durch Benutzer gestoppt)")
    if record.outcome == "error":
        return _COLOR_FAIL, tr(
            "FEHLER bei Schritt {index}: {message}",
            index=record.error_index + 1, message=record.error_message,
        )
    return _TEXT_MUTED, tr("Lauf noch nicht abgeschlossen")


def _step_started_text(record: RunRecord, index: int) -> str:
    step = record.steps[index]
    if step.step_type in ("set_var", "inc_var"):
        op = "=" if step.step_type == "set_var" else "+="
        return tr("Schritt {n}: {var} {op} {value:g}", n=index + 1, var=step.var_name, op=op, value=step.value)
    device = _device_display(record, step.device_kind, step.device_id)
    action = action_label(step.device_kind, step.action)
    detail = f"{step.value:g}" if step.value else "–"
    return tr("Schritt {n}: {device} – {action} ({detail})", n=index + 1, device=device, action=action, detail=detail)


def _iteration_suffix(event) -> str:
    # Dieselben Uebersetzungsschluessel wie testcase_tab.on_iteration_changed
    # (ohne Leerzeichen davor, damit beide Stellen sich eine Uebersetzung
    # teilen), das Leerzeichen wird hier beim Anhaengen ergaenzt.
    if event.iteration_total:
        return " " + tr("(Durchlauf {i}/{n})", i=event.iteration, n=event.iteration_total)
    if event.iteration:
        return " " + tr("(Durchlauf {i})", i=event.iteration)
    return ""


def _timeline_rows(record: RunRecord) -> list[tuple[str, str, str | None]]:
    """Liste von (Zeitversatz-Text, Ereignistext, Zeilenfarbe-oder-None)."""
    rows: list[tuple[str, str, str | None]] = []
    for event in record.events:
        offset = fmt_elapsed(event.t - record.started_at)
        if event.kind == "started":
            text = _step_started_text(record, event.index) + _iteration_suffix(event)
            rows.append((offset, text, None))
        elif event.kind == "result":
            step = record.steps[event.index]
            unit = COND_FIELD_UNITS.get(step.check_field, "")
            verdict = tr("bestanden") if event.passed else tr("fehlgeschlagen")
            text = tr(
                "Prüfung Schritt {n}: {verdict}, gemessen {value:g} {unit}",
                n=event.index + 1, verdict=verdict, value=event.measured or 0.0, unit=unit,
            )
            rows.append((offset, text, None if event.passed else _COLOR_FAIL))
        elif event.kind == "failed":
            text = tr("FEHLER Schritt {n}: {message}", n=event.index + 1, message=event.message)
            rows.append((offset, text, _COLOR_FAIL))
    if record.events_dropped:
        rows.append(("", tr("… {n} weitere Ereignisse ausgelassen", n=record.events_dropped), None))
    return rows


def _step_result_rows(record: RunRecord) -> list[tuple[int, TestStep, bool, int, int, float | None]]:
    """Aggregierte Pruefungs-Ergebnisse je Schritt: (index, step-artige Felder
    als dict fuer die Anzeige, sticky_failed, total, failed, letzter Messwert)."""
    rows = []
    for index, step in enumerate(record.steps):
        if step.step_type != "action" or not step.check_enabled:
            continue
        results = [e for e in record.events if e.kind == "result" and e.index == index]
        total = len(results)
        failed = sum(1 for e in results if not e.passed)
        last_value = results[-1].measured if results else None
        rows.append((index, step, failed > 0, total, failed, last_value))
    return rows


def build_html(record: RunRecord, image_srcs: list[tuple[str, str]]) -> str:
    verdict_color, verdict_text = _verdict(record)
    duration = (record.ended_at - record.started_at) if record.ended_at else 0.0

    used_device_ids = {s.device_id for s in record.samples} | {
        step.device_id for step in record.steps if step.device_id
    }
    device_names = sorted(
        record.device_meta.get(did, ("", did))[1] for did in used_device_ids
    )

    parts: list[str] = []
    parts.append('<div style="font-family: Segoe UI, Arial, sans-serif; color: #1e2530; max-width: 900px;">')
    parts.append(f'<h1 style="margin-bottom:4px;">{html.escape(tr("Testablauf-Report"))}</h1>')
    parts.append(
        f'<p style="color:{_TEXT_MUTED}; margin-top:0;">'
        f'{html.escape(tr("Testablauf:"))} <b>{html.escape(record.testcase_name)}</b>'
        f' &middot; LAB CONTROL v{__version__}'
        f' &middot; {html.escape(tr("Erstellt:"))} {html.escape(_fmt_wall(time.time()))}</p>'
    )

    parts.append(
        f'<table style="width:100%; border-collapse:collapse; margin:12px 0;">'
        f'<tr><td style="background:{verdict_color}; color:#ffffff; font-weight:bold; '
        f'padding:10px 14px; border-radius:4px;">{html.escape(verdict_text)}</td></tr></table>'
    )
    if record.outcome in ("stopped", "error"):
        parts.append(
            f'<p style="color:{_COLOR_STOPPED};">'
            f'{html.escape(tr("Lauf wurde vorzeitig beendet – Report enthält Teildaten."))}</p>'
        )

    def th(text: str) -> str:
        return f'<th style="text-align:left; border-bottom:2px solid {_BORDER}; padding:4px 8px;">{html.escape(text)}</th>'

    def td(text: str, bold: bool = False) -> str:
        style = f'padding:4px 8px; border-bottom:1px solid {_BORDER};'
        if bold:
            style += 'font-weight:bold;'
        return f'<td style="{style}">{html.escape(str(text))}</td>'

    parts.append('<h2>' + html.escape(tr("Übersicht")) + '</h2>')
    parts.append('<table style="border-collapse:collapse;">')
    for label, value in (
        (tr("Start:"), _fmt_wall(record.started_at)),
        (tr("Ende:"), _fmt_wall(record.ended_at)),
        (tr("Dauer:"), _fmt_hms(duration)),
        (tr("Geräte:"), ", ".join(device_names) if device_names else "–"),
    ):
        parts.append(f'<tr>{td(label, bold=True)}{td(value)}</tr>')
    parts.append('</table>')

    step_rows = _step_result_rows(record)
    parts.append('<h2>' + html.escape(tr("Schritt-Ergebnisse")) + '</h2>')
    if not step_rows:
        parts.append(f'<p style="color:{_TEXT_MUTED};">{html.escape(tr("Keine Prüfungen definiert"))}</p>')
    else:
        parts.append('<table style="width:100%; border-collapse:collapse;">')
        parts.append(
            '<tr>' + th("#") + th(tr("Gerät")) + th(tr("Aktion")) + th(tr("Prüfung"))
            + th(tr("Ausführungen")) + th(tr("Ergebnis")) + th(tr("Gemessen")) + '</tr>'
        )
        for index, step, sticky_failed, total, failed, last_value in step_rows:
            row_bg = _BG_FAIL if sticky_failed else _BG_PASS
            device = _device_display(record, step.device_kind, step.device_id)
            action = action_label(step.device_kind, step.action)
            unit = COND_FIELD_UNITS.get(step.check_field, "")
            result_text = (
                tr("{failed}/{total} fehlgeschlagen", failed=failed, total=total)
                if sticky_failed else tr("{total}/{total} bestanden", total=total)
            )
            measured_text = f"{last_value:g} {unit}".rstrip() if last_value is not None else "–"
            cell_style = f'padding:4px 8px; border-bottom:1px solid {_BORDER}; background:{row_bg};'
            parts.append(
                f'<tr>'
                f'<td style="{cell_style}">{index + 1}</td>'
                f'<td style="{cell_style}">{html.escape(device)}</td>'
                f'<td style="{cell_style}">{html.escape(action)}</td>'
                f'<td style="{cell_style}">{html.escape(check_summary(step))}</td>'
                f'<td style="{cell_style}">{total}</td>'
                f'<td style="{cell_style}">{html.escape(result_text)}</td>'
                f'<td style="{cell_style}">{html.escape(measured_text)}</td>'
                f'</tr>'
            )
        parts.append('</table>')

    parts.append('<h2>' + html.escape(tr("Zeitverlauf")) + '</h2>')
    parts.append('<table style="width:100%; border-collapse:collapse;">')
    parts.append(f'<tr>{th(tr("Zeit"))}{th(tr("Ereignis"))}</tr>')
    for offset, text, color in _timeline_rows(record):
        style = f'padding:2px 8px; border-bottom:1px solid {_BORDER};'
        text_style = style + (f'color:{color}; font-weight:bold;' if color else '')
        parts.append(f'<tr><td style="{style}">{html.escape(offset)}</td><td style="{text_style}">{html.escape(text)}</td></tr>')
    parts.append('</table>')

    if image_srcs:
        parts.append('<h2>' + html.escape(tr("Messverlauf")) + '</h2>')
        for title, src in image_srcs:
            parts.append(f'<h3>{html.escape(title)}</h3>')
            parts.append(f'<img src="{src}" width="{_IMG_WIDTH}">')

    parts.append('</div>')
    return "\n".join(parts)


def write_html_report(record: RunRecord) -> Path:
    REPORTS_DIR.mkdir(exist_ok=True)
    charts = _render_charts(record)
    image_srcs = [(title, _png_data_uri(image)) for title, image in charts]
    content = build_html(record, image_srcs)
    slug = _slugify(record.testcase_name)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(record.started_at or time.time()))
    path = REPORTS_DIR / f"report_{slug}_{stamp}.html"
    full_html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{html.escape(tr('Testablauf-Report'))}</title></head><body>{content}</body></html>"
    path.write_text(full_html, encoding="utf-8")
    return path


def export_pdf(record: RunRecord, path: Path) -> None:
    charts = _render_charts(record)
    doc = QTextDocument()
    for i, (_title, image) in enumerate(charts):
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(f"chart{i}.png"), image)
    image_srcs = [(title, f"chart{i}.png") for i, (title, _image) in enumerate(charts)]
    doc.setHtml(build_html(record, image_srcs))

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(96)
    doc.setPageSize(QSizeF(writer.width(), writer.height()))
    doc.print_(writer)
