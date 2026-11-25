"""The terminal version, driven with key presses on a text canvas (no terminal needed)."""

import pytest

from coinroute.tui.app import App
from coinroute.tui.canvas import TextCanvas
from coinroute.tui.screens import make_app
from coinroute.tui.widgets import Item, Menu, Message


@pytest.fixture
def app(demo_db):
    a = make_app(str(demo_db))
    a.clock = lambda: "05/02/26  09:00"
    return a


def screen(app, *keys):
    return app.feed(list(keys)).text()


def test_main_menu_is_laid_out_like_expressnet(app):
    t = screen(app)
    for line in ("MAIN MENU", "1.  Edit Site Record", "2.  Call Costing Records Menu",
                 "3.  Edit Options & Registers", "4.  Reports Menu", "5.  Modems Menu",
                 "6.  System Utilities Menu", "7.  File Utilities Menu", "0.  Exit"):
        assert line in t
    lines = t.splitlines()
    assert "Demo Payphone Co." in lines[0] and "05/02/26  09:00" in lines[0]
    assert "Look up a payphone site" in lines[-2]     # the highlighted item's description
    assert lines[-1].startswith(" ESC Back")          # the key bar


def test_arrow_keys_move_the_description(app):
    assert "Coin and trouble reports" in screen(app, "DOWN", "DOWN", "DOWN").splitlines()[-2]


def test_esc_on_the_main_menu_stays_and_exit_quits(app):
    assert "MAIN MENU" in screen(app, "ESC", "ESC")
    app.key("0")
    assert not app.running


def test_site_search_and_record(app):
    t = screen(app, "1")
    assert "SELECT SITE" in t and "AIRPORT BAGGAGE CLAIM" in t and " 20 " in t
    t = screen(app, "C", "E", "D")  # "CEDAR FLATS" is a city in the demo
    assert "Search: CED_" in t and "AIRPORT BAGGAGE CLAIM" in t and "FLEA MARKET" not in t
    t = screen(app, "ENTER")
    assert "SITE RECORD  210-555-0108" in t and "STANDARD-TX" in t
    for _ in range(12):
        app.key("DOWN")
    t = app.feed([]).text()
    assert "Security code:" in t and "••••" in t and "4821" not in t
    assert "4821" in screen(app, "F5")  # show codes
    t = screen(app, "F3")
    assert "COIN POLLS  210-555-0108" in t and "Lifetime" in t


def test_every_menu_item_opens_without_error(app):
    """Walk every item of every menu; nothing may end in an error box."""
    seen = set()

    def walk(path):
        a = make_app(app.route.path)
        for k in path:
            a.key(k)
        top = a.stack[-1]
        if not isinstance(top, Menu) or top.title in seen:
            return
        seen.add(top.title)
        for it in top.items:
            if it.label in ("Exit", "Return"):
                continue
            b = make_app(app.route.path)
            for k in [*path, it.hotkey]:
                b.key(k)
            shown = b.stack[-1]
            c = b.feed([])
            assert "Error" not in getattr(shown, "title", ""), (it.label, c.text())
            walk([*path, it.hotkey])

    walk([])
    assert {"MAIN MENU", "REPORTS MENU", "MODEMS MENU", "AUTO POLLING", "SYSTEM UTILITIES MENU",
            "FILE UTILITIES MENU", "CALL COSTING RECORDS MENU", "ONE-TIME-ONLY REPORTS"} <= seen


def test_reports_run_and_save(app, tmp_path):
    t = screen(app, "4", "1", "1")
    assert "From (YYYY-MM-DD HH:MM):" in t
    t = screen(app, "ENTER", "ENTER")
    assert "COIN REPORT" in t and "Avg/day" in t and "F2 Save to file" in t
    out = tmp_path / "coin.txt"
    assert "Saved to" in screen(app, "F2", *str(out), "ENTER")
    assert "TOTAL" in out.read_text(encoding="utf-8")
    # close the message, leave the report: back on the one-time reports menu
    t = screen(app, "ESC", "ESC", "2", "ENTER", "ENTER", "ENTER")
    assert "TROUBLE REPORT" in t and "no communications with phone" in t


def test_bad_dates_are_explained(app):
    t = screen(app, "4", "1", "1", *["BACKSPACE"] * 16, "x", "ENTER", "ENTER")
    assert "Dates must look like" in t


def test_failures_page_on_demand(app, demo_db):
    t = screen(app, "5", "5")
    assert "COMMUNICATION ERRORS" in t and "200+" in t
    for _ in range(15):
        app.key("PGDN")
    table = app.stack[-1]
    assert len(table.rows) > 200


def test_without_a_database_menus_ask_for_one(tmp_path):
    a = make_app(None)
    t = a.feed(["1"]).text()
    assert "No route open" in t
    t = a.feed(["ESC", "7", "1", *str(tmp_path / "nothing.db"), "ENTER"]).text()
    assert "Can't open" in t


def test_open_route_from_the_file_menu(demo_db):
    a = make_app(None)
    t = a.feed(["7", "1", *str(demo_db), "ENTER"]).text()
    assert "Opened" in t
    assert "Demo Payphone Co." in a.feed(["ESC"]).text().splitlines()[0]


def test_widgets_alone():
    hits = []
    m = Menu("TEST", [Item("First", "one", lambda: hits.append(1) or "done"), Item("Return", "back")])
    a = App(m, clock=lambda: "now")
    c = a.feed(["1"], TextCanvas(24, 80))
    assert hits == [1] and "done" in c.text()
    assert isinstance(a.stack[-1], Message)
    a.key("x")  # any key closes a message
    assert a.stack[-1] is m


def test_the_last_route_is_remembered(demo_db):
    from coinroute.tui.screens import last_route

    make_app(str(demo_db))
    assert last_route() == str(demo_db.resolve())
    a = make_app(None, remember=True)
    assert a.route.path == str(demo_db.resolve())
    assert make_app(None).route.db is None  # tests and scripts start empty unless asked
