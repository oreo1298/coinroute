"""Import an ExpressNet 3 folder (C:\\XNET) into a SQLite database.

The source folder is only read. Every table keeps the original record bytes
in a `raw` column, so later versions can decode more fields without needing
the old files again.
"""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, ctree, xnet

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE system (install_date TEXT, company TEXT, license_no TEXT, raw BLOB);
CREATE TABLE users (user TEXT, description TEXT, password TEXT, raw BLOB);
CREATE TABLE sites (
    phone TEXT PRIMARY KEY, description TEXT, group_no TEXT, site_no TEXT,
    rate_record TEXT, options_record TEXT, speed TEXT, install_date TEXT,
    location_name TEXT, address TEXT, city TEXT, state TEXT, zip TEXT,
    firmware_current TEXT, firmware_target TEXT, raw BLOB);
CREATE TABLE groups (group_no TEXT, raw BLOB);
CREATE TABLE poll_lists (name TEXT, description TEXT, start_time TEXT, raw BLOB);
CREATE TABLE modems (name TEXT, description TEXT, init TEXT, answer TEXT,
    dial_1 TEXT, dial_2 TEXT, dial_3 TEXT, reset TEXT, raw BLOB);
CREATE TABLE ports (port_no INTEGER, modem_name TEXT, modem_description TEXT, init TEXT,
    answer TEXT, dial_1 TEXT, dial_2 TEXT, dial_3 TEXT, reset TEXT, line_number TEXT, raw BLOB);
CREATE TABLE rate_records (name TEXT, raw BLOB);
CREATE TABLE options_records (name TEXT, raw BLOB);
CREATE TABLE coin_polls (id INTEGER PRIMARY KEY, phone TEXT, group_no TEXT, site_no TEXT,
    at TEXT, coin_total_cents INTEGER, cash_box_cents INTEGER, status_bytes TEXT, raw BLOB);
CREATE TABLE comm_errors (id INTEGER PRIMARY KEY, phone TEXT, group_no TEXT, site_no TEXT,
    at TEXT, code INTEGER, message TEXT, raw BLOB);
CREATE TABLE call_accounting (id INTEGER PRIMARY KEY, phone TEXT, group_no TEXT, site_no TEXT,
    at TEXT, period_start TEXT, period_end TEXT, raw BLOB);
CREATE TABLE audit_polls (id INTEGER PRIMARY KEY, phone TEXT, group_no TEXT, site_no TEXT,
    at TEXT, declared_calls INTEGER, size_ok INTEGER, raw_header BLOB);
CREATE TABLE audit_calls (id INTEGER PRIMARY KEY, poll_id INTEGER, phone TEXT,
    dialed TEXT, call_date TEXT, call_time TEXT, detail BLOB);
