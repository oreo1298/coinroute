"""Adding, editing and removing sites (coinroute.sites) on a copy of the demo route."""
import shutil

import pytest

from coinroute import xnet
from coinroute.db import RouteDB
from coinroute.sites import SiteEditor, SiteError

PHONE = "2105550108"


@pytest.fixture
def route(demo_db, tmp_path):
    db = tmp_path / "route.db"
    shutil.copy(demo_db, db)
    return db


def test_edits_go_into_the_record_bytes(route):
    with SiteEditor(route, now=lambda: "2026-05-03 10:00") as ed:
        changed = ed.update(PHONE, {"description": "AIRPORT GATE 4", "security_code": "1234",
                                    "city": "Cedar Flats", "state": "tx", "group_no": "000200"})
        assert sorted(changed) == ["city", "description", "group_no", "security_code", "state"]
        assert ed.update(PHONE, {"description": "AIRPORT GATE 4"}) == []  # nothing new
        log = ed.changes(PHONE)
    with RouteDB(route) as db:
        s = db.site(PHONE)
        raw = db.con.execute("SELECT raw FROM sites WHERE phone=?", (PHONE,)).fetchone()[0]
    assert (s["description"], s["security_code"], s["city"], s["state"], s["group_no"]) == (
        "AIRPORT GATE 4", "1234", "Cedar Flats", "TX", "000200")
    d = xnet.site_record(raw)  # the raw bytes say the same as the columns
    assert (d["description"], d["security_code"], d["group_no"]) == ("AIRPORT GATE 4", "1234", "000200")
    assert raw[12:73].startswith(b"AIRPORT GATE 4 ")  # space-padded like ExpressNet's
    code = next(c for c in log if c["field"] == "security_code")
    assert code["old"] == "••••" and code["new"] == "••••"  # codes never in the log


@pytest.mark.parametrize("field,value,msg", [
    ("security_code", "12a4", "4 digits"),
    ("install_date", "2009-04-20", "mm/dd/yy"),
    ("state", "TEX", "at most 2"),
    ("rate_record", "NO-SUCH", "no call costing record"),
    ("speed", "fast", "digits only"),
])
def test_bad_values_are_explained(route, field, value, msg):
    with SiteEditor(route) as ed, pytest.raises(SiteError, match=msg):
        ed.update(PHONE, {field: value})


def test_add_poll_remove_restore(route):
    from coinroute.poller import site_for_poll

    with SiteEditor(route) as ed:
        phone = ed.add("(512) 555-0199", {"description": "NEW BUS SHELTER", "group_no": "000400",
                                          "rate_record": "STANDARD-TX", "options_record": "STANDARD",
                                          "security_code": "4821", "city": "HARLOW", "state": "TX"})
        assert phone == "5125550199"
        with pytest.raises(SiteError, match="already"):
            ed.add(phone, {})
        with pytest.raises(SiteError, match="10 digits"):
            ed.add("555-0199", {})
    with RouteDB(route) as db:
        assert any(r["phone_digits"] == phone for r in db.site_summary(30)["sites"])
        site = site_for_poll(db, phone)
        assert site["security_code"] == "4821" and site["rate_record_raw"]
    with SiteEditor(route) as ed:
        ed.remove(PHONE)
    with RouteDB(route) as db:
        assert db.site(PHONE)["removed"]           # history kept, shown as a removed site
        assert not any(r["phone_digits"] == PHONE for r in db.site_summary(30)["sites"])
    with SiteEditor(route) as ed:
        ed.restore(PHONE)
        assert [c["action"] for c in ed.changes(PHONE)][:2] == ["restore", "remove"]
    with RouteDB(route) as db:
        assert db.site(PHONE)["description"] == "AIRPORT BAGGAGE CLAIM"
