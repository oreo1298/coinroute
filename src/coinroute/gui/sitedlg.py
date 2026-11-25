"""Edit a site, or add a new one (the desktop side of coinroute.sites)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ..db import RouteDB
from ..fields import phone_display
from ..sites import FIELDS, SECURITY, SiteEditor, SiteError
from .importdlg import fit_to_contents


class SiteDialog(QDialog):
    """`phone` None: a new site. Saving writes the route database (never the ExpressNet folder)."""

    def __init__(self, db_path: str, phone: str | None = None, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.phone = phone
        self.saved_phone: str | None = None
        self.setWindowTitle(f"Edit site {phone_display(phone)}" if phone else "New site")
        with RouteDB(db_path) as db:
            rates = [r["name"] for r in db.rate_records()]
            options = [r["name"] for r in db.options_records()]
        current = {}
        if phone:
            with SiteEditor(db_path) as ed:
                current = ed.values(phone)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.number = QLineEdit()
        self.number.setPlaceholderText("area code and number, e.g. 512-555-0199")
        if phone:
            form.addRow("Phone number:", QLabel(phone_display(phone)))
        else:
            form.addRow("Phone number:", self.number)
        self.inputs: dict[str, QLineEdit | QComboBox] = {}
        for f in [*FIELDS, SECURITY]:
            if f.key in ("rate_record", "options_record"):
                w = QComboBox()
                w.setEditable(True)
                w.addItems(["", *(rates if f.key == "rate_record" else options)])
                w.setCurrentText(current.get(f.key, ""))
            else:
                w = QLineEdit(current.get(f.key, ""))
                w.setMaxLength(4 if f.key == "security_code" else f.max_len)
            self.inputs[f.key] = w
            form.addRow(f.label + ":", w)
        self.error = QLabel()
        self.error.setStyleSheet("color: #c0392b;")
        self.error.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        note = QLabel("Saved in the route database; the ExpressNet folder is not changed.")
        note.setEnabled(False)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.error)
        lay.addWidget(note)
        lay.addWidget(buttons)
        fit_to_contents(self, 480)

    def values(self) -> dict:
        return {k: (w.currentText() if isinstance(w, QComboBox) else w.text()) for k, w in self.inputs.items()}

    def save(self) -> None:
        try:
            with SiteEditor(self.db_path) as ed:
                if self.phone:
                    ed.update(self.phone, self.values())
                    self.saved_phone = self.phone
                else:
                    self.saved_phone = ed.add(self.number.text(), self.values())
        except SiteError as e:
            self.error.setText(str(e))
            return
        self.accept()
