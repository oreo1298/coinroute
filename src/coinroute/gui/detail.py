"""One site: header with key figures, then Info, Coin polls, Comm errors, Calls and Call accounting tabs."""

from __future__ import annotations

import datetime as _dt

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDateEdit,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QTableView,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..db import RouteDB, status_of
from ..fields import phone_display
from .chart import CoinChart
from .models import RIGHT, Column, PagedModel, RowsModel
from .style import count, css, money, mono_font, muted_css, status_colors, status_text

CALL_PAGE = 200


def _dash(v):
    return "—" if v in (None, "") else str(v)


def make_table(model, *, sortable: bool = False, stretch: int | None = None) -> QTableView:
    t = QTableView()
    t.setModel(model)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setWordWrap(False)
    t.setSortingEnabled(sortable)
    t.verticalHeader().hide()
    t.verticalHeader().setDefaultSectionSize(t.fontMetrics().height() + 10)
    t.horizontalHeader().setHighlightSections(False)
    if stretch is not None:
        t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    else:
        t.horizontalHeader().setStretchLastSection(True)
    return t


def fit_columns(t: QTableView, max_width: int = 360) -> None:
    hh = t.horizontalHeader()
    for c in range(t.model().columnCount()):
        if hh.sectionResizeMode(c) == QHeaderView.ResizeMode.Stretch:
            continue
        t.resizeColumnToContents(c)
        hh.resizeSection(c, min(hh.sectionSize(c) + 14, max_width))


def note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(muted_css())
    return lab


class Tile(QFrame):
    """A small labelled figure in the header."""

    def __init__(self, label: str, value: str, sub: str = ""):
        super().__init__()
        self.setObjectName("tile")
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        lab = QLabel(label)
        lab.setStyleSheet(muted_css())
        self.value = QLabel(value)
        f = self.value.font()
        f.setPointSizeF(f.pointSizeF() * 1.35)
        f.setWeight(f.Weight.DemiBold)
        self.value.setFont(f)
        v.addWidget(lab)
        v.addWidget(self.value)
        if sub:
            s = QLabel(sub)
            s.setStyleSheet(muted_css())
            v.addWidget(s)
        self.setStyleSheet("#tile { border: 1px solid palette(midlight); border-radius: 8px; }")


class InfoTab(QScrollArea):
    def __init__(self, site: dict):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        self.fields: dict[str, QLabel] = {}

        def box(title: str, rows: list[tuple[str, str, str]]) -> QGroupBox:
            g = QGroupBox(title)
            f = QFormLayout(g)
            f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            f.setHorizontalSpacing(14)
            for label, key, value in rows:
                v = QLabel(value)
                v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                v.setWordWrap(True)
                self.fields[key] = v
                f.addRow(label + ":", v)
            return g

        s = site
        if s.get("removed"):
            grid.addWidget(
                note(
                    "This phone's site record is no longer in SITES.DAT. Only what its history "
                    "shows is known."
                ),
                0,
                0,
                1,
                2,
            )
            grid.addWidget(
                box(
                    "Site",
                    [
                        ("Phone", "phone", phone_display(s["phone"])),
                        ("Group", "group_no", _dash(s.get("group_no"))),
                        ("Site number", "site_no", _dash(s.get("site_no"))),
                    ],
                ),
                1,
                0,
            )
        else:

            def found(name, ok):
                if not name:
                    return "—"
                return name if ok else f"{name}  (not found in the imported records)"

            fw_cur, fw_tgt = s.get("firmware_current") or "", s.get("firmware_target") or ""
            pending = fw_tgt and fw_tgt != fw_cur
            grid.addWidget(
                box(
                    "Site",
                    [
                        ("Phone", "phone", phone_display(s["phone"])),
                        ("Description", "description", _dash(s.get("description"))),
                        ("Group", "group_no", _dash(s.get("group_no"))),
                        ("Site number", "site_no", _dash(s.get("site_no"))),
                        ("Installed", "install_date", _dash(s.get("install_date"))),
                        ("Modem speed", "speed", f"{s['speed']} bps" if s.get("speed") else "—"),
                    ],
                ),
                0,
                0,
            )
            grid.addWidget(
                box(
                    "Location",
                    [
                        ("Name", "location_name", _dash(s.get("location_name"))),
                        ("Address", "address", _dash(s.get("address"))),
                        ("City", "city", _dash(s.get("city"))),
                        ("State", "state", _dash(s.get("state"))),
                        ("ZIP", "zip", _dash(s.get("zip"))),
                    ],
                ),
                0,
                1,
            )
            grid.addWidget(
                box(
                    "Programming",
                    [
                        (
                            "Rate record",
                            "rate_record",
                            found(s.get("rate_record"), s.get("rate_record_found")),
                        ),
                        (
                            "Options record",
                            "options_record",
                            found(s.get("options_record"), s.get("options_record_found")),
                        ),
                        ("Firmware in phone", "firmware_current", _dash(fw_cur)),
                        (
                            "Firmware to download",
                            "firmware_target",
                            (fw_tgt + "  (download pending)") if pending else _dash(fw_tgt),
                        ),
                    ],
                ),
                1,
                0,
            )
        grid.addWidget(
            box(
                "History in this database",
                [
                    ("Successful polls", "coin_polls", count(s["coin_polls"])),
                    ("First / last", "poll_span", f"{_dash(s['first_poll'])}  →  {_dash(s['last_poll'])}"),
                    ("Communication errors", "comm_errors", count(s["comm_errors"])),
                    ("Calls on record", "calls", count(s["calls"])),
                    (
                        "Call-accounting periods",
                        "call_accounting_periods",
                        count(s["call_accounting_periods"]),
                    ),
                ],
            ),
            1,
            1,
        )
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(3, 1)
        self.setWidget(inner)


