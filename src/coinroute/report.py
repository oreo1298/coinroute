"""Route health summary from an imported database."""
from __future__ import annotations

import datetime as _dt
import sqlite3

from .fields import phone_display

FMT = "%Y-%m-%d %H:%M"


def _t(s):
    return _dt.datetime.strptime(s, FMT) if s else None


def route_summary(db_path, window_days: int = 30) -> dict:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    end = _t(con.execute("SELECT value FROM meta WHERE key='data_end'").fetchone()[0])
    since = (end - _dt.timedelta(days=window_days)).strftime(FMT) if end else "0000"
    sites = []
    for s in con.execute("SELECT * FROM sites ORDER BY group_no, phone"):
        last = con.execute("SELECT at, cash_box_cents, coin_total_cents FROM coin_polls "
                           "WHERE phone=? ORDER BY at DESC LIMIT 1", (s["phone"],)).fetchone()
        first_in_window = con.execute("SELECT coin_total_cents FROM coin_polls WHERE phone=? AND at>=? "
                                      "ORDER BY at LIMIT 1", (s["phone"], since)).fetchone()
        fails = con.execute("SELECT count(*) n FROM comm_errors WHERE phone=? AND at>=? AND code<>0",
                            (s["phone"], since)).fetchone()["n"]
        why = con.execute("SELECT message, count(*) n FROM comm_errors WHERE phone=? AND at>=? AND code<>0 "
                          "GROUP BY message ORDER BY n DESC LIMIT 1", (s["phone"], since)).fetchone()
        last_at = _t(last["at"]) if last else None
        if last_at is None:
            status = "never polled OK"
        elif end and (end - last_at).days <= 2:
            status = "OK"
        else:
            status = f"silent {(end - last_at).days} days"
        coins = None
        if last and first_in_window:
            delta = last["coin_total_cents"] - first_in_window["coin_total_cents"]
            coins = delta if delta >= 0 else None
        sites.append({
            "group_no": s["group_no"], "phone": phone_display(s["phone"]), "description": s["description"],
            "place": ", ".join(x for x in (s["city"], s["state"]) if x),
            "status": status, "last_ok": last["at"] if last else None,
            "cash_box_cents": last["cash_box_cents"] if last else None,
            "coins_in_window_cents": coins, "failed_calls_in_window": fails,
            "main_failure": why["message"] if why else None,
            "firmware": s["firmware_current"],
        })
    con.close()
    return {"data_end": end.strftime(FMT) if end else None, "window_days": window_days, "sites": sites}


def _money(c):
    return "" if c is None else f"{c / 100:,.2f}"


def format_summary(summary: dict) -> str:
    rows = summary["sites"]
    ok = sum(1 for r in rows if r["status"] == "OK")
    lines = [f"Data ends {summary['data_end']}.  {len(rows)} sites: {ok} answering, {len(rows) - ok} not.",
             f"Coins and failures are for the last {summary['window_days']} days of data.", ""]
    hdr = f"{'Group':<7} {'Phone':<13} {'Description':<32} {'Status':<16} {'In box':>9} {'Coins':>8} {'Fails':>5}  Main failure"
    lines += [hdr, "-" * len(hdr)]
    for r in rows:
        lines.append(f"{r['group_no']:<7} {r['phone']:<13} {r['description'][:32]:<32} {r['status']:<16} "
                     f"{_money(r['cash_box_cents']):>9} {_money(r['coins_in_window_cents']):>8} "
                     f"{r['failed_calls_in_window']:>5}  {r['main_failure'] or ''}")
    return "\n".join(lines)
