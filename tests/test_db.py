"""coinroute.db: the read-only query layer shared by the CLI and the GUI."""

import datetime as dt
import sqlite3
import struct

import fakexnet as fx
import pytest

from coinroute.db import NotARouteDatabase, RouteDB, export_csv, status_of
from coinroute.importer import import_xnet


def test_status_rules():
    end = dt.datetime(2026, 5, 2, 1, 0)
    assert status_of(None, end) == ("never", "never polled OK", None)
    assert status_of(end - dt.timedelta(days=2), end)[0] == "ok"
    assert status_of(end - dt.timedelta(days=3), end) == ("silent", "silent 3 days", 3)
    assert status_of(end - dt.timedelta(days=30), end)[0] == "silent"
    assert status_of(end - dt.timedelta(days=31), end)[0] == "dead"


def test_opens_read_only_and_rejects_other_files(demo_db, tmp_path):
    with RouteDB(demo_db) as db, pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.con.execute("DELETE FROM sites")
    (tmp_path / "x.db").write_bytes(b"not a database at all" * 100)
    with pytest.raises(NotARouteDatabase):
        RouteDB(tmp_path / "x.db")
    sqlite3.connect(tmp_path / "other.db").execute("CREATE TABLE t (x)")
    with pytest.raises(NotARouteDatabase):
        RouteDB(tmp_path / "other.db")
    with pytest.raises(FileNotFoundError):
        RouteDB(tmp_path / "missing.db")


def test_site_summary_and_removed_sites(demo_db):
    with RouteDB(demo_db) as db:
        s = db.site_summary(30)
        assert len(s["sites"]) == 20 and not any(r["removed"] for r in s["sites"])
        kinds = {r["status_kind"] for r in s["sites"]}
        assert kinds == {"ok", "silent", "dead", "never"}
        for r in s["sites"]:
            assert r["phone"].replace("-", "") == r["phone_digits"]
        both = db.site_summary(30, include_removed=True)["sites"]
        removed = [r for r in both if r["removed"]]
        assert len(removed) == len(db.removed_phones()) >= 2
        assert all(r["last_ok"] for r in removed)  # they answered once
        # a longer window can only see more coins and failures
        wide = {r["phone"]: r for r in db.site_summary(365)["sites"]}
        for r in s["sites"]:
            assert wide[r["phone"]]["failed_calls_in_window"] >= r["failed_calls_in_window"]


def test_site_detail_live_and_removed(demo_db):
    with RouteDB(demo_db) as db:
        live = db.site_summary()["sites"][0]["phone_digits"]
        s = db.site(live)
        assert s["description"] and s["rate_record_found"] and s["options_record_found"]
        assert s["coin_polls"] > 0 and s["calls"] == db.calls_count(live)
        gone = db.removed_phones()[0]["phone"]
        r = db.site(gone)
        assert r["removed"] and r["coin_polls"] > 0 and "description" not in r
        assert db.site("0000000000") is None


def test_coin_polls_mark_cash_drops(demo_db):
    with RouteDB(demo_db) as db:
        phone = db.site_summary()["sites"][0]["phone_digits"]
        polls = db.coin_polls(phone)
        assert [p["at"] for p in polls] == sorted(p["at"] for p in polls)
        drops = [p for p in polls if p["collected_cents"] is not None]
        assert drops
        for p in drops:
            assert p["collected_cents"] >= 0
        assert all(p["coins_cents"] is None or p["coins_cents"] >= 0 for p in polls)


def _all_pages(fetch, limit):
    rows, after = [], None
    while True:
        page = fetch(after=after, limit=limit)
        rows += page
        if len(page) < limit:
            return rows
        after = page[-1]["key"]


def test_calls_paging_covers_every_call_once(demo_db):
    with RouteDB(demo_db) as db:
        phone = db.con.execute(
            "SELECT phone FROM audit_calls GROUP BY phone ORDER BY count(*) DESC"
        ).fetchone()[0]
        total = db.calls_count(phone)
        assert total > 1000
        rows = _all_pages(lambda **k: db.calls_page(phone, **k), 97)
        assert len(rows) == total and len({r["id"] for r in rows}) == total
        dates = [r["call_date"] for r in rows]
        assert dates == sorted(dates, reverse=True)  # newest first
        assert len(rows[0]["detail_hex"].split()) == 16
        first, last = db.call_date_range(phone)
        mid = dt.date.fromisoformat(first) + (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)) / 2
        lo, hi = mid.isoformat(), (mid + dt.timedelta(days=30)).isoformat()
        part = _all_pages(lambda **k: db.calls_page(phone, lo, hi, **k), 50)
        assert len(part) == db.calls_count(phone, lo, hi) > 0
        assert all(lo <= r["call_date"] <= hi for r in part)