class CoinPollsTab(QWidget):
    RANGES = [("All", None), ("2 years", 730), ("1 year", 365), ("90 days", 90)]

    def __init__(self, polls: list[dict]):
        super().__init__()
        self.polls = polls
        self.chart = CoinChart()
        self.chart.set_polls(polls)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Show:"))
        self.range_buttons = QButtonGroup(self)
        for i, (label, days) in enumerate(self.RANGES):
            b = QToolButton()
            b.setText(label)
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.clicked.connect(lambda _=False, d=days: self.chart.set_days(d))
            self.range_buttons.addButton(b, i)
            bar.addWidget(b)
        # long histories (real routes go back decades) open on the last two years
        span_days = 0
        if len(polls) > 1:
            first = _dt.datetime.strptime(polls[0]["at"], "%Y-%m-%d %H:%M")
            last = _dt.datetime.strptime(polls[-1]["at"], "%Y-%m-%d %H:%M")
            span_days = (last - first).days
        start = 1 if span_days > 730 else 0
        self.range_buttons.button(start).setChecked(True)
        self.chart.set_days(self.RANGES[start][1])
        bar.addStretch(1)
        drops = [p for p in polls if p["collected_cents"] is not None]
        self.summary = note(
            f"{len(polls):,} successful polls · box emptied {len(drops):,} times · "
            f"{money(sum(p['collected_cents'] for p in drops), '$0.00')} taken out in all"
        )
        self.summary.setWordWrap(False)
        bar.addWidget(self.summary)

        rows = list(reversed(polls))
        self.model = RowsModel(
            [
                Column("Polled", "at"),
                Column("In box", "cash_box_cents", money, RIGHT),
                Column("Lifetime total", "coin_total_cents", money, RIGHT),
                Column("Coins since previous", "coins_cents", money, RIGHT),
                Column(
                    "Box emptied",
                    "collected_cents",
                    lambda v: "" if v is None else f"yes — {money(v)} taken out",
                    tip="The box held less than at the previous poll; estimated amount taken out",
                ),
                Column("Status bytes", "status_bytes", mono=True, tip="Not decoded yet (FORMATS.md ❓)"),
            ],
            rows,
        )
        self.table = make_table(self.model)
        fit_columns(self.table)

        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.addLayout(bar)
        tv.addWidget(self.chart, 1)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(self.table)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(split)


class CommErrorsTab(QWidget):
    def __init__(self, db: RouteDB, phone: str):
        super().__init__()
        counts = db.comm_error_counts(phone)
        self.counts_model = RowsModel(
            [
                Column("Failure", "message"),
                Column("Times", "n", count, RIGHT),
                Column("First", "first"),
                Column("Last", "last"),
            ],
            counts,
        )
        self.counts = make_table(self.counts_model, stretch=0)
        fit_columns(self.counts)
        self.list_model = PagedModel(
            [
                Column("When", "at", _dash),
                Column("Code", "code", align=RIGHT),
                Column("Failure", "message"),
            ],
            page_size=CALL_PAGE,
        )
        self.list = make_table(self.list_model, stretch=2)
        self.list_model.restart(lambda after, limit: db.comm_errors_page(phone, after=after, limit=limit))
        fit_columns(self.list)

        total = sum(c["n"] for c in counts)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel(f"<b>{total:,}</b> failed calls to this phone, by cause"))
        lv.addWidget(self.counts)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("Every failed call, newest first"))
        rv.addWidget(self.list)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        if not total:
            lay.addWidget(note("No communication errors recorded for this phone."))
        lay.addWidget(split)


