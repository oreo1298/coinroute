"""Settings imported from ExpressNet: system record, users, ports, modems, poll lists, groups, records."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..db import RouteDB
from ..xnet import SECURITY_CODES, framing_text
from .detail import fit_columns, make_table, note
from .models import RIGHT, Column, RowsModel
from .style import count

MASK = "••••••••"


def _yes(v) -> str:
    return "yes" if v else "no — used by sites only"


class UsersModel(RowsModel):
    def __init__(self):
        super().__init__(
            [
                Column("User", "user"),
                Column("Password", "password", mono=True),
                Column("Description", "description"),
            ]
        )
        self.show_passwords = False

    def set_show(self, on: bool) -> None:
        self.show_passwords = on
        if self.rows:
            col = 1
            self.dataChanged.emit(self.index(0, col), self.index(len(self.rows) - 1, col))

    def cell(self, row, col, role):
        if col.key == "password" and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if role == Qt.ItemDataRole.ToolTipRole:
                return None
            return row["password"] if self.show_passwords else MASK
        return NotImplemented


class OptionsModel(RowsModel):
    """Options records; the security codes are masked until shown."""

    CODES = [f"code_{key}" for key, _, _ in SECURITY_CODES]

    def __init__(self):
        super().__init__(
            [
                Column("Options record", "name"),
                Column("Description", "description"),
                Column("Sites", "sites", count, RIGHT),
            ]
            + [
                Column(label, f"code_{key}", mono=True, tip=f"{label} security code")
                for key, label, _ in SECURITY_CODES
            ]
        )
        self.show_codes = False

    def set_show(self, on: bool) -> None:
        self.show_codes = on
        if self.rows:
            self.dataChanged.emit(self.index(0, 3), self.index(len(self.rows) - 1, self.columnCount() - 1))

    def cell(self, row, col, role):
        if col.key in self.CODES and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if role == Qt.ItemDataRole.ToolTipRole or not row.get(col.key):
                return None
            return row[col.key] if self.show_codes else "••••"
        return NotImplemented


class Page(QWidget):
    def __init__(self, title: str, blurb: str = ""):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 12, 16, 12)
        h = QLabel(f"<span style='font-size: 13pt; font-weight: 600'>{title}</span>")
        self.lay.addWidget(h)
        if blurb:
            self.lay.addWidget(note(blurb))
        self.lay.addSpacing(4)


class SettingsView(QWidget):
    PAGES = ["System", "Users", "Ports", "Modems", "Poll lists", "Groups", "Rate & option records"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db: RouteDB | None = None
        self.nav = QListWidget()
        self.nav.addItems(self.PAGES)
        self.nav.setFixedWidth(max(190, self.nav.sizeHintForColumn(0) + 36))
        self.nav.setStyleSheet("QListWidget::item { padding: 5px 8px; }")
        self.stack = QStackedWidget()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        # System
        p = Page("System", "The ExpressNet system record and where this database came from.")
        self.sys_fields: dict[str, QLabel] = {}
        sections = [
            (
                "ExpressNet",
                [
                    ("Company", "company"),
                    ("Licence number", "license_no"),
                    ("ExpressNet installed", "install_date"),
                ],
            ),
            (
                "This database",
                [
                    ("File", "file"),
                    ("Imported from", "source"),
                    ("Imported on", "imported_at"),
                    ("Data ends", "data_end"),
                    ("coinroute version", "coinroute_version"),
                ],
            ),
        ]
        label_w = max(
            self.fontMetrics().horizontalAdvance(lab + ":") for _, rows in sections for lab, _ in rows
        )
        boxes = []
        for title, rows in sections:
            g = QGroupBox(title)
            form = QFormLayout(g)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for label, key in rows:
                lab = QLabel(label + ":")
                lab.setMinimumWidth(label_w + 4)
                v = QLabel()
                v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                v.setWordWrap(True)
                self.sys_fields[key] = v
                form.addRow(lab, v)
            boxes.append(g)
        box, box2 = boxes
        p.lay.addWidget(box)
        p.lay.addWidget(box2)
        p.lay.addStretch(1)
        self.stack.addWidget(p)

        # Users
        p = Page("Users", "ExpressNet logins. Passwords are decoded from PASSWORD.DAT; keep them private.")
        self.users_model = UsersModel()
        self.users = make_table(self.users_model, stretch=2)
        self.show_pw = QCheckBox("Show passwords")
        self.show_pw.toggled.connect(self.users_model.set_show)
        p.lay.addWidget(self.show_pw)
        p.lay.addWidget(self.users, 1)
        self.stack.addWidget(p)

        # Ports
        p = Page("Ports", "Communication ports and the modem strings ExpressNet sends on each.")
        self.ports_model = RowsModel(
            [
                Column("Port", "port_no", align=RIGHT),
                Column("Modem", "modem_name"),
                Column("Description", "modem_description"),
                Column("Line", "line_number"),
                Column("Polling speed", "polling_speed", align=RIGHT),
                Column("Framing", "framing", tip="Parity, data bits, stop bits", cell_tip=framing_text),
                Column("PBX local", "pbx_local", mono=True),
                Column("PBX long distance", "pbx_long", mono=True),
                Column("Init", "init", mono=True),
                Column("Answer", "answer", mono=True),
                Column("Dial 1", "dial_1", mono=True),
                Column("Dial 2", "dial_2", mono=True),
                Column("Dial 3", "dial_3", mono=True),
                Column("Reset", "reset", mono=True),
            ]
        )
        self.ports = make_table(self.ports_model)
        p.lay.addWidget(self.ports, 1)
        self.stack.addWidget(p)

        # Modems
        p = Page("Modems", "Modem profiles (PARAM/MODEMS.DAT).")
        self.modems_model = RowsModel(
            [
                Column("Name", "name"),
                Column("Description", "description"),
                Column("Init", "init", mono=True),
                Column("Answer", "answer", mono=True),
                Column("Dial 1", "dial_1", mono=True),
                Column("Dial 2", "dial_2", mono=True),
                Column("Dial 3", "dial_3", mono=True),
                Column("Reset", "reset", mono=True),
            ]
        )
        self.modems = make_table(self.modems_model)
        p.lay.addWidget(self.modems, 1)
        self.stack.addWidget(p)

        # Poll lists
        p = Page("Poll lists", "Polling schedules: when each list starts and on which days.")
        self.polls_model = RowsModel(
            [
                Column("Name", "name"),
                Column("Description", "description"),
                Column("Starts", "start_time"),
                Column("Days", "days_text", tip="Weekdays the list runs on (Sunday first, like ExpressNet)"),
            ]
        )
        self.poll_lists = make_table(self.polls_model, stretch=1)
        p.lay.addWidget(self.poll_lists, 1)
        self.stack.addWidget(p)

        # Groups
        p = Page("Groups", "Route groups and how many sites each holds.")
        self.groups_model = RowsModel(
            [
                Column("Group", "group_no"),
                Column("Sites", "sites", count, RIGHT),
                Column("Defined in GROUPS.DAT", "defined", _yes),
            ]
        )
        self.groups = make_table(self.groups_model, stretch=2)
        p.lay.addWidget(self.groups, 1)
        self.stack.addWidget(p)

        # Rate & option records
        p = Page(
            "Rate & option records",
            "Each site points to one rate record (call costs) and one options record. Rate tables are "
            "not decoded yet. The security codes are what the phone asks for before it answers a poll; "
            "keep them private.",
        )
        self.rates_model = RowsModel(
            [Column("Rate record", "name"), Column("Sites using it", "sites", count, RIGHT)]
        )
        self.options_model = OptionsModel()
        self.rates = make_table(self.rates_model, stretch=0)
        self.options = make_table(self.options_model, stretch=1)
        self.show_codes = QCheckBox("Show security codes")
        self.show_codes.toggled.connect(self.options_model.set_show)
        p.lay.addWidget(QLabel("<b>Options records</b>"))
        p.lay.addWidget(self.show_codes)
        p.lay.addWidget(self.options, 2)
        p.lay.addWidget(QLabel("<b>Rate records</b>"))
        p.lay.addWidget(self.rates, 1)
        self.stack.addWidget(p)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack, 1)
        self.nav.setCurrentRow(0)

    def set_db(self, db: RouteDB | None) -> None:
        self.db = db
        self.show_pw.setChecked(False)
        self.show_codes.setChecked(False)
        sysrec = (db.system() if db else None) or {}
        meta = db.meta() if db else {}
        values = {
            "company": sysrec.get("company"),
            "license_no": sysrec.get("license_no"),
            "install_date": sysrec.get("install_date"),
            "file": str(db.path.resolve()) if db else "",
            "source": meta.get("source"),
            "imported_at": (meta.get("imported_at") or "").replace("T", " "),
            "data_end": meta.get("data_end"),
            "coinroute_version": meta.get("coinroute_version"),
        }
        for k, lab in self.sys_fields.items():
            lab.setText(values.get(k) or "—")
        self.users_model.set_rows(db.users() if db else [])
        self.ports_model.set_rows(db.ports() if db else [])
        self.modems_model.set_rows(db.modems() if db else [])
        self.polls_model.set_rows(db.poll_lists() if db else [])
        self.groups_model.set_rows(db.group_table() if db else [])
        self.rates_model.set_rows(db.rate_records() if db else [])
        self.options_model.set_rows(db.options_records() if db else [])
        for t in (
            self.users,
            self.ports,
            self.modems,
            self.poll_lists,
            self.groups,
            self.rates,
            self.options,
        ):
            fit_columns(t)