def test_paging_puts_undated_rows_last(tmp_path):
    x = fx.build(tmp_path)
    # an extra audit batch whose calls have unreadable dates
    t = dt.datetime(2026, 5, 1, 1, 0)
    rec = bytearray(fx.audit("7145550101", t, [("5550101", t), ("5550102", t), ("5550103", t)]))
    for k in range(3):
        rec[42 + 47 * k + 17 : 42 + 47 * k + 26] = b"????????\0"
    data = (x / "CDR/AUDITCDR.DAT").read_bytes()
    block = b"\xfa\xfa" + struct.pack("<II", len(rec) + 10, len(rec)) + bytes(rec)
    (x / "CDR/AUDITCDR.DAT").write_bytes(data[:-30] + block + data[-30:])  # before the trailing free block
    import_xnet(x, tmp_path / "r.db")
    with RouteDB(tmp_path / "r.db") as db:
        assert db.calls_count("7145550101") == 6
        for limit in (1, 2, 4, 10):
            rows = _all_pages(lambda **k: db.calls_page("7145550101", **k), limit)
            assert [r["dialed"] for r in rows][-3:] == ["5550103", "5550102", "5550101"], limit
            assert len(rows) == 6
        # a date range leaves undated calls out
        assert len(db.calls_page("7145550101", "2000-01-01", "2099-12-31")) == 3


def test_comm_errors(demo_db):
    with RouteDB(demo_db) as db:
        dead = next(
            r
            for r in db.site_summary()["sites"]
            if r["status_kind"] == "dead" and r["failed_calls_in_window"]
        )["phone_digits"]
        n = db.comm_error_count(dead)
        rows = _all_pages(lambda **k: db.comm_errors_page(dead, **k), 25)
        assert len(rows) == n and [r["at"] for r in rows] == sorted((r["at"] for r in rows), reverse=True)
        counts = db.comm_error_counts(dead)
        assert sum(c["n"] for c in counts) == n
        assert counts[0]["message"] == "Time-out on carrier detect"


def test_call_accounting_and_settings(demo_db):
    with RouteDB(demo_db) as db:
        phone = db.site_summary()["sites"][0]["phone_digits"]
        ca = db.call_accounting(phone)
        assert ca and ca[0]["period_start"] <= ca[0]["period_end"]
        busy = [r for r in ca if r["categories"]]
        assert busy and all(r["calls"] == sum(c["calls"] for c in r["categories"]) for r in busy)
        assert {c["category"] for r in busy for c in r["categories"]} >= {"Local Coin"}
        assert db.system() == {
            "install_date": "2000-04-20",
            "company": "Demo Payphone Co.",
            "license_no": "0000012345",
        }
        assert [u["user"] for u in db.users()] == ["PROTEL", "OFFICE", "TECH"]
        assert db.users()[0]["password"] == "PROTELX3"
        assert db.ports()[0]["modem_name"] == "UPMS1200" and db.ports()[0]["line_number"] == "555-0100"
        assert {m["name"] for m in db.modems()} == {"UPMS1200", "GENERIC"}
        assert db.poll_lists()[0] == {
            "name": "NIGHTLY",
            "description": "Every phone, every night",
            "start_time": "01:00",
        }
        groups = db.group_table()
        assert sum(g["sites"] for g in groups) == 20 and all(g["defined"] for g in groups)
        assert sum(r["sites"] for r in db.rate_records()) == 20
        assert db.groups() == ["000100", "000200", "000300", "000400"]
        assert db.meta()["data_end"].startswith("2026-05-02")


def test_export_csv(demo_db, tmp_path):
    seen = []
    paths = export_csv(demo_db, tmp_path / "csv", progress=lambda t, n: seen.append(t))
    names = {p.name for p in paths}
    assert "sites.csv" in names and "audit_calls.csv" not in names
    assert "raw" not in (tmp_path / "csv/sites.csv").read_text().splitlines()[0]
    assert seen[0] == "system"
    export_csv(demo_db, tmp_path / "csv2", include_calls=True)
    assert (tmp_path / "csv2/audit_calls.csv").stat().st_size > 0
