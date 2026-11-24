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
                "SELECT at, cash_box_cents, coin_total_cents FROM coin_polls "
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
            s.pop("raw", None)
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
        `collected_cents` (set when the box was emptied since the previous poll).
        """
        rows = self._all(
            "SELECT id, at, coin_total_cents, cash_box_cents, status_bytes FROM coin_polls "
            "WHERE phone=? ORDER BY at, id",
            phone,
        )
        prev = None
        for r in rows:
            r["coins_cents"] = r["collected_cents"] = None
            if prev:
                delta = r["coin_total_cents"] - prev["coin_total_cents"]
                r["coins_cents"] = delta if delta >= 0 else None
                if r["cash_box_cents"] < prev["cash_box_cents"]:
                    r["collected_cents"] = prev["cash_box_cents"] + max(0, delta) - r["cash_box_cents"]
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
        return rows

    def call_date_range(self, phone: str) -> tuple[str | None, str | None]:
        r = self.con.execute(
            "SELECT min(call_date), max(call_date) FROM audit_calls WHERE phone=?", (phone,)
        ).fetchone()
        return r[0], r[1]

    def call_accounting(self, phone: str) -> list[dict]:
        """Call-accounting periods, newest first. The counters (from byte 46) are not decoded yet."""
        rows = self._all(
            "SELECT id, at, period_start, period_end, raw FROM call_accounting WHERE phone=? "
            "ORDER BY at DESC, id DESC",
            phone,
        )
        for r in rows:
            body = r.pop("raw")[46:]
            r["counters_hex"] = body.rstrip(b"\0").hex(" ")
            r["nonzero_bytes"] = sum(1 for b in body if b)
        return rows

    # --- settings -----------------------------------------------------------------------------

    def system(self) -> dict | None:
        return self._one("SELECT install_date, company, license_no FROM system LIMIT 1")

    def users(self) -> list[dict]:
        return self._all("SELECT user, description, password FROM users ORDER BY rowid")

    def ports(self) -> list[dict]:
        return self._all(
            "SELECT port_no, modem_name, modem_description, line_number, init, answer, "
            "dial_1, dial_2, dial_3, reset FROM ports ORDER BY port_no"
        )

    def modems(self) -> list[dict]:
        return self._all(
            "SELECT name, description, init, answer, dial_1, dial_2, dial_3, reset FROM modems ORDER BY rowid"
        )

    def poll_lists(self) -> list[dict]:
        return self._all("SELECT name, description, start_time FROM poll_lists ORDER BY rowid")

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
        return self._all(
            "SELECT o.name, (SELECT count(*) FROM sites s WHERE s.options_record=o.name) sites "
            "FROM options_records o ORDER BY o.name"
        )


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
