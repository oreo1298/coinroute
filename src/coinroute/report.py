"""Route health summary from an imported database (text form for the CLI)."""
from __future__ import annotations

from .db import RouteDB


def route_summary(db_path, window_days: int = 30, include_removed: bool = False) -> dict:
    """See RouteDB.site_summary."""
    with RouteDB(db_path) as db:
        return db.site_summary(window_days, include_removed=include_removed)


def _money(c):
    return "" if c is None else f"{c / 100:,.2f}"


def format_summary(summary: dict) -> str:
    rows = summary["sites"]
    ok = sum(1 for r in rows if r["status_kind"] == "ok")
    lines = [f"Data ends {summary['data_end']}.  {len(rows)} sites: {ok} answering, {len(rows) - ok} not.",
             f"Coins and failures are for the last {summary['window_days']} days of data.", ""]
    hdr = f"{'Group':<7} {'Phone':<13} {'Description':<32} {'Status':<16} {'In box':>9} {'Coins':>8} {'Fails':>5}  Main failure"
    lines += [hdr, "-" * len(hdr)]
    for r in rows:
        desc = "(site record deleted)" if r["removed"] else r["description"]
        lines.append(f"{r['group_no']:<7} {r['phone']:<13} {desc[:32]:<32} {r['status']:<16} "
                     f"{_money(r['cash_box_cents']):>9} {_money(r['coins_in_window_cents']):>8} "
                     f"{r['failed_calls_in_window']:>5}  {r['main_failure'] or ''}")
    return "\n".join(lines)
