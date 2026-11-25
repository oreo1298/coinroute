"""coinroute command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .db import NotARouteDatabase, export_csv
from .importer import find_file, find_xnet_root, import_xnet
from .report import format_summary, route_summary


def cmd_import(a):
    def progress(rel, n):
        if n:
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
    print(format_summary(route_summary(a.db, a.days, include_removed=a.removed)))


def cmd_export_csv(a):
    for path in export_csv(a.db, a.outdir, include_calls=a.calls):
        print(f"  {path.name}")


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


def cmd_report(a):
    """Write a Coin or Trouble report as HTML (print it from a browser, or use the GUI)."""
    from .db import RouteDB
    from .reports import coin_report_html, default_period, trouble_report_html

    with RouteDB(a.db) as db:
        start, end = default_period(db.meta().get("data_end"), a.days or (7 if a.kind == "coin" else 1))
        start, end = a.start or start, a.end or end
        company = (db.system() or {}).get("company")
        if a.kind == "coin":
            html = coin_report_html(db.coin_report(start, end), company)
        else:
            codes = [c.strip().upper() for c in a.flags.split(",")] if a.flags else None
            html = trouble_report_html(db.trouble_report(start, end, codes), company)
    if a.out:
        Path(a.out).write_text(html, encoding="utf-8")
        print(f"Wrote {a.out}")
    else:
        sys.stdout.write(html)


def cmd_probe(a):
    """Call one phone, send its security code, read some memory, hang up (bench tool)."""
    from .modem import GENERIC, UPMS1200, Modem, ModemError, SerialLine
    from .protocol import ProtocolError
    from .xnet import comm_error_text

    line = SerialLine(a.port, a.baud)
    m = Modem(line, GENERIC if a.generic else UPMS1200)
    try:
        m.reset()
        r = m.dial(a.number, timeout=a.dial_timeout)
        if not r.connected:
            sys.exit(f"coinroute: {r.result or 'no answer'} ({comm_error_text(r.error_code)})")
        print(f"{r.result}")
        link = m.link(timeout=a.reply_timeout)
        try:
            st = link.receive_status()
            print(f"status: phone {st.phone}, type {st.phone_type}, clock {st.clock or '(none)'}")
            if a.code is not None:
                link.security(a.code)
                print("security code accepted")
            addr, count = int(a.read[0], 16), int(a.read[1])
            data = link.read_memory(addr, count)
            print(f"{addr:04X}: {data.hex(' ')}")
        except ProtocolError as e:
            print(f"coinroute: {e}", file=sys.stderr)
        finally:
            for direction, data in link.log:
                print(f"  {direction} {data.hex(' ')}", file=sys.stderr)
            m.hangup()
    except ModemError as e:
        sys.exit(f"coinroute: modem: {e}")
    finally:
        line.close()


def cmd_tui(a):
    try:
        import curses  # noqa: F401
    except ImportError:
        sys.exit("coinroute: the terminal version needs curses; on Windows: pip install windows-curses")
    from .tui.screens import run

    return run(a.db)


def cmd_gui(a):
    try:
        from .gui.app import run
    except ImportError as e:
        sys.exit(f"coinroute: the desktop app needs PySide6 ({e}).\n"
                 "Install it with:  pip install 'coinroute[gui]'")
    sys.exit(run(a.db))


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
    p.add_argument("--removed", action="store_true",
                   help="also list phones that once answered but whose site record was deleted")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("export-csv", help="write every table to CSV files")
    p.add_argument("db")
    p.add_argument("outdir")
    p.add_argument("--calls", action="store_true", help="include individual call records (large)")
    p.set_defaults(func=cmd_export_csv)

    p = sub.add_parser("report", help="write a Coin or Trouble report (HTML, ready to print)")
    p.add_argument("kind", choices=("coin", "trouble"))
    p.add_argument("db")
    p.add_argument("--days", type=int, help="days back from the end of the data (default 7 coin, 1 trouble)")
    p.add_argument("--from", dest="start", metavar="'YYYY-MM-DD HH:MM'", help="start of the period")
    p.add_argument("--to", dest="end", metavar="'YYYY-MM-DD HH:MM'", help="end of the period")
    p.add_argument("--flags", help="trouble report: only these flag codes, e.g. RJ,HG,LB")
    p.add_argument("--out", help="HTML file to write (default: print to the screen)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("probe", help="(experimental) call one phone through the modem and read its memory")
    p.add_argument("number", help="phone number to dial")
    p.add_argument("--port", required=True, help="serial device of the modem, e.g. /dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=1200)
    p.add_argument("--generic", action="store_true", help="a generic Hayes modem (300 bps), not the UPMS1200")
    p.add_argument("--code", help="the site's 4-digit security code, sent first (see docs/PROTOCOL.md)")
    p.add_argument("--read", nargs=2, metavar=("ADDR", "COUNT"), default=["0FF8", "8"],
                   help="memory to read: hex address and byte count (default 0FF8 8)")
    p.add_argument("--dial-timeout", type=float, default=60.0)
    p.add_argument("--reply-timeout", type=float, default=3.0)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("tui", help="start the terminal version (menus like ExpressNet's)")
    p.add_argument("db", nargs="?", help="route database to open (made by `coinroute import`)")
    p.set_defaults(func=cmd_tui)

    p = sub.add_parser("gui", help="start the desktop app")
    p.add_argument("db", nargs="?", help="route database to open")
    p.set_defaults(func=cmd_gui)

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
    except (FileNotFoundError, FileExistsError, NotARouteDatabase) as e:
        sys.exit(f"coinroute: {e}")


if __name__ == "__main__":
    main()
