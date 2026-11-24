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


# --- sample command -------------------------------------------------------------------------

def test_sample_keeps_recent_history_masks_numbers_and_still_imports(tmp_path):
    from coinroute.sample import make_sample
    src = fx.build(tmp_path / "src", damaged_audit=True)
    (src / "SITES/SITES.IDX").write_bytes(b"\0" * 4096)
    (src / "XNET.EXE").write_bytes(b"MZ")
    (src / "CDR/BILLCDR.DAT").write_bytes(fx.var_file([]))
    zpath = tmp_path / "sample.zip"
    rep = make_sample(src, zpath, days=30)
    assert rep.since == "2026-04-02"
    assert rep.kept["CDR/COIN.DAT"] == (42, 42)                 # coin history is kept whole
    assert rep.kept["CDR/COMMERR.DAT"] == (10, 11)
    skipped = " ".join(rep.skipped)
    for name in ("SITES.IDX", "XNET.EXE", "PASSWORD.DAT", "BILLCDR.DAT"):
        assert name in skipped
    import zipfile
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        assert "SAMPLE_README.txt" in names and "XNET/SITES/SITES.DAT" in names
        assert not any(n.upper().endswith((".IDX", ".EXE")) for n in names)
        z.extractall(tmp_path / "out")
    db = tmp_path / "s.db"
    rep2 = import_xnet(tmp_path / "out", db)
    assert rep2.counts["SITES/SITES.DAT"] == 2 and rep2.counts["CDR/COIN.DAT"] == 42
    con = sqlite3.connect(db)
    dialed = sorted(r[0] for r in con.execute("SELECT dialed FROM audit_calls"))
    assert dialed == ["555XXXX", "555XXXX", "911"]               # 7-digit numbers masked, 911 kept
    con.close()


def test_sample_spreads_older_audit_batches(tmp_path):
    from coinroute.sample import _audit_subset
    end = dt.datetime(2026, 5, 2, 1, 0)
    t = dt.datetime(2026, 1, 1, 12, 0)
    recs = [fx.audit("7145550101", end - dt.timedelta(days=d), [("5551212", t)]) for d in range(1000, -1, -1)]
    data = fx.var_file(recs)
    since = fx.datetime_to_minutes(end - dt.timedelta(days=10))
    out, kept, total = _audit_subset(data, since, True, older=99)
    assert total == 1001 and 11 + 90 <= kept <= 11 + 99      # 11 recent + ~99 spread older
    got = [xnet.audit_record(r)["at"] for r in ctree.records(out)]
    first = (end - dt.timedelta(days=1000)).strftime("%Y-%m-%d %H:%M")
    assert got[0] == first and got[-1] == "2026-05-02 01:00"        # spread reaches back to the start


def test_sample_refuses_existing_destination(tmp_path):
    from coinroute.sample import make_sample
    src = fx.build(tmp_path / "src")
    (tmp_path / "out.zip").write_bytes(b"x")
    with pytest.raises(FileExistsError):
        make_sample(src, tmp_path / "out.zip")
