import datetime as dt
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import fakexnet as fx  # noqa: E402
from coinroute import ctree, xnet  # noqa: E402
from coinroute.cli import main  # noqa: E402
from coinroute.fields import mmddyy, yymmdd  # noqa: E402
from coinroute.importer import import_xnet  # noqa: E402
from coinroute.report import route_summary  # noqa: E402
from coinroute.scramble import check_password, decode_password, encode_password, password_key  # noqa: E402


# --- c-tree readers -------------------------------------------------------------

def test_fixed_reader_skips_header_slots_deleted_and_empty():
    recs = [b"\0A" + b"x" * 81, b"\0B" + b"y" * 81]
    data = fx.fixed_file(83, recs, deleted=2) + b"\0" * 83          # plus one never-used slot
    stats = ctree.ReadStats()
    got = list(ctree.records(data, stats))
    assert got == recs
    assert stats.deleted == 2 and stats.records == 2


def test_variable_reader_resyncs_after_damage():
    recs = [b"\0rec-one" + b"." * 40, b"\0rec-two" + b"." * 50, b"\0rec-three" + b"." * 60]
    clean = list(ctree.records(fx.var_file(recs)))
    assert clean == recs
    stats = ctree.ReadStats()
    damaged = list(ctree.records(fx.var_file(recs, garbage_after=1), stats))
    assert damaged == recs
    assert stats.skipped_bytes >= 768 and stats.free_blocks == 1


# --- field helpers -----------------------------------------------------------------

def test_dates():
    assert yymmdd("260502") == "2026-05-02"
    assert yymmdd("990211") == "1999-02-11"
    assert mmddyy("08/12/09") == "2009-08-12"
    assert mmddyy("  /  /  ") is None


# --- scrambles ------------------------------------------------------------------------

@pytest.mark.parametrize("pw", ["PROTELX3", "QWERTYUI", "ABC", "PASS1234", "A"])
@pytest.mark.parametrize("key", [0x00, 0x9C, 0xFF, 0x0F, 0xF0, 0x37])
def test_password_roundtrip(pw, key):
    enc = encode_password(pw, key)
    if key >> 4 == 0xF and len(pw) == 8:
        # ExpressNet quirk: for these keys the 8th character is never stored.
        assert decode_password(enc, key) == pw[:7]
    elif 0 not in enc:
        assert decode_password(enc, key) == pw


def test_check_password_matches_original_behaviour():
    for salt in range(256):
        rec = fx.user("PROTEL", "PROTELX3", salt=salt)
        assert check_password("PROTELX3", rec)
        assert not check_password("PROTELX4"[:6] + "ZZ", rec)
        if password_key(rec) >> 4 == 0xF:
            assert check_password("PROTELX?", rec)        # 8th character ignored, as in the original


def test_known_password_vector():
    # The factory default login (PROTEL / PROTELX3), as the original program stores it
    # for the key derived from that user record.
    assert encode_password("PROTELX3", 0x9C).hex(" ") == "77 2c d3 b2 66 56 40 8d"
    assert decode_password(bytes.fromhex("772cd3b26656408d"), 0x9C) == "PROTELX3"


def test_user_and_system_records():
    u = xnet.user_record(fx.user("PROTEL", "SECRET12"))
    assert (u["user"], u["password"]) == ("PROTEL", "SECRET12")
    s = xnet.system_record(fx.syspar())
    assert s == {**s, "install_date": "2000-04-20", "company": "Test Payphone Co.", "license_no": "1234567890"}


# --- record decoders ------------------------------------------------------------------

def test_coin_record():
    when = dt.datetime(2026, 2, 28, 1, 8)
    c = xnet.coin_record(fx.coin("9495550135", when, 167100, 4410))
    assert c["phone"] == "9495550135" and c["group_no"] == "000100" and c["site_no"] == "100"
    assert c["at"] == "2026-02-28 01:08"
    assert c["date_text"] == "2026-02-28" and c["time_text"] == "01:08"
    assert (c["coin_total_cents"], c["cash_box_cents"]) == (167100, 4410)


def test_comm_error_codes():
    assert xnet.comm_error_text(4) == "Time-out on carrier detect"
    assert xnet.comm_error_text(5) == "BUSY detected"
    assert xnet.comm_error_text(100) == "100 Not presently used"
    assert xnet.comm_error_text(114) == "Undefined Error"
    assert xnet.comm_error_text(200).startswith("Unknown")
    e = xnet.comm_error_record(fx.commerr("2105550199", dt.datetime(2026, 5, 1, 1, 0), 5))
    assert (e["code"], e["message"]) == (5, "BUSY detected")


