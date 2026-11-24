"""The synthetic demo installation (coinroute.demo / `coinroute demo`)."""

import sqlite3

import pytest

from coinroute.cli import main
from coinroute.demo import make_demo
from coinroute.report import route_summary


def test_demo_is_reproducible(tmp_path):
    a, b = make_demo(tmp_path / "a", sites=6, days=60), make_demo(tmp_path / "b", sites=6, days=60)
    files = sorted(p.relative_to(a) for p in a.rglob("*.DAT"))
    assert len(files) == 13
    for f in files:
        assert (a / f).read_bytes() == (b / f).read_bytes(), f
    c = make_demo(tmp_path / "c", sites=6, days=60, seed=2)
    assert (c / "CDR/COIN.DAT").read_bytes() != (a / "CDR/COIN.DAT").read_bytes()


def test_demo_imports_with_the_expected_damage(demo_import):
    _, rep = demo_import
    assert rep.missing == []
    assert rep.counts["SITES/SITES.DAT"] == 20
    assert rep.counts["CDR/COIN.DAT"] > 5000
    assert rep.counts["audit calls"] > 20 * 200  # a few hundred calls per phone
    assert any("skipped 4,096 unreadable bytes" in w for w in rep.warnings)
    assert any("size doesn't match" in w for w in rep.warnings)


def test_demo_route_has_every_kind_of_phone(demo_db):
    db = demo_db
    s = route_summary(db)
    statuses = [r["status"] for r in s["sites"]]
    silent = [int(x.split()[1]) for x in statuses if x.startswith("silent")]
    assert statuses.count("OK") >= 10
    assert any(d <= 30 for d in silent) and any(d > 30 for d in silent)
    assert "never polled OK" in statuses
    assert {r["group_no"] for r in s["sites"]} == {"000100", "000200", "000300", "000400"}
    assert any(r["main_failure"] == "Time-out on carrier detect" for r in s["sites"])

    con = sqlite3.connect(db)
    # deleted site records: their phones still have history
    removed = con.execute(
        "SELECT count(DISTINCT phone) FROM coin_polls WHERE phone NOT IN (SELECT phone FROM sites)"
    ).fetchone()[0]
    assert removed >= 2
    # cash boxes get emptied
    drops = con.execute(
        "SELECT count(*) FROM (SELECT cash_box_cents b, lag(cash_box_cents) OVER "
        "(PARTITION BY phone ORDER BY at) p FROM coin_polls) WHERE b < p"
    ).fetchone()[0]
    assert drops > 20
    # lifetime totals never go down
    assert (
        con.execute(
            "SELECT count(*) FROM (SELECT coin_total_cents t, lag(coin_total_cents) OVER "
            "(PARTITION BY phone ORDER BY at) p FROM coin_polls) WHERE t < p"
        ).fetchone()[0]
        == 0
    )
    busiest = con.execute(
        "SELECT max(n) FROM (SELECT count(*) n FROM audit_calls GROUP BY phone)"
    ).fetchone()[0]
    assert busiest > 1000  # enough for several pages
    con.close()


def test_cli_demo_then_import(tmp_path, capsys):
    main(["demo", str(tmp_path / "d"), "--sites", "5", "--days", "40"])
    assert "coinroute import" in capsys.readouterr().out
    main(["import", str(tmp_path / "d/XNET"), str(tmp_path / "d.db")])
    assert route_summary(tmp_path / "d.db")["sites"]
    capsys.readouterr()
    main(["summary", str(tmp_path / "d.db"), "--removed"])
    assert "(site record deleted)" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="already exists"):
        main(["demo", str(tmp_path / "d")])
