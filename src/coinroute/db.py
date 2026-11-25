"""Read-only queries over an imported coinroute database.

Shared by the CLI and the GUI so neither holds SQL of its own. Standard library
only; never imports Qt. Every method returns plain dicts and lists.

Large tables (audit calls run to ~2 million rows on a real route, comm errors
to hundreds of thousands) are only read a page at a time, using keyset paging:
each row carries a `key`, and passing the last row's key as `after` returns the
next page. Rows whose sort column could not be decoded (NULL) come last.
"""

from __future__ import annotations

import csv
import datetime as _dt
import sqlite3
from os import PathLike
from pathlib import Path

from .fields import phone_display
from .xnet import (
    call_accounting_counters,
    call_detail,
    coin_extras,
    options_fields,
    poll_list_days,
    port_settings,
    site_security_code,
)

FMT = "%Y-%m-%d %H:%M"
OK_DAYS = 2  # last good poll at most this many days before the data ends -> "OK"
AMBER_DAYS = 30  # silent up to this many days -> amber; longer -> red

CSV_TABLES = [
    "system",
    "sites",
    "groups",
    "poll_lists",
    "modems",
    "ports",
    "rate_records",
    "options_records",
    "coin_polls",
    "comm_errors",
    "call_accounting",
    "audit_polls",
    "audit_calls",
]
BLOB_COLUMNS = ("raw", "raw_header", "detail")


class NotARouteDatabase(ValueError):
    pass


def _t(s: str | None) -> _dt.datetime | None:
    return _dt.datetime.strptime(s, FMT) if s else None


def status_of(last_ok: _dt.datetime | None, data_end: _dt.datetime | None) -> tuple[str, str, int | None]:
    """(kind, text, days silent). kind is 'ok', 'silent' (≤ AMBER_DAYS), 'dead' or 'never'."""
    if last_ok is None:
        return "never", "never polled OK", None
    days = (data_end - last_ok).days if data_end else 0
    if days <= OK_DAYS:
        return "ok", "OK", days
    return ("silent" if days <= AMBER_DAYS else "dead"), f"silent {days} days", days