def test_audit_record():
    t = dt.datetime(2026, 4, 30, 14, 5)
    a = xnet.audit_record(fx.audit("7145550101", dt.datetime(2026, 5, 1, 1, 5), [("5551212", t), ("911", t)]))
    assert a["declared_calls"] == 2 and a["size_ok"]
    assert [c["dialed"] for c in a["calls"]] == ["5551212", "911"]
    assert a["calls"][0]["call_date"] == "2026-04-30" and a["calls"][0]["call_time"] == "14:05"


def test_site_record():
    s = xnet.site_record(fx.site("7145550101", "COURT LOBBY"))
    assert s["phone"] == "7145550101" and s["description"] == "COURT LOBBY"
    assert s["rate_record"] == "CALIFORNIA" and s["options_record"] == "CALIFORNIA"
    assert s["group_no"] == "000100" and s["site_no"] == "100"
    assert s["install_date"] == "2009-08-12" and s["zip"] == "99999"
    assert (s["city"], s["state"], s["firmware_current"]) == ("SPRINGFIELD", "CA", "DD7019-0023R")


# --- import + summary --------------------------------------------------------------------

def test_import_and_summary(tmp_path):
    src = fx.build(tmp_path, damaged_audit=True)
    db = tmp_path / "route.db"
    rep = import_xnet(tmp_path, db)                       # parent folder is accepted too
    assert rep.counts["SITES/SITES.DAT"] == 2
    assert rep.counts["CDR/COIN.DAT"] == 42
    assert rep.counts["audit calls"] == 3
    assert any("unreadable bytes" in w for w in rep.warnings)
    assert "LOCAL/PORTS.DAT" in rep.missing
    con = sqlite3.connect(db)
    assert con.execute("SELECT company FROM system").fetchone()[0] == "Test Payphone Co."
    assert con.execute("SELECT password FROM users").fetchone()[0] == "SECRET12"
    assert con.execute("SELECT value FROM meta WHERE key='data_end'").fetchone()[0] == "2026-05-02 01:05"
    raw = con.execute("SELECT raw FROM sites WHERE phone='7145550101'").fetchone()[0]
    assert raw == (src / "SITES/SITES.DAT").read_bytes()[2456:2456 * 2]    # original bytes kept
    con.close()

    s = route_summary(db)
    by = {r["phone"]: r for r in s["sites"]}
    ok, dead = by["714-555-0101"], by["210-555-0199"]
    assert ok["status"] == "OK" and ok["cash_box_cents"] == 6000
    assert ok["coins_in_window_cents"] == 30 * 25
    assert dead["status"] == "silent 100 days"
    assert dead["failed_calls_in_window"] == 10 and dead["main_failure"] == "Time-out on carrier detect"


def test_import_refuses_to_overwrite(tmp_path):
    fx.build(tmp_path)
    db = tmp_path / "route.db"
    db.write_text("keep me")
    with pytest.raises(FileExistsError):
        import_xnet(tmp_path / "XNET", db)
    assert db.read_text() == "keep me"


def test_cli_end_to_end(tmp_path, capsys):
    fx.build(tmp_path)
    db = tmp_path / "r.db"
    main(["import", str(tmp_path / "XNET"), str(db)])
    main(["summary", str(db)])
    out = capsys.readouterr().out
    assert "2 sites: 1 answering, 1 not" in out and "COURT LOBBY" in out
    main(["export-csv", str(db), str(tmp_path / "csv")])
    assert (tmp_path / "csv/sites.csv").read_text().startswith("phone,description")
    assert not (tmp_path / "csv/audit_calls.csv").exists()
    main(["users", str(tmp_path / "XNET")])
    assert "SECRET12" in capsys.readouterr().out


# --- optional: a real ExpressNet folder (never committed) ---------------------------------

REAL = os.environ.get("COINROUTE_REAL_XNET")


@pytest.mark.skipif(not REAL, reason="set COINROUTE_REAL_XNET to a real XNET folder to run")
def test_real_folder_invariants():
    from coinroute.importer import find_file, find_xnet_root
    root = find_xnet_root(Path(REAL))
    data = find_file(root, "CDR/COIN.DAT").read_bytes()
    n = mismatched = 0
    for rec in ctree.records(data):
        c = xnet.coin_record(rec)
        n += 1
        if c["at"] != f"{c['date_text']} {c['time_text']}":
            mismatched += 1
    assert n > 0 and mismatched == 0, f"{mismatched}/{n} coin timestamps disagree with their text dates"


