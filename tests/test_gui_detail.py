"""Site detail tabs, calls paging and the coin chart. Headless (pytest-qt); synthetic demo data only."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QModelIndex  # noqa: E402

from coinroute.gui.chart import CoinChart  # noqa: E402
from coinroute.gui.detail import SiteDetail  # noqa: E402


def busiest(db):
    return db.con.execute("SELECT phone FROM audit_calls GROUP BY phone ORDER BY count(*) DESC").fetchone()[0]


def test_detail_tabs_populate(qtbot, db):
    phone = busiest(db)
    d = SiteDetail(db, phone)
    qtbot.addWidget(d)
    site = db.site(phone)
    assert d.info.fields["description"].text() == site["description"]
    assert d.info.fields["rate_record"].text() == site["rate_record"]
    assert d.coins.model.rowCount() == site["coin_polls"]
    assert len(d.coins.chart.polls) == site["coin_polls"]
    assert d.coins.model.rows[0]["at"] >= d.coins.model.rows[-1]["at"]  # newest first
    assert sum(r["n"] for r in d.errors.counts_model.rows) == site["comm_errors"]
    assert d.errors.list_model.rowCount() == min(site["comm_errors"], 200)
    assert d.accounting.model.rowCount() == site["call_accounting_periods"]
    assert "46 " in d.accounting.dump.toPlainText()  # first row selected
    assert d.status.text() == "Answering"
    for i in range(d.tabs.count()):
        d.tabs.setCurrentIndex(i)
        assert not d.grab().isNull()


def test_calls_tab_pages_lazily(qtbot, db):
    phone = busiest(db)
    d = SiteDetail(db, phone)
    qtbot.addWidget(d)
    m = d.calls.model
    total = db.calls_count(phone)
    assert total > 1000
    assert m.rowCount() == 200 and m.canFetchMore(QModelIndex())
    assert f"{total:,}" in d.calls.count_label.text()
    m.fetchMore(QModelIndex())
    assert m.rowCount() == 400
    while m.canFetchMore(QModelIndex()):
        m.fetchMore(QModelIndex())
    assert m.rowCount() == total and len({r["id"] for r in m.rows}) == total
    # scrolling to the bottom asks for the next page by itself
    d.calls.only.setChecked(False)
    d.calls.reload()
    d.resize(900, 600)
    d.show()
    d.tabs.setCurrentWidget(d.calls)
    d.calls.table.scrollToBottom()
    qtbot.waitUntil(lambda: m.rowCount() > 200, timeout=3000)
    # date range
    d.calls.only.setChecked(True)
    lo, hi = d.calls.range()
    assert m.rowCount() == min(200, db.calls_count(phone, lo, hi)) > 0
    assert all(lo <= r["call_date"] <= hi for r in m.rows)


def test_detail_of_removed_and_never_polled_sites(qtbot, db):
    gone = db.removed_phones()[0]["phone"]
    d = SiteDetail(db, gone)
    qtbot.addWidget(d)
    assert "deleted" in d.title
    never = next(r for r in db.site_summary()["sites"] if r["status_kind"] == "never")["phone_digits"]
    d2 = SiteDetail(db, never)
    qtbot.addWidget(d2)
    assert d2.status.text() == "Never polled"
    assert d2.coins.model.rowCount() == 0 and not d2.coins.chart.grab().isNull()


def test_chart_hover_snaps_to_nearest_poll(qtbot, db):
    polls = db.coin_polls(busiest(db))
    ch = CoinChart()
    qtbot.addWidget(ch)
    ch.resize(800, 400)
    ch.set_polls(polls)
    ch.grab()  # lays out the plot area
    left, right = ch._plot_x
    assert ch.poll_at(left) == 0 and ch.poll_at(right) == len(polls) - 1
    ch.set_days(90)
    ch.grab()
    i0, i1 = ch.visible()
    assert i1 == len(polls) and 80 <= i1 - i0 <= 92
    assert ch.poll_at(left) == i0


def test_long_history_opens_on_two_years(qtbot):
    from coinroute.gui.detail import CoinPollsTab

    def poll(at, total):
        return {
            "at": at,
            "coin_total_cents": total,
            "cash_box_cents": 0,
            "coins_cents": None,
            "collected_cents": None,
            "status_bytes": "",
        }

    t = CoinPollsTab([poll("2015-01-01 01:00", 100), poll("2026-01-01 01:00", 900)])
    qtbot.addWidget(t)
    assert t.chart.days == 730 and t.range_buttons.checkedId() == 1
    t2 = CoinPollsTab([poll("2025-06-01 01:00", 100), poll("2026-01-01 01:00", 900)])
    qtbot.addWidget(t2)
    assert t2.chart.days is None and t2.range_buttons.checkedId() == 0


def test_calls_tab_survives_unparseable_dates(qtbot, db, monkeypatch):
    from coinroute.gui.detail import CallsTab

    phone = busiest(db)
    monkeypatch.setattr(db, "call_date_range", lambda p: ("0000-00-00", "2026-13-45"))
    t = CallsTab(db, phone)
    qtbot.addWidget(t)
    assert t.date_from.date().isValid() and t.date_to.date().isValid()
    assert t.model.rowCount() == 200
