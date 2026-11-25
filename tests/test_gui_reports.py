"""Reports: the data behind them, the HTML, and the GUI dialog. Synthetic demo data only."""

import pytest

from coinroute.reports import coin_report_html, default_period, trouble_report_html


def test_coin_report_data_and_html(db):
    start, end = default_period(db.meta()["data_end"], 30)
    data = db.coin_report(start, end)
    rows = data["rows"]
    assert len(rows) == 20 and data["days"] == 30
    active = [r for r in rows if r["active"]]
    dead = [r for r in rows if not r["active"]]
    assert active and dead  # the demo has dead phones
    assert data["totals"]["total_cents"] == sum(r["total_cents"] for r in active)
    assert any(r["collected_cents"] > 0 for r in active)  # boxes emptied in a month
    html = coin_report_html(data, "Demo Payphone Co.")
    assert "Coin Report" in html and "no activity" in html and "20 phones" in html


def test_trouble_report_data_and_html(db):
    start, end = default_period(db.meta()["data_end"], 5)
    data = db.trouble_report(start, end)
    flagged = [r for r in data["rows"] if not r["no_comms"]]
    assert flagged and all(r["flags"] for r in flagged)
    assert any(r["no_comms"] for r in data["rows"])
    only = db.trouble_report(start, end, ["HO"])
    assert all(r["flags"] == ["HO"] for r in only["rows"] if not r["no_comms"])
    html = trouble_report_html(data)
    assert "Trouble Report" in html and "Relay Jam" in html  # legend names the flags used


def test_report_cli(demo_db, tmp_path, capsys):
    from coinroute.cli import main

    main(["report", "coin", str(demo_db), "--days", "14", "--out", str(tmp_path / "c.html")])
    assert "Coin Report" in (tmp_path / "c.html").read_text(encoding="utf-8")
    main(["report", "trouble", str(demo_db), "--flags", "RJ,RC"])
    assert "Trouble Report" in capsys.readouterr().out


def test_reports_dialog(qtbot, db, tmp_path):
    pytest.importorskip("PySide6.QtPrintSupport")
    from coinroute.gui.reportsdlg import ReportsDialog

    d = ReportsDialog(db, kind="coin")
    qtbot.addWidget(d)
    assert "Coin Report" in d.html and not d.flags.isVisible()
    d.kind.setCurrentIndex(1)
    assert "Trouble Report" in d.html
    d.flags.setText("RJ")
    d.refresh()
    assert "flags RJ" in d.html
    html = d.save_html(str(tmp_path / "t.html"))
    assert html and (tmp_path / "t.html").read_text(encoding="utf-8") == d.html
    d.save_pdf(str(tmp_path / "t.pdf"))
    assert (tmp_path / "t.pdf").read_bytes()[:5] == b"%PDF-"
