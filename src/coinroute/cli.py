"""coinroute command line."""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .importer import find_file, find_xnet_root, import_xnet
from .report import format_summary, route_summary

CSV_TABLES = ["system", "sites", "groups", "poll_lists", "modems", "ports", "rate_records",
              "options_records", "coin_polls", "comm_errors", "call_accounting", "audit_polls", "audit_calls"]


def cmd_import(a):
    def progress(rel, n):
        print(f"  {rel}: {n:,} records...", file=sys.stderr)
    print(f"Importing {a.xnet} -> {a.db}", file=sys.stderr)
    rep = import_xnet(a.xnet, a.db, include_calls=not a.no_calls, progress=progress)
    for name, n in rep.counts.items():
        print(f"  {name:<24} {n:>10,}")
    for m in rep.missing:
        print(f"  missing (skipped): {m}")
    for w in rep.warnings:
        print(f"  warning: {w}")
    print(f"Done: {a.db}")


def cmd_summary(a):
    print(format_summary(route_summary(a.db, a.days)))


def cmd_export_csv(a):
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(a.db)
    tables = [t for t in CSV_TABLES if a.calls or t != "audit_calls"]
    for t in tables:
        cur = con.execute(f"SELECT * FROM {t}")
        cols = [c[0] for c in cur.description]
        keep = [i for i, c in enumerate(cols) if c not in ("raw", "raw_header", "detail")]
        with open(out / f"{t}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([cols[i] for i in keep])
            for row in cur:
                w.writerow([row[i] for i in keep])
        print(f"  {t}.csv")
    con.close()


def cmd_users(a):
    from . import ctree, xnet
    root = find_xnet_root(Path(a.xnet))
    path = find_file(root, "SYSTEM/PASSWORD.DAT")
    if not path:
        sys.exit("no SYSTEM/PASSWORD.DAT in that folder")
    for rec in ctree.records(path.read_bytes()):
        u = xnet.user_record(rec)
        print(f"{u['user']:<14} {u['password']:<10} {u['description']}")


def cmd_demo(a):
    from .demo import make_demo
    out = Path(a.outdir)
    if (out / "XNET").exists():
        sys.exit(f"coinroute: {out / 'XNET'} already exists; choose an empty folder")
    x = make_demo(out, sites=a.sites, days=a.days, seed=a.seed)
    print(f"Wrote a synthetic ExpressNet folder to {x}")
    print(f"Next: coinroute import {x} demo.db")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="coinroute", description="Payphone route manager (ExpressNet replacement).")
    ap.add_argument("--version", action="version", version=f"coinroute {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="read an ExpressNet folder (C:\\XNET) into a new database")
    p.add_argument("xnet", help="the XNET folder, or a folder containing it")
    p.add_argument("db", help="new database file to create, e.g. route.db")
    p.add_argument("--no-calls", action="store_true", help="skip the individual call records (smaller, faster)")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("summary", help="route health: which phones answer, cash in box, failures")
    p.add_argument("db")
    p.add_argument("--days", type=int, default=30, help="window for coins and failures (default 30)")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("export-csv", help="write every table to CSV files")
    p.add_argument("db")
    p.add_argument("outdir")
    p.add_argument("--calls", action="store_true", help="include individual call records (large)")
    p.set_defaults(func=cmd_export_csv)

    p = sub.add_parser("demo", help="write a synthetic ExpressNet folder (made-up route) to OUTDIR/XNET")
    p.add_argument("outdir")
    p.add_argument("--sites", type=int, default=20, help="number of payphones (default 20)")
    p.add_argument("--days", type=int, default=400, help="days of history (default 400)")
    p.add_argument("--seed", type=int, default=1, help="random seed; the same seed gives the same files")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("users", help="show ExpressNet logins and passwords from an XNET folder")
    p.add_argument("xnet")
    p.set_defaults(func=cmd_users)

    a = ap.parse_args(argv)
    try:
        a.func(a)
    except (FileNotFoundError, FileExistsError) as e:
        sys.exit(f"coinroute: {e}")


if __name__ == "__main__":
    main()