class CallsTab(QWidget):
    """Audit calls, newest first, loaded a page at a time as you scroll."""

    def __init__(self, db: RouteDB, phone: str):
        super().__init__()
        self.db, self.phone = db, phone
        first, last = db.call_date_range(phone)
        self.only = QCheckBox("Only calls from")
        self.date_from = QDateEdit()
        self.date_to = QDateEdit()
        for d in (self.date_from, self.date_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
            d.setEnabled(False)
        today = QDate.currentDate()
        lo = QDate.fromString(first or "", "yyyy-MM-dd")
        hi = QDate.fromString(last or "", "yyyy-MM-dd")
        lo = lo if lo.isValid() else today           # damaged call dates can be unparseable
        hi = hi if hi.isValid() else today
        self.date_from.setDate(hi.addDays(-30) if hi.addDays(-30) > lo else lo)
        self.date_to.setDate(hi)
        self.count_label = QLabel()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.reload)
        self.only.toggled.connect(self._toggle)
        self.date_from.dateChanged.connect(lambda _: self._timer.start())
        self.date_to.dateChanged.connect(lambda _: self._timer.start())

        bar = QHBoxLayout()
        bar.addWidget(self.only)
        bar.addWidget(self.date_from)
        bar.addWidget(QLabel("to"))
        bar.addWidget(self.date_to)
        bar.addSpacing(16)
        bar.addWidget(self.count_label)
        bar.addStretch(1)

        self.model = PagedModel(
            [
                Column("Date", "call_date", _dash),
                Column("Time", "call_time", _dash),
                Column("Dialed", "dialed", mono=True),
                Column(
                    "Detail (16 bytes, not decoded yet)",
                    "detail_hex",
                    mono=True,
                    tip="The last 16 bytes of each call entry; meaning not mapped yet (FORMATS.md ❓)",
                ),
            ],
            page_size=CALL_PAGE,
        )
        self.table = make_table(self.model, stretch=3)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addLayout(bar)
        lay.addWidget(self.table, 1)
        if first is None:
            lay.addWidget(
                note("No individual calls for this phone (or the database was imported without calls).")
            )
        self.reload()
        fit_columns(self.table)

    def _toggle(self, on: bool) -> None:
        self.date_from.setEnabled(on)
        self.date_to.setEnabled(on)
        self.reload()

    def range(self) -> tuple[str | None, str | None]:
        if not self.only.isChecked():
            return None, None
        a, b = self.date_from.date().toString("yyyy-MM-dd"), self.date_to.date().toString("yyyy-MM-dd")
        return (a, b) if a <= b else (b, a)

    def reload(self) -> None:
        lo, hi = self.range()
        n = self.db.calls_count(self.phone, lo, hi)
        self.count_label.setText(f"<b>{n:,}</b> call{'s' if n != 1 else ''}" + (" in range" if lo else ""))
        self.model.restart(
            lambda after, limit: self.db.calls_page(self.phone, lo, hi, after=after, limit=limit)
        )
        self.table.scrollToTop()


class CallAccountingTab(QWidget):
    def __init__(self, rows: list[dict]):
        super().__init__()
        self.model = RowsModel(
            [
                Column("Polled", "at", _dash),
                Column("Period start", "period_start", _dash),
                Column("Period end", "period_end", _dash),
                Column("Non-zero bytes", "nonzero_bytes", count, RIGHT),
                Column("Counters (raw hex from byte 46)", "counters_hex", mono=True),
            ],
            rows,
        )
        self.table = make_table(self.model, stretch=4)
        fit_columns(self.table)
        self.dump = QPlainTextEdit()
        self.dump.setReadOnly(True)
        self.dump.setFont(mono_font())
        self.dump.setPlaceholderText("Select a period to see its counter bytes.")
        self.table.selectionModel().currentRowChanged.connect(self._show)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.dump)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(
            note(
                "The call counters (local coin, intra/inter LATA, speed dials, card groups …) are not "
                "decoded yet, so they are shown as raw bytes for now."
            )
        )
        lay.addWidget(split, 1)
        if rows:
            self.table.selectRow(0)

    def _show(self, cur, _prev) -> None:
        if not cur.isValid():
            self.dump.clear()
            return
        r = self.model.rows[cur.row()]
        data = bytes.fromhex(r["counters_hex"].replace(" ", "")) if r["counters_hex"] else b""
        lines = [f"Period {r['period_start']} to {r['period_end']} (polled {r['at']})", ""]
        for off in range(0, len(data), 16):
            chunk = data[off : off + 16]
            lines.append(f"{46 + off:4d}  " + " ".join(f"{b:02x}" for b in chunk))
        if not data:
            lines.append("(all zero)")
        self.dump.setPlainText("\n".join(lines))