"""

INDEXES = """
CREATE INDEX coin_polls_phone_at ON coin_polls (phone, at);
CREATE INDEX comm_errors_phone_at ON comm_errors (phone, at);
CREATE INDEX call_accounting_phone_at ON call_accounting (phone, at);
CREATE INDEX audit_polls_phone_at ON audit_polls (phone, at);
CREATE INDEX audit_calls_poll ON audit_calls (poll_id);
CREATE INDEX audit_calls_phone_date ON audit_calls (phone, call_date);
"""


@dataclass
class ImportReport:
    counts: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    missing: list = field(default_factory=list)


def find_file(root: Path, rel: str) -> Path | None:
    """Case-insensitive lookup of 'SITES/SITES.DAT' under root (DOS names on Linux)."""
    cur = root
    for part in rel.split("/"):
        if not cur.is_dir():
            return None
        match = next((p for p in cur.iterdir() if p.name.upper() == part.upper()), None)
        if match is None:
            return None
        cur = match
    return cur if cur.is_file() else None


def find_xnet_root(path: Path) -> Path:
    """Accept the XNET folder itself or a folder that contains it."""
    if find_file(path, "SITES/SITES.DAT"):
        return path
    for child in sorted(path.iterdir()) if path.is_dir() else []:
        if child.is_dir() and find_file(child, "SITES/SITES.DAT"):
            return child
    raise FileNotFoundError(f"{path} does not look like an ExpressNet folder (no SITES/SITES.DAT)")


# The files read, in the order import_xnet reads them.
SOURCE_FILES = [
    "SYSTEM/X3SYSPAR.DAT", "SYSTEM/PASSWORD.DAT", "SITES/SITES.DAT", "LOCAL/GROUPS.DAT", "LOCAL/POLLIST.DAT",
    "PARAM/MODEMS.DAT", "LOCAL/PORTS.DAT", "PARAM/CALLCOST.DAT", "PARAM/MISCOPT.DAT", "CDR/COIN.DAT",
    "CDR/COMMERR.DAT", "CDR/CALLACCT.DAT", "CDR/AUDITCDR.DAT",
]
INDEX_STEP = "building indexes"


def _rows(rep, root, rel, decode, *, progress=None, every=50000):
    if progress:
        progress(rel, 0)
    path = find_file(root, rel)
    if path is None:
        rep.missing.append(rel)
        return
    data = path.read_bytes()
    stats = ctree.ReadStats()
    n = 0
    for rec in ctree.records(data, stats):
        try:
            yield decode(rec)
            n += 1
        except Exception as e:                       # keep going; note the bad record
            rep.warnings.append(f"{rel}: record skipped ({e})")
            continue
        if progress and n % every == 0:
            progress(rel, n)
    rep.counts[rel] = n
    if stats.skipped_bytes:
        spots = ", ".join(f"offset {o:,} ({ln:,} bytes)" for o, ln in stats.damaged_spots[:5])
        rep.warnings.append(f"{rel}: skipped {stats.skipped_bytes:,} unreadable bytes"
                            + (f" at {spots}" if spots else ""))


def import_xnet(src: str | os.PathLike, db_path: str | os.PathLike, *, include_calls: bool = True,
                progress=None, progress_every: int = 50000) -> ImportReport:
    """Read the ExpressNet folder `src` into a new SQLite database `db_path`.

    `progress(step, n)` is called with n=0 when each file in SOURCE_FILES (and
    finally INDEX_STEP) starts, then every `progress_every` records. If it raises,
    the import stops and the partly written database is removed.
    """
    root = find_xnet_root(Path(src))
    db_path = Path(db_path)
    if db_path.exists():
        raise FileExistsError(f"{db_path} already exists; choose a new file")
    rep = ImportReport()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        cur = con.cursor()
        pr = {"progress": progress, "every": max(1, progress_every)}

        def put(table, cols, rows):
            sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
            batch = []
            for row in rows:
                batch.append(tuple(row[c] for c in cols))
                if len(batch) >= 5000:
                    cur.executemany(sql, batch)
                    batch.clear()
            if batch:
                cur.executemany(sql, batch)

        put("system", ["install_date", "company", "license_no", "raw"],
            _rows(rep, root, "SYSTEM/X3SYSPAR.DAT", xnet.system_record, **pr))
        put("users", ["user", "description", "password", "raw"],
            _rows(rep, root, "SYSTEM/PASSWORD.DAT", xnet.user_record, **pr))
        put("sites", ["phone", "description", "group_no", "site_no", "rate_record", "options_record",
                      "speed", "install_date", "location_name", "address", "city", "state", "zip",
                      "firmware_current", "firmware_target", "raw"],
            _rows(rep, root, "SITES/SITES.DAT", xnet.site_record, **pr))
        put("groups", ["group_no", "raw"], _rows(rep, root, "LOCAL/GROUPS.DAT", xnet.group_record, **pr))
        put("poll_lists", ["name", "description", "start_time", "raw"],
            _rows(rep, root, "LOCAL/POLLIST.DAT", xnet.poll_list_record, **pr))
        put("modems", ["name", "description", "init", "answer", "dial_1", "dial_2", "dial_3", "reset", "raw"],
            _rows(rep, root, "PARAM/MODEMS.DAT", xnet.modem_record, **pr))
        put("ports", ["port_no", "modem_name", "modem_description", "init", "answer", "dial_1", "dial_2",
                      "dial_3", "reset", "line_number", "raw"],
            _rows(rep, root, "LOCAL/PORTS.DAT", xnet.port_record, **pr))
        put("rate_records", ["name", "raw"], _rows(rep, root, "PARAM/CALLCOST.DAT", xnet.named_record, **pr))
        put("options_records", ["name", "raw"],
            _rows(rep, root, "PARAM/MISCOPT.DAT", xnet.named_record, **pr))
        put("coin_polls", ["phone", "group_no", "site_no", "at", "coin_total_cents", "cash_box_cents",
                           "status_bytes", "raw"],
            _rows(rep, root, "CDR/COIN.DAT", xnet.coin_record, **pr))
        put("comm_errors", ["phone", "group_no", "site_no", "at", "code", "message", "raw"],
            _rows(rep, root, "CDR/COMMERR.DAT", xnet.comm_error_record, **pr))
        put("call_accounting", ["phone", "group_no", "site_no", "at", "period_start", "period_end", "raw"],
            _rows(rep, root, "CDR/CALLACCT.DAT", xnet.call_accounting_record, **pr))

        poll_sql = ("INSERT INTO audit_polls (phone, group_no, site_no, at, declared_calls, size_ok, raw_header) "
                    "VALUES (?,?,?,?,?,?,?)")
        call_sql = ("INSERT INTO audit_calls (poll_id, phone, dialed, call_date, call_time, detail) "
                    "VALUES (?,?,?,?,?,?)")
        calls, bad_size = [], 0
        ncalls = 0
        for a in _rows(rep, root, "CDR/AUDITCDR.DAT", xnet.audit_record, **pr):
            cur.execute(poll_sql, (a["phone"], a["group_no"], a["site_no"], a["at"], a["declared_calls"],
                                   int(a["size_ok"]), a["raw_header"]))
            bad_size += not a["size_ok"]
            if include_calls:
                pid = cur.lastrowid
                for c in a["calls"]:
                    calls.append((pid, a["phone"], c["dialed"], c["call_date"], c["call_time"], c["detail"]))
                if len(calls) >= 20000:
                    cur.executemany(call_sql, calls)
                    ncalls += len(calls)
                    calls.clear()
        if calls:
            cur.executemany(call_sql, calls)
            ncalls += len(calls)
        rep.counts["audit calls"] = ncalls
        if bad_size:
            rep.warnings.append(f"CDR/AUDITCDR.DAT: {bad_size} poll records whose size doesn't match "
                                "their call count (kept; calls read as far as possible)")

        if progress:
            progress(INDEX_STEP, 0)
        con.executescript(INDEXES)
        meta = {
            "source": str(root.resolve()),
            "imported_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "coinroute_version": __version__,
            "data_end": con.execute("SELECT max(at) FROM (SELECT max(at) at FROM coin_polls "
                                    "UNION ALL SELECT max(at) FROM comm_errors)").fetchone()[0],
        }
        con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
        con.commit()
    except BaseException:
        con.close()
        db_path.unlink(missing_ok=True)
        raise
    con.close()
    return rep
