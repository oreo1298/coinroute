"""Printable Coin and Trouble reports, laid out like ExpressNet's, as HTML.

The data comes from RouteDB.coin_report / trouble_report; this module only
renders it. The HTML sticks to what Qt's rich-text engine understands (plain
tables, a little inline CSS) so the GUI can preview and print it, and it prints
the same from a browser. Standard library only.
"""

from __future__ import annotations

import datetime as _dt
from html import escape

from .xnet import TROUBLE_FLAGS

CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #111; }
h1 { font-size: 14pt; margin: 0; }
.meta { color: #555; margin: 2px 0 10px 0; }
table.r { border-collapse: collapse; width: 100%; }
table.r th { text-align: left; border-bottom: 1px solid #888; padding: 3px 6px; font-weight: 600; }
table.r td { padding: 2px 6px; border-bottom: 1px solid #ddd; }
table.r td.n, table.r th.n { text-align: right; }
table.r tr.total td { border-top: 1px solid #888; border-bottom: none; font-weight: 600; }
.muted { color: #777; }
.legend td { padding: 1px 10px 1px 0; color: #444; }
@page { size: landscape; margin: 12mm; }
"""


def _money(c) -> str:
    return "" if c is None else f"{c / 100:,.2f}"


def _when(s: str | None) -> str:
    return _dt.datetime.strptime(s, "%Y-%m-%d %H:%M").strftime("%m/%d/%Y %H:%M") if s else ""


def _page(title: str, period: str, company: str | None, body: str) -> str:
    made = _dt.datetime.now().strftime("%m/%d/%Y %H:%M")
    who = f"{escape(company)} &middot; " if company else ""
    return (
        f"<html><head><meta charset='utf-8'><title>{escape(title)}</title><style>{CSS}</style></head>"
        f"<body><h1>{escape(title)}</h1><div class='meta'>{who}{period} &middot; printed {made} "
        f"by coinroute</div>{body}</body></html>"
    )


def coin_report_html(data: dict, company: str | None = None) -> str:
    rows = []
    group = None
    for r in data["rows"]:
        if group is not None and r["group_no"] != group:
            rows.append("<tr><td colspan='9'>&nbsp;</td></tr>")
        group = r["group_no"]
        first = (
            f"<td>{escape(r['group_no'] or '')}</td><td>{escape(r['phone_display'])}</td>"
            f"<td>{escape(r['description'] or '')}</td>"
        )
        if not r["active"]:
            rows.append(f"<tr>{first}<td colspan='6' class='muted'>no activity</td></tr>")
            continue
        marks = "+" if r["anomaly"] else ""
        days = f"{r['polls']}{'*' if r['short'] else ''}"
        rows.append(
            f"<tr>{first}<td class='n'>{_money(r['avg_per_day_cents'])}</td>"
            f"<td class='n'>{_money(r['total_cents'])}{marks}</td><td class='n'>{days}</td>"
            f"<td class='n'>{_money(r['collected_cents'])}</td>"
            f"<td class='n'>{_money(r['cash_box_cents'])}</td>"
            f"<td class='n'>{_money(r['last_24h_cents'])}</td></tr>"
        )
    t = data["totals"]
    rows.append(
        f"<tr class='total'><td></td><td>{t['phones']} phones</td><td>Total</td>"
        f"<td class='n'>{_money(t['avg_per_day_cents'])}</td><td class='n'>{_money(t['total_cents'])}</td>"
        f"<td></td><td class='n'>{_money(t['collected_cents'])}</td>"
        f"<td class='n'>{_money(t['cash_box_cents'])}</td>"
        f"<td class='n'>{_money(t['last_24h_cents'])}</td></tr>"
    )
    head = (
        "<tr><th>Group</th><th>Phone</th><th>Description</th><th class='n'>Avg/day</th>"
        "<th class='n'>Total</th><th class='n'>Polls</th><th class='n'>Collected</th>"
        "<th class='n'>In box</th><th class='n'>Last 24 h</th></tr>"
    )
    notes = (
        "<p class='muted'>Total: coins in the period. Polls: good polls in the period; * = fewer "
        f"than the {data['days']} days covered. + = the lifetime coin total went down (possible anomaly). "
        "Collected: money taken out when boxes were emptied.</p>"
    )
    period = f"From {_when(data['start'])} to {_when(data['end'])}"
    return _page(
        "Coin Report",
        period,
        company,
        f"<table class='r' cellspacing='0'>{head}{''.join(rows)}</table>{notes}",
    )


def trouble_report_html(data: dict, company: str | None = None) -> str:
    names = {code: name for code, name, _, _ in TROUBLE_FLAGS}
    rows = []
    for r in data["rows"]:
        first = (
            f"<td>{escape(r['group_no'] or '')}</td><td>{escape(r['phone_display'])}</td>"
            f"<td>{escape(r['description'] or '')}</td>"
        )
        if r["no_comms"]:
            rows.append(f"<tr>{first}<td colspan='3' class='muted'>no communications with phone</td></tr>")
        else:
            rows.append(
                f"<tr>{first}<td>{_when(r['at'])}</td><td><b>{escape(' '.join(r['flags']))}</b></td>"
                f"<td class='n'>{_money(r['cash_box_cents'])}</td></tr>"
            )
    if not rows:
        rows.append("<tr><td colspan='6' class='muted'>No trouble in this period.</td></tr>")
    head = (
        "<tr><th>Group</th><th>Phone</th><th>Description</th><th>Polled</th><th>Flags</th>"
        "<th class='n'>In box</th></tr>"
    )
    used = sorted({c for r in data["rows"] for c in r["flags"]})
    legend = "".join(
        f"<tr><td><b>{escape(c)}</b></td><td>{escape(names.get(c, '?'))}</td></tr>" for c in used
    )
    which = (
        "all trouble flags" if not data["codes"] else "flags " + ", ".join(escape(c) for c in data["codes"])
    )
    period = f"From {_when(data['start'])} to {_when(data['end'])} &middot; {which}"
    body = f"<table class='r' cellspacing='0'>{head}{''.join(rows)}</table>"
    if legend:
        body += f"<p><b>Flags in this report</b></p><table class='legend'>{legend}</table>"
    return _page("Trouble Report", period, company, body)


def default_period(data_end: str | None, days: int) -> tuple[str, str]:
    """`days` days back from the end of the data (or from now when the database is empty)."""
    end = (
        _dt.datetime.strptime(data_end, "%Y-%m-%d %H:%M")
        if data_end
        else _dt.datetime.now().replace(second=0, microsecond=0)
    )
    start = end - _dt.timedelta(days=days)
    return start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")


# --- plain text, for the terminal and for printers ---------------------------------------------


def _text_head(title: str, period: str, company: str | None, width: int) -> list[str]:
    made = _dt.datetime.now().strftime("%m/%d/%Y %H:%M")
    return [title.center(width).rstrip(), (company or "").center(width).rstrip(), period.center(width).rstrip(),
            f"Printed {made}".rjust(width), ""]


def coin_report_text(data: dict, company: str | None = None) -> str:
    """The Coin Report as fixed-width text (98 columns)."""
    hdr = (f"{'Group':<7} {'Phone':<13} {'Description':<26} {'Avg/day':>9} {'Total':>10} {'Polls':>6} "
           f"{'Collected':>10} {'In box':>9} {'Last 24h':>8}")
    out = _text_head("COIN REPORT", f"From {_when(data['start'])} to {_when(data['end'])}", company, len(hdr))
    out += [hdr, "-" * len(hdr)]
    group = None
    for r in data["rows"]:
        if group is not None and r["group_no"] != group:
            out.append("")
        group = r["group_no"]
        first = f"{(r['group_no'] or ''):<7} {r['phone_display']:<13} {(r['description'] or '')[:26]:<26}"
        if not r["active"]:
            out.append(f"{first} {'no activity':>9}")
            continue
        polls = f"{r['polls']}{'*' if r['short'] else ''}"
        out.append(
            f"{first} {_money(r['avg_per_day_cents']):>9} "
            f"{_money(r['total_cents']) + ('+' if r['anomaly'] else ''):>10} {polls:>6} "
            f"{_money(r['collected_cents']):>10} {_money(r['cash_box_cents']):>9} {_money(r['last_24h_cents']):>8}"
        )
    t = data["totals"]
    out += ["-" * len(hdr),
            f"{'':<7} {str(t['phones']) + ' phones':<13} {'TOTAL':<26} {_money(t['avg_per_day_cents']):>9} "
            f"{_money(t['total_cents']):>10} {'':>6} {_money(t['collected_cents']):>10} "
            f"{_money(t['cash_box_cents']):>9} {_money(t['last_24h_cents']):>8}",
            "",
            f"Polls: good polls in the period; * = fewer than the {data['days']} days covered.",
            "+ = the lifetime coin total went down (possible anomaly).",
            "Collected: money taken out when coin boxes were emptied."]
    return "\n".join(out) + "\n"


def trouble_report_text(data: dict, company: str | None = None) -> str:
    """The Trouble Report as fixed-width text (80 columns), with a legend of the flags used."""
    names = {code: name for code, name, _, _ in TROUBLE_FLAGS}
    # 76 columns: fits an 80-column screen inside a box, and any printer
    hdr = f"{'Group':<6} {'Phone':<12} {'Description':<22} {'Polled':<14} {'Flags':<10} {'In box':>6}"
    which = "all trouble flags" if not data["codes"] else "flags " + ", ".join(data["codes"])
    out = _text_head("TROUBLE REPORT", f"From {_when(data['start'])} to {_when(data['end'])} - {which}",
                     company, len(hdr))
    out += [hdr, "-" * len(hdr)]
    for r in data["rows"]:
        first = f"{(r['group_no'] or ''):<6} {r['phone_display']:<12} {(r['description'] or '')[:22]:<22}"
        if r["no_comms"]:
            out.append(f"{first} no communications with phone")
        else:
            polled = _dt.datetime.strptime(r["at"], "%Y-%m-%d %H:%M").strftime("%m/%d/%y %H:%M")
            out.append(f"{first} {polled:<14} {' '.join(r['flags'])[:10]:<10} {_money(r['cash_box_cents']):>6}")
    if not data["rows"]:
        out.append("No trouble in this period.")
    used = sorted({c for r in data["rows"] for c in r["flags"]})
    if used:
        out += ["", "Flags in this report:"] + [f"  {c:<3} {names.get(c, '?')}" for c in used]
    return "\n".join(out) + "\n"
