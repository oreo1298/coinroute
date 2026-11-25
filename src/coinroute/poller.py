"""Polling a phone: ExpressNet's call steps, and storing the result like ExpressNet did.

    line = SerialLine("/dev/ttyUSB0", 9600)            # or simphone.SimModemLine() to try it
    outcome = Poller(Modem(line)).poll(site)            # site: RouteDB.site(phone)
    PollStore(db_path).record(site, outcome)

A poll follows docs/PROTOCOL.md: reset the modem, dial, receive the phone's status, check its
number, send the security code, (optionally) read the call-record counts and records, clear
the coin flag after noting the cash collected, set the phone's clock, end the call and hang
up. A failed step stops the poll with ExpressNet's error code for that step (xnet.COMM_ERRORS).

PollStore writes into the route database: a successful poll adds a COIN.DAT-style row to
coin_polls (lifetime coins, cash in box, the phone's trouble flags), a failed one a row to
comm_errors, so every screen and report shows them like imported history. The phone's status
frame and the call log are kept in coinroute's own table `polls` (raw bytes, for later
decoding). Standard library only.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from os import PathLike

from . import protocol as p
from .demo import cdr_header
from .modem import Modem, ModemError
from .xnet import comm_error_text

FMT = "%Y-%m-%d %H:%M"
COUNTER = 100000  # the phone's coin counter: 5 decimal digits of cents
MAX_WRAP = 20000  # a counter that went down by more than a $200 wrap could explain was reset


@dataclass
class PollOutcome:
    phone: str
    at: _dt.datetime
    ok: bool = False
    code: int = 0  # 0, or the failing step's index into xnet.COMM_ERRORS
    steps: list[str] = field(default_factory=list)
    status: p.Status | None = None
    connect: str = ""
    billing: list[bytes] = field(default_factory=list)
    audit: list[bytes] = field(default_factory=list)
    log: list[tuple[str, bytes]] = field(default_factory=list)

    @property
    def message(self) -> str:
        return "Poll complete" if self.ok else comm_error_text(self.code)


class Poller:
    def __init__(self, modem: Modem, *, progress: Callable[[str], None] | None = None,
                 collect_records: bool = False, clear_records: bool = False, store: PollStore | None = None,
                 set_clock: bool = True, dial_prefix: str = "",
                 now: Callable[[], _dt.datetime] | None = None, reply_timeout: float = 5.0,
                 status_timeout: float = 30.0):
        self.modem = modem
        self.progress = progress or (lambda step: None)
        self.collect_records = collect_records
        # records are cleared from the phone only once they are safe in the database
        self.clear_records = clear_records and store is not None
        self.store = store
        self.set_clock = set_clock
        self.dial_prefix = dial_prefix
        self.now = now or (lambda: _dt.datetime.now().replace(second=0, microsecond=0))
        self.reply_timeout = reply_timeout
        self.status_timeout = status_timeout

    def poll(self, site: dict) -> PollOutcome:
        out = PollOutcome(site["phone"], self.now())

        def step(name: str) -> None:
            out.steps.append(name)
            self.progress(name)

        def fail(code: int) -> PollOutcome:
            out.code = code
            out.steps.append(f"Failed: {comm_error_text(code)}")
            self.progress(out.steps[-1])
            return out

        link = None
        try:
            step("Resetting the modem")
            try:
                self.modem.reset()
            except ModemError:
                return fail(80)
            step(f"Dialing {self.dial_prefix}{site['phone']}")
            r = self.modem.dial(self.dial_prefix + site["phone"])
            if not r.connected:
                return fail(r.error_code)
            out.connect = r.result
            link = self.modem.link(timeout=self.reply_timeout)

            step("Receive phone status")
            frame = link.receive(self.status_timeout)
            if frame is None:
                return fail(8)
            if not frame.ok:
                return fail(10)
            try:
                st = out.status = p.parse_status(frame.body)
            except p.ProtocolError:
                return fail(9)
            if not st.matches(site["phone"]):
                return fail(11)
            if site.get("phone_type") and not st.type_ok(site["phone_type"]):
                return fail(12)

            step("Send security code")
            code = site.get("security_code")
            if not code:
                return fail(14)
            try:
                link.security(code)
            except p.ProtocolError as e:
                return fail(13 if "no reply" in str(e) else 14)

            if self.collect_records:
                err = self._records(link, st, out, step, site)
                if err:
                    return fail(err)

            if "E$" in _flag_codes(st.flags):
                step("Clear coin flag")
                try:
                    link.clear_flags(*p.CLEAR_COIN)
                except p.ProtocolError:
                    return fail(33)

            if self.set_clock:
                step("Reset phone clock")
                try:
                    link.set_clock(out.at.timetuple())
                except p.ProtocolError as e:
                    return fail(72 if "no reply" in str(e) else 73)

            step("End of call")
            for letter in "ON":  # end-of-call commands; a phone that hangs up first is fine
                try:
                    link.command(p.cmd_simple(letter))
                except p.ProtocolError:
                    break
            out.ok = True
            return out
        finally:
            if link is not None:
                out.log = list(link.log)
            step("Hang up")
            try:
                self.modem.hangup()
            except ModemError:
                pass

    def _records(self, link: p.Link, st: p.Status, out: PollOutcome, step, site: dict) -> int:
        """Read the billing and audit records the phone holds; with `clear_records`, save them and
        then clear them from the phone with T / K and the matching flag masks, as ExpressNet does."""
        step("Collect CDR counts")
        try:
            c = link.cdr_counts()
        except p.ProtocolError:
            return 37
        clear = p.clear_code(site.get("rate_record_raw"), st.phone_type)
        kinds = (
            ("billing", p.billing_base(st.phone_type), p.BILLING_RECORD, p.BILLING_SLOTS, p.BILLING_PER_READ,
             c.billing_first, c.billing_count, c.billing_ok, out.billing, 40,
             p.cmd_clear_billing, 46, p.CLEAR_BILLING, 47),
            ("audit", p.audit_base(st.phone_type, st.model), p.AUDIT_RECORD, p.AUDIT_SLOTS, p.AUDIT_PER_READ,
             c.audit_first, c.audit_count, c.audit_ok, out.audit, 48,
             p.cmd_clear_audit, 53, p.CLEAR_AUDIT, 54),
        )
        for (name, base, size, slots, per, first, count, ok, into, code,
             clear_cmd, clear_err, flags, flags_err) in kinds:
            reads = p.record_reads(p.ring_reads(base, size, slots, first, count, ok), size, per)
            if reads:
                step(f"Get {name} CDRs ({sum(n for _, n in reads) // size})")
            got = []
            for addr, n in reads:
                try:
                    data = link.read_memory(addr, n)
                except p.ProtocolError:
                    return code
                got += [data[i:i + size] for i in range(0, len(data), size)]
            into += got
            if not (self.clear_records and got):
                continue
            self.store.save_pending(site["phone"], name, got)  # safe before the phone forgets them
            step(f"Clear {name} CDRs")
            try:
                link.command(clear_cmd(clear[0], clear[1]))
            except p.ProtocolError:
                return clear_err
            try:
                link.clear_flags(*flags)
            except p.ProtocolError:
                return flags_err
        return 0


def _flag_codes(flags: bytes) -> list[str]:
    from .xnet import trouble_flags

    return trouble_flags(bytes(48) + bytes(flags))


SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (id INTEGER PRIMARY KEY, phone TEXT, at TEXT, ok INTEGER, code INTEGER,
    message TEXT, connect TEXT, accumulator INTEGER, coin_poll_id INTEGER, comm_error_id INTEGER,
    status BLOB, steps TEXT, log TEXT);
CREATE TABLE IF NOT EXISTS poll_records (id INTEGER PRIMARY KEY, poll_id INTEGER, kind TEXT, raw BLOB,
    phone TEXT);
CREATE INDEX IF NOT EXISTS polls_phone ON polls(phone, at);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


class PollStore:
    """Writes poll results into a route database (made by `coinroute import`)."""

    def __init__(self, db_path: str | PathLike):
        self.con = sqlite3.connect(str(db_path))
        self.con.executescript(SCHEMA)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(poll_records)")}
        if "phone" not in cols:  # databases polled by an earlier version
            self.con.execute("ALTER TABLE poll_records ADD COLUMN phone TEXT")

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def save_pending(self, phone: str, kind: str, records: list[bytes]) -> None:
        """Keep call records the moment they are read (before the phone is told to clear them);
        record() attaches them to the poll."""
        self.con.executemany("INSERT INTO poll_records (poll_id, kind, raw, phone) VALUES (NULL, ?, ?, ?)",
                             [(kind, r, phone) for r in records])
        self.con.commit()

    def setting(self, key: str, default: str = "") -> str:
        r = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self.con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        self.con.commit()

    def record(self, site: dict, out: PollOutcome) -> int:
        """Store one poll; returns its id in `polls`."""
        cur = self.con.cursor()
        at = out.at.strftime(FMT)
        group, site_no = site.get("group_no") or "", site.get("site_no") or ""
        coin_id = err_id = None
        acc = out.status.coin_total if out.ok and out.status else None
        if out.ok and out.status:
            lifetime, in_box = self._totals(site["phone"], out.status)
            raw = bytearray(cdr_header(88, site["phone"], out.at, group, site_no))
            raw[44], raw[45] = len(out.billing), len(out.audit)
            raw[46], raw[47] = len(out.billing), len(out.audit)
            raw[48:57] = out.status.flags[:9]
            collected = out.status.cash_collected if "E$" in _flag_codes(out.status.flags) else 0
            struct.pack_into("<III", raw, 64, lifetime, in_box, collected or 0)
            cur.execute(
                "INSERT INTO coin_polls (phone, group_no, site_no, at, coin_total_cents, cash_box_cents, "
                "status_bytes, raw) VALUES (?,?,?,?,?,?,?,?)",
                (site["phone"], group, site_no, at, lifetime, in_box, bytes(raw[56:64]).hex(), bytes(raw)),
            )
            coin_id = cur.lastrowid
        else:
            raw = bytearray(cdr_header(130, site["phone"], out.at, group, site_no))
            raw[44] = out.code
            cur.execute(
                "INSERT INTO comm_errors (phone, group_no, site_no, at, code, message, raw) VALUES (?,?,?,?,?,?,?)",
                (site["phone"], group, site_no, at, out.code, out.message, bytes(raw)),
            )
            err_id = cur.lastrowid
        cur.execute(
            "INSERT INTO polls (phone, at, ok, code, message, connect, accumulator, coin_poll_id, comm_error_id, "
            "status, steps, log) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (site["phone"], at, int(out.ok), out.code, out.message, out.connect, acc, coin_id, err_id,
             out.status.raw if out.status else None, "\n".join(out.steps),
             "\n".join(f"{d} {b.hex(' ')}" for d, b in out.log)),
        )
        poll_id = cur.lastrowid
        pending = {(k, r) for k, r in cur.execute(
            "SELECT kind, raw FROM poll_records WHERE poll_id IS NULL AND phone=?", (site["phone"],))}
        cur.execute("UPDATE poll_records SET poll_id=? WHERE poll_id IS NULL AND phone=?", (poll_id, site["phone"]))
        cur.executemany("INSERT INTO poll_records (poll_id, kind, raw, phone) VALUES (?,?,?,?)",
                        [(poll_id, k, r, site["phone"]) for k, recs in (("billing", out.billing), ("audit", out.audit))
                         for r in recs if (k, r) not in pending])
        end = self.con.execute("SELECT value FROM meta WHERE key='data_end'").fetchone()
        if not end or (end[0] or "") < at:
            self.con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('data_end', ?)", (at,))
        self.con.commit()
        return poll_id

    def _totals(self, phone: str, st: p.Status) -> tuple[int, int]:
        """Lifetime coins and cash in box after this poll, the way ExpressNet works them out: the
        change in the phone's 5-digit coin counter since the last poll is added to both, and the
        cash collected (when the box was emptied) comes out of the box."""
        last = self.con.execute(
            "SELECT coin_total_cents, cash_box_cents FROM coin_polls WHERE phone=? ORDER BY at DESC, id DESC "
            "LIMIT 1", (phone,)).fetchone()
        prev_acc = self.con.execute(
            "SELECT accumulator FROM polls WHERE phone=? AND ok=1 AND accumulator IS NOT NULL "
            "ORDER BY at DESC, id DESC LIMIT 1", (phone,)).fetchone()
        lifetime, in_box = (last[0] or 0, last[1] or 0) if last else (0, 0)
        acc = st.coin_total
        delta = 0
        if prev_acc and acc is not None:
            if acc >= prev_acc[0]:
                delta = acc - prev_acc[0]
            else:  # it went down: past 999.99 and round again, or reset (then count from zero)
                wrapped = acc + COUNTER - prev_acc[0]
                delta = wrapped if wrapped <= MAX_WRAP else acc
        collected = st.cash_collected or 0 if "E$" in _flag_codes(st.flags) else 0
        return lifetime + delta, max(0, in_box + delta - collected)


def last_accumulator(db_path, phone: str) -> int | None:
    """The phone's coin counter at its last successful coinroute poll (None if never polled)."""
    with PollStore(db_path) as st:
        r = st.con.execute("SELECT accumulator FROM polls WHERE phone=? AND ok=1 AND accumulator IS NOT NULL "
                           "ORDER BY at DESC, id DESC LIMIT 1", (phone,)).fetchone()
    return r[0] if r else None


def simulated_phone(db_path, site: dict):
    """A simulated phone for `site` that carries on from its last poll with a few coins more."""
    from .simphone import SimPhone

    ph = SimPhone(security_code=site.get("security_code") or "0000", phone=site["phone"])
    ph.coin_total = ((last_accumulator(db_path, site["phone"]) or 0) + 375) % COUNTER
    return ph


def site_for_poll(db, phone: str) -> dict | None:
    """What a poll needs from the route database (RouteDB): number, codes, group and site number."""
    s = db.site(phone)
    if not s or s.get("removed"):
        return None
    return {"phone": phone, "security_code": s.get("security_code"), "group_no": s.get("group_no"),
            "site_no": s.get("site_no"), "description": s.get("description"),
            "rate_record_raw": db.rate_record_raw(s.get("rate_record"))}
