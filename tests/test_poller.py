"""The poller against a simulated modem line and phone (in-process, any platform)."""
import datetime as dt
import shutil

import pytest

from coinroute import protocol as p
from coinroute.db import RouteDB
from coinroute.modem import Modem
from coinroute.poller import Poller, PollStore, simulated_phone, site_for_poll
from coinroute.simphone import SimModemLine, SimPhone

PHONE = "2105550108"  # a demo site (security code 4821)
NOW = dt.datetime(2026, 5, 3, 1, 15)


@pytest.fixture
def route(demo_db, tmp_path):
    """A writable copy of the demo route."""
    db = tmp_path / "route.db"
    shutil.copy(demo_db, db)
    return db


@pytest.fixture
def site(route):
    with RouteDB(route) as db:
        return site_for_poll(db, PHONE)


def poll(phone, site, **kw):
    line = SimModemLine(phone, answer=kw.pop("answer", "connect"))
    return Poller(Modem(line), now=lambda: NOW, **kw).poll(site), line


def test_a_good_poll_runs_expressnets_steps(site):
    ph = SimPhone(security_code="4821", phone=PHONE)
    ph.coin_total, ph.cash_collected, ph.flags[0] = 4321, 1500, 0x01  # E$: the box was emptied
    out, line = poll(ph, site)
    assert out.ok and out.code == 0 and out.message == "Poll complete"
    assert line.dialled == [PHONE]
    assert [s.split(" ")[0] for s in out.steps] == [
        "Resetting", "Dialing", "Receive", "Send", "Clear", "Reset", "End", "Hang"]
    assert out.status.coin_total == 4321 and out.status.cash_collected == 1500
    assert ph.flags[0] == 0                    # E$ cleared after the collection was noted
    assert ph.clock == "2605031011500"         # the phone's clock set (Sunday = 1)
    assert not line.online                     # hung up


@pytest.mark.parametrize("answer,code", [("busy", 5), ("no carrier", 6), ("no answer", 4)])
def test_dial_failures(site, answer, code):
    out, _ = poll(SimPhone(security_code="4821", phone=PHONE), site, answer=answer)
    assert not out.ok and out.code == code


def test_wrong_phone_and_wrong_code(site):
    out, _ = poll(SimPhone(security_code="4821", phone="5550100999"), site)
    assert out.code == 11 and "Phone number mismatch" in out.message
    out, _ = poll(SimPhone(security_code="1111", phone=PHONE), site)
    assert out.code == 14


def test_status_failures(site):
    ph = SimPhone(security_code="4821", phone=PHONE)
    good = ph.greeting
    ph.greeting = lambda: good()[:-1] + bytes([good()[-1] ^ 0xFF])   # spoiled check byte
    assert poll(ph, site)[0].code == 10
    ph.greeting = lambda: b""                                          # says nothing
    assert poll(ph, site, status_timeout=2)[0].code == 8


def test_records_are_read_when_asked(site):
    ph = SimPhone(security_code="4821", phone=PHONE)
    ph.memory[p.CDR_COUNTS:p.CDR_COUNTS + 8] = bytes([0, 2, 0, 2, 0, 3, 0, 3])
    ph.memory[0x3000:0x3000 + 90] = bytes(range(90))
    out, _ = poll(ph, site, collect_records=True)
    assert out.ok and len(out.billing) == 2 and len(out.audit) == 3
    assert out.billing[1] == bytes(range(45, 90)) and all(len(r) == 23 for r in out.audit)


def test_results_are_stored_like_expressnets(route, site):
    with RouteDB(route) as db:
        before = db.coin_polls(PHONE)[-1]
        errors_before = db.comm_error_count(PHONE)
    ph = SimPhone(security_code="4821", phone=PHONE)
    ph.coin_total = 99900
    with PollStore(route) as st:
        st.record(site, poll(ph, site)[0])            # sets the counter's baseline
        ph.coin_total = 150                           # +2.50, wrapping past 999.99
        ph.cash_collected, ph.flags[0] = 1000, 0x01   # and the box was emptied ($10)
        st.record(site, poll(ph, site)[0])
        st.record(site, poll(ph, site, answer="busy")[0])
    with RouteDB(route) as db:
        polls = db.coin_polls(PHONE)
        a, b = polls[-2], polls[-1]
        assert a["coin_total_cents"] == before["coin_total_cents"]   # first poll: baseline only
        assert b["coin_total_cents"] == a["coin_total_cents"] + 250
        assert b["cash_box_cents"] == max(0, a["cash_box_cents"] + 250 - 1000)
        assert b["flags"] == ["E$"] and b["collected_cents"] == 1000
        assert db.comm_error_count(PHONE) == errors_before + 1
        assert db.comm_errors_page(PHONE, limit=1)[0]["message"] == "BUSY detected"
        assert db.meta()["data_end"] == "2026-05-03 01:15"
        s = db.site_summary(30)["sites"]
        assert next(r for r in s if r["phone_digits"] == PHONE)["status_kind"] == "ok"


def test_a_counter_reset_is_not_a_wrap(route, site):
    ph = SimPhone(security_code="4821", phone=PHONE)
    with PollStore(route) as st:
        ph.coin_total = 60000
        st.record(site, poll(ph, site)[0])
        ph.coin_total = 300  # reset (e.g. new firmware): $3.00 since, not $403.00
        st.record(site, poll(ph, site)[0])
    with RouteDB(route) as db:
        a, b = db.coin_polls(PHONE)[-2:]
    assert b["coin_total_cents"] - a["coin_total_cents"] == 300


def test_simulated_phone_carries_on_from_the_last_poll(route, site):
    with PollStore(route) as st:
        ph = simulated_phone(route, site)
        st.record(site, poll(ph, site)[0])
    assert simulated_phone(route, site).coin_total == ph.coin_total + 375


def test_settings_are_kept(route):
    with PollStore(route) as st:
        assert st.setting("port", "SIM") == "SIM"
        st.set_setting("port", "/dev/ttyUSB0")
    with PollStore(route) as st:
        assert st.setting("port") == "/dev/ttyUSB0"


def test_manual_poll_from_the_terminal_version(route):
    from coinroute.tui.screens import PollScreen, make_app

    app = make_app(str(route))
    app.feed(["5", "3", "ENTER", "ENTER", "ENTER"])  # Modems > Manual Polling > first site > SIM
    screen = app.stack[-1]
    assert isinstance(screen, PollScreen)
    screen.thread.join(10)
    t = app.feed([]).text()
    assert "Poll complete" in t and "Send security code" in t
    app.key("ESC")
    assert not isinstance(app.stack[-1], PollScreen)
