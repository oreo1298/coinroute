"""The sites view: model, colours, filters, sorting. Headless (pytest-qt); synthetic demo data only."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402

from coinroute.gui.models import RowRole, SortRole  # noqa: E402
from coinroute.gui.sitesview import SitesView, StatusKindRole  # noqa: E402
from coinroute.gui.style import status_colors  # noqa: E402

STATUS_COL = 4


def visible_rows(view: SitesView) -> list[dict]:
    return [view.proxy.index(i, 0).data(RowRole) for i in range(view.proxy.rowCount())]


def test_sites_rows_and_status_colours(qtbot, db):
    v = SitesView()
    qtbot.addWidget(v)
    v.set_db(db)
    assert v.proxy.rowCount() == 20
    for i in range(v.proxy.rowCount()):
        idx = v.proxy.index(i, STATUS_COL)
        kind = idx.data(StatusKindRole)
        bg, fg = status_colors(kind)
        assert idx.data(Qt.ItemDataRole.BackgroundRole).color() == bg
        assert idx.data(Qt.ItemDataRole.ForegroundRole).color() == fg
    texts = {
        v.proxy.index(i, STATUS_COL).data(): v.proxy.index(i, STATUS_COL).data(StatusKindRole)
        for i in range(20)
    }
    assert texts["Answering"] == "ok" and texts["Never polled"] == "never"
    assert any(t.startswith("Silent") and k == "dead" for t, k in texts.items())
    # green / amber / red / grey are actually different colours
    assert len({status_colors(k)[0].name() for k in ("ok", "silent", "dead", "never")}) == 4
    # money formatting and blanks
    row0 = v.proxy.index(0, 0).data(RowRole)
    assert v.proxy.index(0, 6).data().startswith("$") or row0["cash_box_cents"] is None


def test_sites_filtering(qtbot, db):
    v = SitesView()
    qtbot.addWidget(v)
    v.set_db(db)
    v.search.setText("laundromat")
    assert [r["description"] for r in visible_rows(v)] == ["LAUNDROMAT"]
    phone = visible_rows(v)[0]["phone"]
    v.search.setText(phone)  # 714-555-0112 style
    assert len(visible_rows(v)) == 1
    v.search.setText(phone.replace("-", "")[-7:])  # digits only
    assert len(visible_rows(v)) == 1
    v.search.setText("lakeview")  # city
    assert visible_rows(v) and all("LAKEVIEW" in r["place"] for r in visible_rows(v))
    v.search.clear()

    v.group.setCurrentIndex(v.group.findData("000200"))
    assert visible_rows(v) and {r["group_no"] for r in visible_rows(v)} == {"000200"}
    v.group.setCurrentIndex(0)
    assert len(visible_rows(v)) == 20

    v.chips["dead"].click()
    assert visible_rows(v) and {r["status_kind"] for r in visible_rows(v)} == {"dead"}
    assert v.chips["dead"].text().endswith(str(len(visible_rows(v))))
    assert f"{len(visible_rows(v))}</b> sites shown" in v.totals.text()
    v.chips[None].click()
    assert len(visible_rows(v)) == 20
    v.search.setText("no such place")
    assert not visible_rows(v) and v.empty.isVisibleTo(v)


def test_sites_sorting_window_and_removed(qtbot, db):
    v = SitesView()
    qtbot.addWidget(v)
    v.set_db(db)
    v.table.sortByColumn(STATUS_COL, Qt.SortOrder.AscendingOrder)
    kinds = [r["status_kind"] for r in visible_rows(v)]
    assert kinds[0] == "dead" and kinds[-1] == "ok"
    v.table.sortByColumn(6, Qt.SortOrder.DescendingOrder)  # in box, blanks last either way
    boxes = [r["cash_box_cents"] for r in visible_rows(v)]
    assert boxes[-1] is None and boxes[0] == max(b for b in boxes if b is not None)
    v.table.sortByColumn(6, Qt.SortOrder.AscendingOrder)
    assert [r["cash_box_cents"] for r in visible_rows(v)][-1] is None

    before = sum(r["failed_calls_in_window"] for r in v.model.rows)
    v.days.setValue(365)
    qtbot.waitUntil(lambda: v.model.window_days == 365, timeout=3000)
    assert v.model.headerData(7, Qt.Orientation.Horizontal) == "Coins (365 d)"
    assert sum(r["failed_calls_in_window"] for r in v.model.rows) > before

    v.removed.setChecked(True)
    removed = [r for r in v.model.rows if r["removed"]]
    assert removed and len(v.model.rows) == 20 + len(removed)
    i = v.model.rows.index(removed[0])
    assert v.model.index(i, 2).data() == "(site record deleted)"
    assert v.model.index(i, 2).data(Qt.ItemDataRole.FontRole).italic()


def test_sort_role_is_python_value(qtbot, db):
    v = SitesView()
    qtbot.addWidget(v)
    v.set_db(db)
    assert isinstance(v.model.index(0, STATUS_COL).data(SortRole), tuple)


def test_columns_fit_the_width_without_sideways_scrolling(qtbot, qapp, db):
    base = qapp.font()
    big = qapp.font()
    big.setPointSizeF(base.pointSizeF() * 1.15)  # wider cells, as with Breeze's font and padding
    qapp.setFont(big)
    try:
        v = SitesView()
        qtbot.addWidget(v)
        v.resize(1300, 420)  # short, so the vertical scrollbar is needed once the rows are in
        v.show()
        qtbot.waitExposed(v)
        v.set_db(db)
        qtbot.wait(50)  # let the scrollbars settle
        hh = v.table.horizontalHeader()
        assert v.table.verticalScrollBar().isVisible()
        total = sum(hh.sectionSize(c) for c in range(hh.count()))
        squeezed = [hh.sectionSize(c) for c in v.SQUEEZABLE]
        # it fits without a sideways scrollbar, unless every long column is already at its floor
        # (on narrow screens with big fonts there is no room left to give)
        assert total <= v.table.viewport().width() or all(w <= v.FLOOR for w in squeezed), (total, squeezed)
        if total <= v.table.viewport().width():
            assert not v.table.horizontalScrollBar().isVisible()
        assert all(hh.sectionSize(c) >= 56 for c in range(hh.count()))  # never crushed
        idx = v.proxy.index(0, 2)  # the full text of a squeezed cell is in its tooltip
        assert idx.data(Qt.ItemDataRole.ToolTipRole) == idx.data()
        assert v.minimumSizeHint().width() < 1100  # the totals line wraps instead of widening the view
    finally:
        qapp.setFont(base)


def test_trouble_column_shows_codes_with_names(qtbot, db):
    v = SitesView()
    qtbot.addWidget(v)
    v.set_db(db)
    col = next(i for i, c in enumerate(v.model.columns) if c.title == "Trouble")
    rows = [i for i in range(v.proxy.rowCount()) if v.proxy.index(i, col).data()]
    assert rows
    idx = v.proxy.index(rows[0], col)
    codes = idx.data().split()
    assert all(f"{c}  " in idx.data(Qt.ItemDataRole.ToolTipRole) for c in codes)
    v.search.setText("rj")  # search matches trouble codes
    assert visible_rows(v) and all("RJ" in r["flags"] for r in visible_rows(v))
