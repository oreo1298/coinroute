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


def cmd_poll(a):
    from .db import RouteDB
    from .modem import GENERIC, UPMS1200, Modem, ModemError, open_line
    from .poller import Poller, PollStore, simulated_phone, site_for_poll
    from .simphone import SimModemLine

    phone = "".join(ch for ch in a.phone if ch.isdigit())
    with RouteDB(a.db) as db:
        site = site_for_poll(db, phone)
    if site is None:
        sys.exit(f"coinroute: no site {a.phone} in {a.db}")
    if a.simulate:
        line = SimModemLine(simulated_phone(a.db, site))
    elif a.port:
        line = open_line(a.port, a.baud)
    else:
        sys.exit("coinroute: give --port DEVICE (the modem's serial port), or --simulate")
    store = None if a.dry_run else PollStore(a.db)
    try:
        modem = Modem(line, GENERIC if a.generic else UPMS1200)
        out = Poller(modem, progress=lambda s: print(f"  {s}"), collect_records=a.cdrs or a.keep_cdrs,
                     clear_records=a.cdrs and not a.keep_cdrs, store=store,
                     set_clock=not a.no_clock, dial_prefix=a.prefix).poll(site)
    except ModemError as e:
        sys.exit(f"coinroute: modem: {e}")
    finally:
        if hasattr(line, "close"):
            line.close()
    st = out.status
    if out.ok and st:
        print(f"OK: coins in phone {st.coin_total / 100:,.2f} (counter), box {st.percent_full}% full, "
              f"flags {' '.join(_flags(st)) or 'none'}")
    else:
        print(f"Failed: {out.message} (code {out.code})")
    if out.billing or out.audit:
        print(f"Call records: {len(out.billing)} billing, {len(out.audit)} audit"
              + (" (cleared from the phone)" if a.cdrs and not a.keep_cdrs and not a.dry_run else ""))
    if store is None:
        print("(not saved: --dry-run)")
    else:
        with store:
            store.record(site, out)
        print(f"Saved to {a.db}.")
    return 0 if out.ok else 1


def cmd_autopoll(a):
    import threading

    from .autopoll import AutoPoller
    from .db import RouteDB
    from .modem import GENERIC, UPMS1200, open_line
    from .poller import simulated_phone, site_for_poll
    from .simphone import SimModemLine

    if not a.port and not a.simulate:
        sys.exit("coinroute: give --port DEVICE (the modem's serial port), or --simulate")

    def line_for(phone=None):
        if not a.simulate:
            return open_line(a.port, a.baud)
        with RouteDB(a.db) as db:
            site = site_for_poll(db, ap.current)
        return SimModemLine(simulated_phone(a.db, site))

    stamp = lambda m: print(f"{__import__('time').strftime('%H:%M:%S')}  {m}", flush=True)
    ap = AutoPoller(a.db, line_for, profile=GENERIC if a.generic else UPMS1200, progress=stamp,
                    retries=a.retries, poller_options={"dial_prefix": a.prefix, "collect_records": a.cdrs,
                                                       "clear_records": a.cdrs})
    for pl in ap.plan():
        print(f"  list {pl['number']} {pl['name']:<12} {pl['start_time']:>5}  {pl['days_text']:<28} "
              f"{len(pl['phones'])} phones")
    if a.list:
        pl = next((x for x in ap.plan() if str(x["number"]) == a.list or x["name"] == a.list), None)
        if pl is None:
            sys.exit(f"coinroute: no poll list {a.list}")
        ap.run_list(pl)
        return 0
    if a.once:
        ap.run_due()
        return 0
    stop = threading.Event()
    try:
        ap.run_forever(stop)
    except KeyboardInterrupt:
        stop.set()
    return 0


def _flags(st):
    from .xnet import trouble_flags

    return trouble_flags(bytes(48) + st.flags)


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

    p = sub.add_parser("poll", help="call one phone now and save the result in the route database")
    p.add_argument("db", help="route database (made by `coinroute import`)")
    p.add_argument("phone", help="the site's phone number")
    p.add_argument("--port", help="the modem's serial port (e.g. /dev/ttyUSB0 or COM1)")
    p.add_argument("--baud", type=int, default=9600, help="PC-to-modem speed (default 9600)")
    p.add_argument("--generic", action="store_true", help="a generic Hayes modem instead of the UPMS1200")
    p.add_argument("--prefix", default="", help="dial this before the number (e.g. 1, or 9, for a PBX)")
    p.add_argument("--cdrs", action="store_true",
                   help="also collect the phone's call records: saved, then cleared from the phone")
    p.add_argument("--keep-cdrs", action="store_true", help="read the call records but leave them in the phone")
    p.add_argument("--no-clock", action="store_true", help="don't set the phone's clock")
    p.add_argument("--simulate", action="store_true", help="poll a simulated phone instead (no modem needed)")
    p.add_argument("--dry-run", action="store_true", help="don't save the result")
    p.set_defaults(func=cmd_poll)

    p = sub.add_parser("autopoll", help="run the poll lists on their schedule (until Ctrl-C)")
    p.add_argument("db", help="route database (made by `coinroute import`)")
    p.add_argument("--port", help="the modem's serial port (e.g. /dev/ttyUSB0 or COM1)")
    p.add_argument("--baud", type=int, default=9600, help="PC-to-modem speed (default 9600)")
    p.add_argument("--generic", action="store_true", help="a generic Hayes modem instead of the UPMS1200")
    p.add_argument("--prefix", default="", help="dial this before each number")
    p.add_argument("--retries", type=int, default=1, help="passes over failed phones at the end of a run")
    p.add_argument("--cdrs", action="store_true", help="collect call records too (saved, then cleared)")
    p.add_argument("--once", action="store_true", help="run whatever is due now, then stop")
    p.add_argument("--list", help="run this poll list now (number or name), then stop")
    p.add_argument("--simulate", action="store_true", help="poll simulated phones instead (no modem needed)")
    p.set_defaults(func=cmd_autopoll)

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