def test_import_progress_reports_each_file_and_can_cancel(tmp_path):
    from coinroute.importer import INDEX_STEP, SOURCE_FILES
    fx.build(tmp_path)
    seen = []
    import_xnet(tmp_path / "XNET", tmp_path / "a.db", progress=lambda s, n: seen.append((s, n)),
                progress_every=10)
    starts = [s for s, n in seen if n == 0]
    assert starts == SOURCE_FILES + [INDEX_STEP]
    assert ("CDR/COIN.DAT", 40) in seen

    class Stop(Exception):
        pass

    def stop_at_coins(step, n):
        if step == "CDR/COIN.DAT":
            raise Stop
    with pytest.raises(Stop):
        import_xnet(tmp_path / "XNET", tmp_path / "b.db", progress=stop_at_coins)
    assert not (tmp_path / "b.db").exists()                 # partial database removed


# --- Milestone 3 decodes (layouts from ExpressNet's own report/export tables) ---------------

def test_trouble_flags_decode():
    r = bytearray(fx.coin("7145550101", dt.datetime(2026, 5, 1, 1, 0), 1000, 0))
    assert xnet.trouble_flags(bytes(r)) == []
    r[48] |= 0x01          # cash emptied
    r[53] |= 0x08 | 0x04   # relay collect, relay refund
    r[48] |= 0x40          # relay jam
    r[56] |= 0x01          # failed comms lives in the 'PFlags' byte
    assert set(xnet.trouble_flags(bytes(r))) == {"E$", "RC", "RR", "RJ", "FC"}
    assert len(xnet.TROUBLE_FLAGS) == 45 and xnet.TROUBLE_NAMES["DI"] == "Dial Inactive"
    built = fx.coin("7145550101", dt.datetime(2026, 5, 1, 1, 0), 1000, 0, flags=["HO", "E$"], audit_records=7,
                    collected=1250)
    extras = xnet.coin_extras(built)
    assert set(extras["flags"]) == {"HO", "E$"}
    assert extras["audit_records"] == extras["audit_collected"] == 7
    assert extras["amount_collected_cents"] == 1250


def test_call_detail_and_accounting_decode():
    d = xnet.call_detail(bytes.fromhex("3c000000 32000000 32000000 01 00 03 00"))
    assert d == {"duration_s": 60, "charged_cents": 50, "collected_cents": 50, "cost_band": 1, "route": 3}
    assert xnet.call_detail(None)["duration_s"] is None
    assert len(xnet.CALL_CATEGORIES) == 83
    assert xnet.CALL_CATEGORIES[17] == "911" and xnet.CALL_CATEGORIES[34] == "(1)800-NXX-XXXX"
    rec = fx.callacct("7145550101", dt.datetime(2026, 5, 1, 1, 0), dt.date(2026, 4, 30), dt.date(2026, 5, 1),
                      {0: (12, 240, 21), 17: (1, 0, 2)})
    cats = xnet.call_accounting_counters(rec)
    assert [(c["category"], c["calls"], c["money_cents"], c["minutes"]) for c in cats] == [
        ("Local Coin", 12, 240, 21), ("911", 1, 0, 2)]


def test_options_record_security_codes():
    from coinroute.demo import options
    r = options("STANDARD", "Standard options", ("4821", "1735", "2468", "9062", "0000"))
    f = xnet.options_fields(r)
    assert f["description"] == "Standard options"
    assert [f[f"code_{k}"] for k, _, _ in xnet.SECURITY_CODES] == ["4821", "1735", "2468", "9062", "0000"]
    assert xnet.named_record(r)["name"] == "STANDARD"


def test_poll_list_days():
    from coinroute.demo import poll_list
    assert xnet.poll_list_days(poll_list("N", "nightly", "01:00")) == xnet.WEEKDAYS
    assert xnet.poll_list_days(poll_list("W", "weekdays", "08:00", days="-MTWTF-")) == ["Mon", "Tue", "Wed", "Thu",
                                                                                           "Fri"]


def test_port_settings():
    from coinroute.demo import modem, port

    r = bytearray(port(1, modem("UPMS1200", "Protel", "ATZ", "ATA", ("ATDT", "", ""), "ATZ"), "555-0100"))
    r[246:258] = b"9,\0".ljust(12, b"\0")
    r[258:270] = b"     \x00600\x00N8"  # blanks, then leftovers from an older layout (seen in real files)
    got = xnet.port_settings(bytes(r))
    assert got == {"pbx_local": "9,", "pbx_long": "", "polling_speed": "1200", "port_speed": "9600",
                   "framing": "N82"}
    assert xnet.framing_text("N82") == "8 bits, no parity, 2 stop bits"
    assert xnet.framing_text("E71") == "7 bits, even parity, 1 stop bit"
    assert xnet.port_settings(b"short") == {}
