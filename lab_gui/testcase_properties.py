"""Eigenschaften eines Testablaufs (Format v3 "meta", 0.58.0): Beschreibung, Anforderungs-IDs des
ganzen Ablaufs und die Freigabe Quelle/Senke (Netzteil und Last gleichzeitig ein, FP-7 b)."""
from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QPlainTextEdit, QVBoxLayout,
)

from i18n import tr
from testcase_model import clean_meta


def split_requirements(text: str) -> list[str]:
    """"SSR-12, SSR-13; SYS-4" -> ["SSR-12", "SSR-13", "SYS-4"] (Komma, Semikolon, Leerraum), ohne Doppelte."""
    result: list[str] = []
    for part in re.split(r"[,;\s]+", text or ""):
        part = part.strip()
        if part and part not in result:
            result.append(part)
    return result


class TestcasePropertiesDialog(QDialog):
    def __init__(self, meta: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Eigenschaften des Ablaufs"))
        self._meta = dict(meta or {})
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._description = QPlainTextEdit(str(self._meta.get("description", "")))
        self._description.setMaximumHeight(90)
        form.addRow(tr("Beschreibung"), self._description)
        self._requirements = QLineEdit(", ".join(self._meta.get("requirements", [])))
        self._requirements.setPlaceholderText("SSR-12, SSR-13")
        form.addRow(tr("Anforderungen"), self._requirements)
        self._source_sink = QCheckBox(tr("Netzteil und Last gleichzeitig (Quelle/Senke)"))
        self._source_sink.setToolTip(tr("Hebt für diesen Ablauf die Verriegelung Netzteil/Last auf; "
                                        "der Bericht vermerkt das."))
        self._source_sink.setChecked(bool(self._meta.get("source_sink")))
        form.addRow("", self._source_sink)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def meta(self) -> dict:
        meta = dict(self._meta)
        meta["description"] = self._description.toPlainText()
        meta["requirements"] = split_requirements(self._requirements.text())
        meta["source_sink"] = self._source_sink.isChecked()
        return clean_meta(meta)
