"""The main view: every site with its status, cash in box, coins and failures."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QSize, QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QFont, QGuiApplication, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..db import RouteDB
from .models import RIGHT, Column, RowRole, RowsModel, SortRole
from .style import (
    STATUS_LABELS,
    STATUS_ORDER,
    count,
    css,
    flag_codes,
    flag_names,
    money,
    muted,
    status_colors,
    status_text,
)

StatusKindRole = Qt.ItemDataRole.UserRole + 10


def _when(v):
    return "—" if v is None else v


class SitesModel(RowsModel):
    """Rows from RouteDB.site_summary."""

    def __init__(self, parent=None):
        super().__init__(self.make_columns(30), [], parent)
        self.window_days = 30
        self.data_end: str | None = None
        self._italic = QFont()
        self._italic.setItalic(True)

    @staticmethod
    def make_columns(days: int) -> list[Column]:
        return [
            Column("Group", "group_no"),
            Column("Phone", "phone"),
            Column("Description", "description"),
            Column("City/State", "place"),
            Column(
                "Status",
                "status",
                tip="Answering: polled OK in the last 2 days of data.\n"
                "Amber: silent up to 30 days. Red: longer. Grey: never polled OK.",
            ),
            Column("Last good poll", "last_ok", _when),
            Column("In box", "cash_box_cents", money, RIGHT, "Cash in the coin box at the last good poll"),
            Column(
                f"Coins ({days} d)",
                "coins_in_window_cents",
                money,
                RIGHT,
                f"Coins collected by the phone in the last {days} days of data",
            ),
            Column(
                f"Failures ({days} d)",
                "failed_calls_in_window",
                count,
                RIGHT,
                f"Polls that failed in the last {days} days of data",
            ),
            Column("Main failure", "main_failure", tip="The most frequent failure in the window"),
            Column(
                "Trouble",
                "flags",
                flag_codes,
                tip="Trouble flags at the last good poll (hover a cell for their names)",
                cell_tip=flag_names,
            ),
            Column("Firmware", "firmware"),
        ]

    def load(self, summary: dict) -> None:
        self.beginResetModel()
        self.window_days = summary["window_days"]
        self.data_end = summary["data_end"]
        self.columns = self.make_columns(self.window_days)
        self.rows = summary["sites"]
        self.endResetModel()

    def cell(self, row: dict, col: Column, role: int):
        key = col.key
        if key == "description" and row["removed"]:
            if role == Qt.ItemDataRole.DisplayRole:
                return "(site record deleted)"
            if role == Qt.ItemDataRole.FontRole:
                return self._italic
            if role == Qt.ItemDataRole.ForegroundRole:
                return QBrush(muted())
            if role == Qt.ItemDataRole.ToolTipRole:
                return "This phone answered polls once, but its site record is no longer in SITES.DAT."
        if key == "status":
            if role == Qt.ItemDataRole.DisplayRole:
                return status_text(row["status_kind"], row["silent_days"])
            if role == StatusKindRole:
                return row["status_kind"]
            if role in (Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.ForegroundRole):
                bg, fg = status_colors(row["status_kind"])
                return QBrush(bg if role == Qt.ItemDataRole.BackgroundRole else fg)
            if role == SortRole:
                return (STATUS_ORDER[row["status_kind"]], -(row["silent_days"] or 0))
            if role == Qt.ItemDataRole.ToolTipRole:
                end = f" (data ends {self.data_end})" if self.data_end else ""
                return STATUS_LABELS[row["status_kind"]] + end
        if key == "phone" and role == SortRole:
            return row["phone_digits"]
        return NotImplemented


class SitesProxy(QSortFilterProxyModel):
    """Search text, group and status filters; sorting with blanks last."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.group: str | None = None
        self.kind: str | None = None
        self.setSortRole(SortRole)

    def set_filters(self, text: str | None = None, group: str | None = "", kind: str | None = "") -> None:
        new_api = hasattr(self, "beginFilterChange")  # Qt 6.9+; invalidateRowsFilter is deprecated
        if new_api:
            self.beginFilterChange()
        if text is not None:
            self.text = text.strip().lower()
        if group != "":
            self.group = group
        if kind != "":
            self.kind = kind
        if new_api:
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:
            self.invalidateRowsFilter()

    def matches(self, row: dict, use_kind: bool = True) -> bool:
        if self.group and row["group_no"] != self.group:
            return False
        if use_kind and self.kind and row["status_kind"] != self.kind:
            return False
        if self.text:
            t = self.text
            digits = "".join(ch for ch in t if ch.isdigit())
            hay = " ".join(
                [str(row.get(k) or "") for k in ("description", "place", "site_no", "main_failure")]
                + list(row.get("flags") or [])  # trouble codes, e.g. "HG"
            ).lower()
            if t not in hay and not (
                digits
                and len(digits) == len(t.replace("-", "").replace(" ", ""))
                and digits in row["phone_digits"]
            ):
                return False
        return True

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        return self.matches(self.sourceModel().rows[source_row])

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        a, b = left.data(SortRole), right.data(SortRole)
        if a is None or b is None:
            # blanks always at the bottom, whichever way the column is sorted
            asc = self.sortOrder() == Qt.SortOrder.AscendingOrder
            if a is None and b is None:
                return left.row() < right.row()
            return (b is None) if asc else (a is None)
        if isinstance(a, str) and isinstance(b, str):
            a, b = a.lower(), b.lower()
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class StatusDelegate(QStyledItemDelegate):
    """Paints the status as a coloured pill."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.backgroundBrush = QBrush()
        style = opt.widget.style() if opt.widget else QStyle()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        kind = index.data(StatusKindRole)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if not kind:
            return
        bg, fg = status_colors(kind)
        painter.save()
        painter.setFont(opt.font)
        fm = painter.fontMetrics()
        h = fm.height() + 4
        w = min(fm.horizontalAdvance(text) + 18, option.rect.width() - 6)
        r = QRect(option.rect.left() + 4, option.rect.center().y() - h // 2 + 1, w, h)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(r, h / 2, h / 2)
        painter.setPen(fg)
        painter.drawText(
            r.adjusted(9, 0, -9, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width() - 17),
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        s = super().sizeHint(option, index)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        return QSize(
            max(s.width(), option.fontMetrics.horizontalAdvance(text)) + 30,
            max(s.height(), option.fontMetrics.height() + 10),
        )


class SitesView(QWidget):
    """Toolbar (search, group, window, removed), status chips with totals, and the sites table."""

    open_site = Signal(str)  # phone digits

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db: RouteDB | None = None
        self.model = SitesModel(self)
        self.proxy = SitesProxy(self)
        self.proxy.setSourceModel(self.model)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search phone, description, city…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(180)
        self.search.textChanged.connect(lambda t: self._filter(text=t))

        self.group = QComboBox()
        self.group.setMinimumContentsLength(10)
        self.group.currentIndexChanged.connect(lambda _: self._filter(group=self.group.currentData()))

        self.days = QSpinBox()
        self.days.setRange(1, 3650)
        self.days.setValue(30)
        self.days.setSuffix(" days")
        self.days.setToolTip(
            "Window for the Coins and Failures columns, counted back from the end of the data"
        )
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(350)
        self._reload_timer.timeout.connect(self.reload)
        self.days.valueChanged.connect(lambda _: self._reload_timer.start())

        self.removed = QCheckBox("Show removed sites")
        self.removed.setToolTip("Also list phones that once answered but whose site record has been deleted")
        self.removed.toggled.connect(lambda _: self.reload())

        bar = QHBoxLayout()
        bar.addWidget(self.search, 2)
        bar.addSpacing(8)
        bar.addWidget(QLabel("Group:"))
        bar.addWidget(self.group)
        bar.addSpacing(8)
        bar.addWidget(QLabel("Window:"))
        bar.addWidget(self.days)
        bar.addSpacing(8)
        bar.addWidget(self.removed)
        bar.addStretch(1)

        # status chips: click one to show only that status
        self.chips: dict[str | None, QToolButton] = {}
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for kind in (None, "ok", "silent", "dead", "never"):
            b = QToolButton()
            b.setCheckable(True)
            b.setAutoRaise(False)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=kind: self._chip(k))
            self.chip_group.addButton(b)
            self.chips[kind] = b
            chips.addWidget(b)
        self.chips[None].setChecked(True)
        chips.addStretch(1)
        self.totals = QLabel()
        self.totals.setWordWrap(True)  # one long line would set the whole view's minimum width
        self.totals.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # the totals get their own line, so the header never sets a wide minimum for the window

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 12)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setItemDelegateForColumn(4, StatusDelegate(self.table))
        self.table.doubleClicked.connect(self._activated)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        enter = QShortcut(
            QKeySequence(Qt.Key.Key_Return), self.table, context=Qt.ShortcutContext.WidgetShortcut
        )
        enter.activated.connect(lambda: self._activated(self.table.currentIndex()))
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        self.empty = QLabel("No sites match.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 6)
        lay.addLayout(bar)
        lay.addLayout(chips)
        lay.addWidget(self.totals)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.empty)
        self._update_chips()

    # --- data ---

    def set_db(self, db: RouteDB | None) -> None:
        self.db = db
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItem("All groups", None)
        for g in db.groups() if db else []:
            self.group.addItem(g, g)
        self.group.blockSignals(False)
        self.proxy.set_filters(group=None)
        self.reload()

    def reload(self) -> None:
        if self.db is None:
            self.model.load({"window_days": self.days.value(), "data_end": None, "sites": []})
        else:
            QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.model.load(
                    self.db.site_summary(self.days.value(), include_removed=self.removed.isChecked())
                )
            finally:
                QGuiApplication.restoreOverrideCursor()
        self._fit_columns()
        self._update_chips()

    SQUEEZABLE = (2, 9, 3)  # description, main failure, city: give up width first when space is short
    FLOOR = 110  # narrowest a squeezed column gets (its full text is in the tooltip)
    PAD = 12  # breathing room added to each column when the table has space for it

    def _fit_columns(self) -> None:
        """Size columns to their contents, then fit the table's width without a sideways scrollbar.

        Styles pad cells differently (Breeze much more than Fusion), so the padding is only added
        when there is room, and the long text columns give up width when there isn't.
        """
        hh = self.table.horizontalHeader()
        self.table.resizeColumnsToContents()
        for c in range(self.model.columnCount()):
            hh.resizeSection(c, min(max(hh.sectionSize(c), 56), 340))
        avail = self.table.viewport().width() if self.isVisible() else 0
        vbar = self.table.verticalScrollBar()
        if avail > 0 and not vbar.isVisible() and (
            self.table.verticalHeader().length() > self.table.viewport().height()
        ):
            # the rows won't fit, so a vertical scrollbar is about to take some of the width
            avail -= self.table.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent, None, vbar)
        if avail <= 0:  # not laid out yet; fitted again when first shown
            for c in range(hh.count()):
                hh.resizeSection(c, hh.sectionSize(c) + self.PAD)
            return
        self._fitted_visible = True
        spare = avail - sum(hh.sectionSize(c) for c in range(hh.count()))
        if spare >= 0:
            pad = min(self.PAD, spare // max(1, hh.count()))
            for c in range(hh.count()):
                hh.resizeSection(c, hh.sectionSize(c) + pad)
            return
        excess = -spare
        for c in self.SQUEEZABLE:
            if excess <= 0:
                break
            cut = min(excess, max(0, hh.sectionSize(c) - self.FLOOR))
            hh.resizeSection(c, hh.sectionSize(c) - cut)
            excess -= cut

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not getattr(self, "_fitted_visible", False):  # once; later the user's own widths stay
            QTimer.singleShot(0, self._fit_columns)

    # --- filters ---

    def _filter(self, **kw) -> None:
        self.proxy.set_filters(**kw)
        self._update_chips()

    def _chip(self, kind: str | None) -> None:
        self._filter(kind=kind)

    def _update_chips(self) -> None:
        rows = [r for r in self.model.rows if self.proxy.matches(r, use_kind=False)]
        n = {k: sum(1 for r in rows if r["status_kind"] == k) for k in ("ok", "silent", "dead", "never")}
        self.chips[None].setText(f"All  {len(rows)}")
        for k, b in self.chips.items():
            if k is None:
                b.setStyleSheet(self._chip_css(None))
                continue
            b.setText(f"{STATUS_LABELS[k]}  {n[k]}")
            b.setStyleSheet(self._chip_css(k))
        shown = [
            self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()]
            for i in range(self.proxy.rowCount())
        ]
        cash = sum(r["cash_box_cents"] or 0 for r in shown)
        coins = sum(r["coins_in_window_cents"] or 0 for r in shown)
        fails = sum(r["failed_calls_in_window"] or 0 for r in shown)
        what = "site" if len(shown) == 1 else "sites"
        self.totals.setText(
            f"<b>{len(shown)}</b> {what} shown · <b>{money(cash, '$0.00')}</b> in cash boxes · "
            f"<b>{money(coins, '$0.00')}</b> coins and <b>{fails:,}</b> failed calls "
            f"in the last {self.model.window_days} days"
        )
        self.empty.setVisible(bool(self.model.rows) and not shown)

    def _chip_css(self, kind: str | None) -> str:
        if kind is None:
            return (
                "QToolButton { padding: 3px 10px; border-radius: 11px; border: 1px solid palette(mid); }"
                "QToolButton:checked { background: palette(highlight); color: palette(highlighted-text);"
                " border-color: palette(highlight); }"
            )
        bg, fg = status_colors(kind)
        return (
            f"QToolButton {{ padding: 3px 10px; border-radius: 11px; background: {css(bg)}; color: {css(fg)};"
            f" border: 1px solid {css(bg)}; }}"
            f"QToolButton:checked {{ border: 2px solid {css(fg)}; font-weight: bold; }}"
        )

    # --- actions ---

    def selected_phone(self) -> str | None:
        idx = self.table.currentIndex()
        return idx.data(RowRole)["phone_digits"] if idx.isValid() else None

    def _activated(self, index: QModelIndex) -> None:
        if index.isValid():
            self.open_site.emit(index.data(RowRole)["phone_digits"])

    def _menu(self, pos) -> None:
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.data(RowRole)
        m = QMenu(self)
        a = QAction("Open site", m)
        a.triggered.connect(lambda: self.open_site.emit(row["phone_digits"]))
        m.addAction(a)
        c = QAction("Copy phone number", m)
        c.triggered.connect(lambda: QGuiApplication.clipboard().setText(row["phone"]))
        m.addAction(c)
        m.exec(self.table.viewport().mapToGlobal(pos))
