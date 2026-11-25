"""Auto polling: poll lists on their schedule, against simulated phones."""
import datetime as dt
import shutil
import threading

import pytest

from coinroute import xnet
from coinroute.autopoll import AutoPoller, is_due
from coinroute.db import RouteDB
from coinroute.demo import group
from coinroute.poller import simulated_phone, site_for_poll
from coinroute.simphone import SimModemLine

MONDAY_2AM = dt.datetime(2026, 5, 4, 2, 0)


@pytest.fixture
def route(demo_db, tmp_path):
    db = tmp_path / "route.db"
    shutil.copy(demo_db, db)
    return db


def test_group_poll_list_decode():
    assert xnet.group_poll_list(group("000100", 2)) == 2
    assert xnet.group_poll_list(group("000100")) is None


def test_plan_follows_the_group_assignments(route):
    with RouteDB(route) as db:
        plan = {pl["name"]: pl for pl in db.poll_plan()}
        groups = db.group_table()
    assert plan["NIGHTLY"]["number"] == 1 and len(plan["NIGHTLY"]["phones"]) == 20
    assert plan["NIGHTLY"]["groups"] == ["000100", "000200", "000300", "000400"]
    assert plan["MORNING"]["phones"] == []
    assert {g["poll_list"] for g in groups} == {"NIGHTLY"}


def test_is_due():
    pl = {"phones": ["5550100000"], "days": ["Mon", "Tue"], "start_time": "01:00"}
    assert is_due(pl, MONDAY_2AM, None)
    assert not is_due(pl, MONDAY_2AM.replace(hour=0, minute=59), None)       # before its start
    assert not is_due(pl, MONDAY_2AM, "2026-05-04")                         # already ran today
    assert is_due(pl, MONDAY_2AM, "2026-05-03")
    assert not is_due(pl, MONDAY_2AM + dt.timedelta(days=2), None)          # Wednesday: not its day
    assert not is_due({**pl, "phones": []}, MONDAY_2AM, None)               # nothing to poll


def make(route, busy=(), **kw):
    """An AutoPoller whose lines reach simulated phones; numbers in `busy` give BUSY."""
    ap = None

    def line():
        if ap.current in busy:
            return SimModemLine(answer="busy")
        with RouteDB(route) as db:
            return SimModemLine(simulated_phone(route, site_for_poll(db, ap.current)))

    ap = AutoPoller(route, line, now=lambda: MONDAY_2AM, **kw)
    return ap


def test_a_run_polls_every_phone_and_retries_failures(route):
    log = []
    busy = {"2105550108", "7025550104"}
    ap = make(route, busy=busy, progress=log.append, retries=1)
    due = ap.due_lists()
    assert [pl["name"] for pl in due] == ["NIGHTLY"]
    rep = ap.run_list(due[0])
    assert len(rep.ok) == 18 and {p for p, _ in rep.failed} == busy
    assert all(m == "BUSY detected" for _, m in rep.failed)
    assert any("trying 2 failed phone(s) again" in m for m in log)
    with RouteDB(route) as db:
        assert db.comm_errors_page("2105550108", limit=5)[0]["message"] == "BUSY detected"
        assert db.meta()["data_end"].startswith("2026-05-04")
    # it ran today: not due again until tomorrow, even after a restart
    assert make(route).due_lists() == []


def test_run_forever_stops_when_asked(route):
    stop = threading.Event()
    ap = make(route)
    calls = []
    ap.run_due = lambda s=None: calls.append(1) or stop.set() or []
    t = threading.Thread(target=ap.run_forever, args=(stop, 0.01))
    t.start()
    t.join(5)
    assert not t.is_alive() and calls == [1]


def test_terminal_auto_polling_screen(route):
    from coinroute.tui.screens import AutoPollScreen, make_app

    app = make_app(str(route))
    t = app.feed(["5", "2", "2"]).text()  # Modems > Auto Polling > Stop/Restart
    assert "Auto polling: STOPPED" in t and "NIGHTLY" in t and "20 phones" in t
    screen = app.stack[-1]
    assert isinstance(screen, AutoPollScreen)
    t = app.feed(["F2"]).text()
    assert "START AUTO POLLING" in t
    app.feed(["ENTER", "ENTER"])
    assert screen.running and "RUNNING on SIM" in app.feed([]).text()
    app.key("F2")
    screen.thread.join(30)
    assert not screen.running