class RouteDB:
    """An imported route, opened read-only."""

    def __init__(self, path: str | PathLike):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"{self.path} does not exist")
        self.con = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as e:
            self.con.close()
            raise NotARouteDatabase(f"{self.path} is not a coinroute database ({e})") from None
        if not {"meta", "sites", "coin_polls", "comm_errors"} <= tables:
            self.con.close()
            raise NotARouteDatabase(f"{self.path} is not a coinroute database (made by `coinroute import`)")

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _all(self, sql: str, *args) -> list[dict]:
        return [dict(r) for r in self.con.execute(sql, args)]

    def _one(self, sql: str, *args) -> dict | None:
        r = self.con.execute(sql, args).fetchone()
        return dict(r) if r else None

    # --- the database as a whole -----------------------------------------------------------

    def meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.con.execute("SELECT key, value FROM meta")}

    def data_end(self) -> _dt.datetime | None:
        return _t(self.meta().get("data_end"))

    def calls_total(self) -> int:
        """Number of individual audit calls in the database (0 if imported with --no-calls)."""
        return self.con.execute("SELECT count(*) FROM audit_calls").fetchone()[0]

    def groups(self) -> list[str]:
        """Group numbers used by sites or defined in LOCAL/GROUPS.DAT."""
        return [
            r[0]
            for r in self.con.execute(
                "SELECT group_no FROM sites UNION SELECT group_no FROM groups ORDER BY 1"
            )
            if r[0]
        ]

    # --- sites ------------------------------------------------------------------------------

    def removed_phones(self) -> list[dict]:
        """Phones that answered at some point (coin polls or audit batches) but have no site record now."""
        return self._all(
            "SELECT phone, max(group_no) group_no, max(site_no) site_no FROM ("
            " SELECT phone, group_no, site_no FROM coin_polls WHERE phone NOT IN (SELECT phone FROM sites)"
            " UNION ALL SELECT phone, group_no, site_no FROM audit_polls"
            " WHERE phone NOT IN (SELECT phone FROM sites)) GROUP BY phone ORDER BY group_no, phone"
        )

    def site_summary(self, window_days: int = 30, include_removed: bool = False) -> dict:
        """One row per site: status, last good poll, cash in box, coins and failures in the window."""
        end = self.data_end()
        since = (end - _dt.timedelta(days=window_days)).strftime(FMT) if end else "0000"
        sites = [
            dict(s, removed=False)
            for s in self.con.execute(
                "SELECT phone, description, group_no, site_no, city, state, firmware_current FROM sites "
                "ORDER BY group_no, phone"
            )
        ]
        if include_removed:
            sites += [
                {
                    "phone": r["phone"],
                    "description": "",
                    "group_no": r["group_no"],
                    "site_no": r["site_no"],
                    "city": "",
                    "state": "",
                    "firmware_current": "",
                    "removed": True,
                }
                for r in self.removed_phones()
            ]
        rows = []
        for s in sites:
            last = self._one(
                "SELECT at, cash_box_cents, coin_total_cents, raw FROM coin_polls "
                "WHERE phone=? ORDER BY at DESC LIMIT 1",
                s["phone"],
            )
            first_in_window = self._one(
                "SELECT coin_total_cents FROM coin_polls WHERE phone=? AND at>=? ORDER BY at LIMIT 1",
                s["phone"],
                since,
            )
            fails = self.con.execute(
                "SELECT count(*) FROM comm_errors WHERE phone=? AND at>=? AND code<>0", (s["phone"], since)
            ).fetchone()[0]
            why = self._one(
                "SELECT message, count(*) n FROM comm_errors WHERE phone=? AND at>=? AND code<>0 "
                "GROUP BY message ORDER BY n DESC, message LIMIT 1",
                s["phone"],
                since,
            )
            kind, text, days = status_of(_t(last["at"]) if last else None, end)
            coins = None
            if last and first_in_window:
                delta = last["coin_total_cents"] - first_in_window["coin_total_cents"]
                coins = delta if delta >= 0 else None
            rows.append(
                {
                    "group_no": s["group_no"],
                    "phone": phone_display(s["phone"]),
                    "phone_digits": s["phone"],
                    "site_no": s["site_no"],
                    "description": s["description"],
                    "place": ", ".join(x for x in (s["city"], s["state"]) if x),
                    "status": text,
                    "status_kind": kind,
                    "silent_days": days,
                    "last_ok": last["at"] if last else None,
                    "cash_box_cents": last["cash_box_cents"] if last else None,
                    "coins_in_window_cents": coins,
                    "failed_calls_in_window": fails,
                    "main_failure": why["message"] if why else None,
                    "firmware": s["firmware_current"],
                    "flags": coin_extras(last["raw"])["flags"] if last and last["raw"] else [],
                    "removed": s["removed"],
                }
            )
        return {"data_end": end.strftime(FMT) if end else None, "window_days": window_days, "sites": rows}

    def site(self, phone: str) -> dict | None:
        """Every decoded field of a site, plus counts of its history.

        A removed site (no record in SITES.DAT) gets only what its history shows.
        """
        s = self._one("SELECT * FROM sites WHERE phone=?", phone)
        if s is None:
            h = self._one(
                "SELECT max(group_no) group_no, max(site_no) site_no FROM coin_polls WHERE phone=?", phone
            )
            if not h or h["group_no"] is None:
                h = self._one(
                    "SELECT max(group_no) group_no, max(site_no) site_no FROM audit_polls WHERE phone=?",
                    phone,
                )
            if not h or h["group_no"] is None:
                return None
            s = {"phone": phone, "group_no": h["group_no"], "site_no": h["site_no"], "removed": True}
        else:
            s["security_code"] = site_security_code(s.pop("raw", None) or b"")
            s["removed"] = False
            s["rate_record_found"] = bool(
                self._one("SELECT 1 x FROM rate_records WHERE name=?", s["rate_record"])
            )
            s["options_record_found"] = bool(
                self._one("SELECT 1 x FROM options_records WHERE name=?", s["options_record"])
            )
        polls = self._one(
            "SELECT count(*) n, min(at) first, max(at) last FROM coin_polls WHERE phone=?", phone
        )
        s.update(
            {
                "coin_polls": polls["n"],
                "first_poll": polls["first"],
                "last_poll": polls["last"],
                "comm_errors": self.comm_error_count(phone),
                "calls": self.calls_count(phone),
                "call_accounting_periods": self.con.execute(
                    "SELECT count(*) FROM call_accounting WHERE phone=?", (phone,)
                ).fetchone()[0],
            }
        )
        return s

    # --- one site's history -------------------------------------------------------------------

    def coin_polls(self, phone: str) -> list[dict]:
        """All successful polls of a phone, oldest first (one a day at most, so this stays small).

        Adds `coins_cents` (growth of the lifetime total since the previous poll) and
        `collected_cents` (set when the box was emptied since the previous poll: the amount the phone
        reported if it did, else an estimate from the box and lifetime totals), plus the decoded
        trouble `flags` and record counts from the raw bytes.
        """
        rows = self._all(
            "SELECT id, at, coin_total_cents, cash_box_cents, status_bytes, raw FROM coin_polls "
            "WHERE phone=? ORDER BY at, id",
            phone,
        )
        prev = None
        for r in rows:
            r.update(coin_extras(r.pop("raw") or b""))
            r["coins_cents"] = r["collected_cents"] = None
            if prev:
                delta = r["coin_total_cents"] - prev["coin_total_cents"]
                r["coins_cents"] = delta if delta >= 0 else None
                if r["cash_box_cents"] < prev["cash_box_cents"]:
                    r["collected_cents"] = prev["cash_box_cents"] + max(0, delta) - r["cash_box_cents"]
            if r["amount_collected_cents"]:
                r["collected_cents"] = r["amount_collected_cents"]
            prev = r
        return rows

    def _page(
        self, table: str, cols: str, sort: str, where: str, args: list, after, limit: int, tail: bool = True
    ) -> list[dict]:
        """Keyset page ordered by (sort DESC, id DESC); rows with a NULL sort value follow when `tail`."""
        rows: list[dict] = []
        if after is None or after[0] is not None:
            w, a = f"{where} AND {sort} IS NOT NULL", list(args)
            if after is not None:
                w += f" AND ({sort}, id) < (?, ?)"
                a += list(after)
            rows = self._all(
                f"SELECT {cols} FROM {table} WHERE {w} ORDER BY {sort} DESC, id DESC LIMIT ?", *a, limit
            )
        if len(rows) < limit and tail:
            w, a = f"{where} AND {sort} IS NULL", list(args)
            if after is not None and after[0] is None:
                w += " AND id < ?"
                a.append(after[1])
            rows += self._all(
                f"SELECT {cols} FROM {table} WHERE {w} ORDER BY id DESC LIMIT ?", *a, limit - len(rows)
            )
        for r in rows:
            r["key"] = (r[sort], r["id"])
        return rows

    def comm_error_count(self, phone: str) -> int:
        return self.con.execute("SELECT count(*) FROM comm_errors WHERE phone=?", (phone,)).fetchone()[0]

    def comm_errors_page(self, phone: str, after=None, limit: int = 200) -> list[dict]:
        """A page of a phone's communication errors, newest first."""
        return self._page("comm_errors", "id, at, code, message", "at", "phone=?", [phone], after, limit)

    def failures_page(self, after=None, limit: int = 200) -> list[dict]:
        """Communication errors of every phone, newest first, with the site's description."""
        rows = self._page("comm_errors", "id, phone, group_no, at, code, message", "at", "1=1", [], after, limit)
        desc = dict(self.con.execute("SELECT phone, description FROM sites").fetchall())
        for r in rows:
            r["description"] = desc.get(r["phone"], "")
            r["phone_display"] = phone_display(r["phone"])
        return rows

    def collections(self, start: str, end: str) -> list[dict]:
        """Polls between start and end that found the coin box emptied, with the amount taken out."""
        sites = {r["phone"]: r for r in self._all("SELECT phone, description, group_no FROM sites")}
        out = []
        for phone, polls in self._polls_between(start, end).items():
            for r in polls:
                if "E$" in r.get("flags", []):
                    site = sites.get(phone, {})
                    out.append({
                        "phone": phone, "phone_display": phone_display(phone),
                        "group_no": site.get("group_no", ""), "description": site.get("description", ""),
                        "at": r["at"], "collected_cents": r.get("amount_collected_cents"),
                        "cash_box_cents": r["cash_box_cents"],
                    })
        return sorted(out, key=lambda r: (r["at"], r["phone"]), reverse=True)

    def comm_error_counts(self, phone: str) -> list[dict]:
        """How often each failure happened for a phone, with first and last time."""
        return self._all(
            "SELECT code, message, count(*) n, min(at) first, max(at) last FROM comm_errors "
            "WHERE phone=? GROUP BY code, message ORDER BY n DESC, code",
            phone,
        )

    @staticmethod
    def _call_filter(phone: str, date_from: str | None, date_to: str | None) -> tuple[str, list]:
        where, args = "phone=?", [phone]
        if date_from:
            where += " AND call_date>=?"
            args.append(date_from)
        if date_to:
            where += " AND call_date<=?"
            args.append(date_to)
        return where, args

    def calls_count(self, phone: str, date_from: str | None = None, date_to: str | None = None) -> int:
        where, args = self._call_filter(phone, date_from, date_to)
        return self.con.execute(f"SELECT count(*) FROM audit_calls WHERE {where}", args).fetchone()[0]

    def calls_page(
        self,
        phone: str,
        date_from: str | None = None,
        date_to: str | None = None,
        after=None,
        limit: int = 200,
    ) -> list[dict]:
        """A page of a phone's audit calls, newest first. Dates are 'YYYY-MM-DD', both ends included.

        Calls whose date could not be read come last, and only when no date range is given.
        """
        where, args = self._call_filter(phone, date_from, date_to)
        rows = self._page(
            "audit_calls",
            "id, poll_id, dialed, call_date, call_time, detail",
            "call_date",
            where,
            args,
            after,
            limit,
            tail=not (date_from or date_to),
        )
        for r in rows:
            r["detail_hex"] = r["detail"].hex(" ") if r["detail"] is not None else ""
            r.update(call_detail(r["detail"]))
        return rows

    def call_date_range(self, phone: str) -> tuple[str | None, str | None]:
        r = self.con.execute(
            "SELECT min(call_date), max(call_date) FROM audit_calls WHERE phone=?", (phone,)
        ).fetchone()
        return r[0], r[1]

    def call_accounting(self, phone: str) -> list[dict]:
        """Call-accounting periods, newest first, each with its non-empty categories and totals."""
        rows = self._all(
            "SELECT id, at, period_start, period_end, raw FROM call_accounting WHERE phone=? "
            "ORDER BY at DESC, id DESC",
            phone,
        )
        for r in rows:
            cats = call_accounting_counters(r.pop("raw") or b"")
            r["categories"] = cats
            r["calls"] = sum(c["calls"] for c in cats)
            r["money_cents"] = sum(c["money_cents"] for c in cats)
            r["minutes"] = sum(c["minutes"] for c in cats)
        return rows

    # --- reports ------------------------------------------------------------------------------

    def report_sites(self) -> list[dict]:
        return self._all("SELECT phone, description, group_no FROM sites ORDER BY group_no, phone")

    def _polls_between(self, start: str, end: str) -> dict[str, list[dict]]:
        by_phone: dict[str, list[dict]] = {}
        for r in self._all(
            "SELECT phone, at, coin_total_cents, cash_box_cents, raw FROM coin_polls "
            "WHERE at >= ? AND at <= ? ORDER BY phone, at, id", start, end
        ):
            r.update(coin_extras(r.pop("raw") or b""))
            by_phone.setdefault(r["phone"], []).append(r)
        return by_phone

    def coin_report(self, start: str, end: str) -> dict:
        """ExpressNet's Coin Report for polls between `start` and `end` ('YYYY-MM-DD HH:MM').

        Per site: coins taken in the period (growth of the lifetime total since the last poll
        before it), average per day, polls in the period, money emptied, cash in box at the last
        poll, and coins in the last 24 hours. `anomaly` marks a lifetime total that went down.
        """
        t0, t1 = _t(start), _t(end)
        days = max(1, round((t1 - t0).total_seconds() / 86400))
        polls = self._polls_between(start, end)
        rows = []
        for s in self.report_sites():
            ps = polls.get(s["phone"], [])
            row = {**s, "phone_display": phone_display(s["phone"]), "polls": len(ps), "days": days}
            if not ps:
                rows.append({**row, "active": False})
                continue
            before = self._one(
                "SELECT coin_total_cents FROM coin_polls WHERE phone=? AND at < ? ORDER BY at DESC LIMIT 1",
                s["phone"], start,
            )
            base = before["coin_total_cents"] if before else ps[0]["coin_total_cents"]
            totals = [base] + [p["coin_total_cents"] for p in ps]
            anomaly = any(b < a for a, b in zip(totals, totals[1:]))
            total = max(0, ps[-1]["coin_total_cents"] - base)
            last = ps[-1]
            day_ago = (_t(last["at"]) - _dt.timedelta(hours=24)).strftime(FMT)
            ref = self._one(
                "SELECT coin_total_cents FROM coin_polls WHERE phone=? AND at <= ? ORDER BY at DESC LIMIT 1",
                s["phone"], day_ago,
            )
            last24 = max(0, last["coin_total_cents"] - ref["coin_total_cents"]) if ref else None
            rows.append({
                **row, "active": True, "total_cents": total, "avg_per_day_cents": total / days,
                "collected_cents": sum(p["amount_collected_cents"] for p in ps),
                "cash_box_cents": last["cash_box_cents"], "last_24h_cents": last24, "anomaly": anomaly,
                "short": len({p["at"][:10] for p in ps}) < days,
            })
        active = [r for r in rows if r["active"]]
        totals = {
            "phones": len(rows),
            "total_cents": sum(r["total_cents"] for r in active),
            "avg_per_day_cents": sum(r["avg_per_day_cents"] for r in active),
            "collected_cents": sum(r["collected_cents"] for r in active),
            "cash_box_cents": sum(r["cash_box_cents"] for r in active),
            "last_24h_cents": sum(r["last_24h_cents"] or 0 for r in active),
        }
        return {"start": start, "end": end, "days": days, "rows": rows, "totals": totals}

    def trouble_report(self, start: str, end: str, codes: list[str] | None = None) -> dict:
        """ExpressNet's Trouble Report: every poll in the period with trouble flags (only `codes`
        if given), and the sites that did not answer at all in the period."""
        polls = self._polls_between(start, end)
        wanted = set(codes) if codes else None
        rows = []
        for s in self.report_sites():
            ps = polls.get(s["phone"], [])
            base = {**s, "phone_display": phone_display(s["phone"])}
            if not ps:
                rows.append({**base, "at": None, "flags": [], "cash_box_cents": None, "no_comms": True})
                continue
            for p in ps:
                flags = [f for f in p["flags"] if wanted is None or f in wanted]
                if flags:
                    rows.append({**base, "at": p["at"], "flags": flags, "cash_box_cents": p["cash_box_cents"],
                                 "no_comms": False})
        return {"start": start, "end": end, "codes": sorted(wanted) if wanted else None, "rows": rows}

    # --- settings -----------------------------------------------------------------------------

    def system(self) -> dict | None:
        return self._one("SELECT install_date, company, license_no FROM system LIMIT 1")

    def users(self) -> list[dict]:
        return self._all("SELECT user, description, password FROM users ORDER BY rowid")

    def ports(self) -> list[dict]:
        rows = self._all(
            "SELECT port_no, modem_name, modem_description, line_number, init, answer, "
            "dial_1, dial_2, dial_3, reset, raw FROM ports ORDER BY port_no"
        )
        for r in rows:
            r.update(port_settings(r.pop("raw") or b""))
        return rows

    def modems(self) -> list[dict]:
        return self._all(
            "SELECT name, description, init, answer, dial_1, dial_2, dial_3, reset FROM modems ORDER BY rowid"
        )

    def poll_lists(self) -> list[dict]:
        rows = self._all("SELECT name, description, start_time, raw FROM poll_lists ORDER BY rowid")
        for r in rows:
            days = poll_list_days(r.pop("raw") or b"")
            r["days"] = days
            r["days_text"] = "every day" if len(days) == 7 else (", ".join(days) or "no days")
        return rows

    def group_table(self) -> list[dict]:
        """Groups with how many sites each holds (groups used by sites but not defined are included)."""
        return self._all(
            "SELECT g.group_no, (SELECT count(*) FROM sites s WHERE s.group_no=g.group_no) sites, "
            "g.group_no IN (SELECT group_no FROM groups) defined "
            "FROM (SELECT group_no FROM groups UNION SELECT group_no FROM sites) g ORDER BY g.group_no"
        )

    def rate_records(self) -> list[dict]:
        return self._all(
            "SELECT r.name, (SELECT count(*) FROM sites s WHERE s.rate_record=r.name) sites "
            "FROM rate_records r ORDER BY r.name"
        )

    def options_records(self) -> list[dict]:
        """Options records with description, security codes and how many sites use each."""
        rows = self._all(
            "SELECT o.name, (SELECT count(*) FROM sites s WHERE s.options_record=o.name) sites, o.raw "
            "FROM options_records o ORDER BY o.name"
        )
        for r in rows:
            r.update(options_fields(r.pop("raw") or b""))
        return rows


def export_csv(
    db_path: str | PathLike, outdir: str | PathLike, include_calls: bool = False, progress=None
) -> list[Path]:
    """Write every table to OUTDIR/<table>.csv, leaving out the raw byte columns.

    `progress(table, rows_written)` is called per table and every 100,000 rows.
    """
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    with RouteDB(db_path) as db:
        for t in CSV_TABLES:
            if t == "audit_calls" and not include_calls:
                continue
            cur = db.con.execute(f"SELECT * FROM {t}")
            cols = [c[0] for c in cur.description]
            keep = [i for i, c in enumerate(cols) if c not in BLOB_COLUMNS]
            path = out / f"{t}.csv"
            n = 0
            if progress:
                progress(t, 0)
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([cols[i] for i in keep])
                for row in cur:
                    w.writerow([row[i] for i in keep])
                    n += 1
                    if progress and n % 100_000 == 0:
                        progress(t, n)
            written.append(path)
    return written