class SiteDetail(QWidget):
    """Everything about one phone. `phone` is the 10-digit number as stored."""

    TABS = ["Info", "Coin polls", "Comm errors", "Calls", "Call accounting"]

    def __init__(self, db: RouteDB, phone: str, parent=None):
        super().__init__(parent)
        self.db, self.phone = db, phone
        site = db.site(phone) or {
            "phone": phone,
            "removed": True,
            "coin_polls": 0,
            "first_poll": None,
            "last_poll": None,
            "comm_errors": 0,
            "calls": 0,
            "call_accounting_periods": 0,
        }
        self.site = site
        polls = db.coin_polls(phone)
        last = polls[-1] if polls else None
        end = db.data_end()
        kind, _, days = status_of(_dt.datetime.strptime(last["at"], "%Y-%m-%d %H:%M") if last else None, end)
        desc = site.get("description") or ("(site record deleted)" if site.get("removed") else "")
        self.title = f"{phone_display(phone)} · {desc}" if desc else phone_display(phone)

        # header
        title = QLabel(
            f"<span style='font-size: 15pt; font-weight: 600'>{phone_display(phone)}</span>"
            f"&nbsp;&nbsp;<span style='font-size: 13pt'>{desc}</span>"
        )
        title.setTextFormat(Qt.TextFormat.RichText)
        place = ", ".join(x for x in (site.get("city"), site.get("state")) if x)
        sub = QLabel(
            "  ·  ".join(
                x
                for x in (
                    f"Group {site.get('group_no')}" if site.get("group_no") else "",
                    f"Site {site.get('site_no')}" if site.get("site_no") else "",
                    place,
                )
                if x
            )
        )
        sub.setStyleSheet(muted_css())
        bg, fg = status_colors(kind)
        self.status = QLabel(status_text(kind, days))
        self.status.setToolTip(
            "Answering: polled OK in the last 2 days of data. Amber: silent up to 30 days. "
            "Red: longer. Grey: never polled OK."
        )
        self.status.setStyleSheet(
            f"background: {css(bg)}; color: {css(fg)}; border-radius: 10px; "
            "padding: 3px 10px; font-weight: 600;"
        )
        head = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(title)
        row.addSpacing(10)
        row.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        head.addLayout(row)
        head.addWidget(sub)
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        tiles.addWidget(
            Tile("Cash in box", money(last["cash_box_cents"], "—") if last else "—", "at the last good poll")
        )
        tiles.addWidget(
            Tile(
                "Last good poll",
                last["at"] if last else "never",
                f"data ends {end:%Y-%m-%d %H:%M}" if end else "",
            )
        )
        tiles.addWidget(
            Tile(
                "Lifetime coins",
                money(last["coin_total_cents"], "—") if last else "—",
                "as reported by the phone",
            )
        )
        tiles.addWidget(Tile("Failed calls", count(site["comm_errors"]), "on record"))
        tiles.addWidget(Tile("Calls", count(site["calls"]), "on record"))
        tiles.addStretch(1)
        head.addSpacing(6)
        head.addLayout(tiles)

        self.tabs = QTabWidget()
        self.info = InfoTab(site)
        self.coins = CoinPollsTab(polls)
        self.errors = CommErrorsTab(db, phone)
        self.calls = CallsTab(db, phone)
        self.accounting = CallAccountingTab(db.call_accounting(phone))
        for w, name in zip((self.info, self.coins, self.errors, self.calls, self.accounting), self.TABS):
            self.tabs.addTab(w, name)
        self.tabs.setCurrentIndex(1 if polls else 0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 8)
        lay.addLayout(head)
        lay.addSpacing(8)
        lay.addWidget(self.tabs, 1)
