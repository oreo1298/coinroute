"""A synthetic ExpressNet 3 installation, for demos, screenshots and tests.

Everything here is invented: 555-01xx phone numbers, made-up places and a
made-up company. It is written in the same file formats coinroute reads
(see FORMATS.md), so the importer and the GUI can be exercised end to end
without anybody's real route data.

    make_demo(root, sites=20, days=400, seed=1)  ->  root/XNET

The same seed always produces byte-identical files.
"""
from __future__ import annotations

import datetime as dt
import math
import random
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .fields import datetime_to_minutes
from .scramble import KEY_SALT_OFFSET, PW_OFFSET, encode_password, password_key, scramble_syspar

DEMO_END = dt.datetime(2026, 5, 2, 1, 5)       # the last nightly poll run in the demo data

# --- low-level record builders ------------------------------------------------------


def put(buf: bytearray, off: int, text: str, width: int) -> None:
    """Write a NUL-terminated string into a fixed-width field."""
    b = text.encode("latin-1")[:width - 1]
    buf[off:off + width] = b + b"\0" * (width - len(b))


def header(reclen: int, variable: bool = False) -> bytearray:
    """A 128-byte c-tree file header with the fields coinroute reads."""
    h = bytearray(128)
    struct.pack_into("<I", h, 0x28, 0x81 | (0x01000000 if variable else 0))
    struct.pack_into("<H", h, 0x2C, reclen)
    return h


def deleted(rec: bytes, link: int = 0) -> bytes:
    """Mark a record deleted the way c-tree does: 0xFF, then a free-list link."""
    r = bytearray(rec)
    r[0] = 0xFF
    struct.pack_into("<I", r, 1, link)
    return bytes(r)


def fixed_file(reclen: int, recs: list[bytes], deleted: int = 0) -> bytes:
    """A fixed-length c-tree file. `deleted` appends that many blank deleted slots."""
    first = -(-128 // reclen)
    out = bytearray(header(reclen))
    out += b"\xff" * (first * reclen - 128)
    for r in recs:
        assert len(r) == reclen
        out += r
    for _ in range(deleted):
        out += b"\xff" + b"\0" * (reclen - 1)
    return bytes(out)


def var_file(recs: list[bytes], garbage_after: int | None = None, garbage: bytes | None = None) -> bytes:
    """A variable-length c-tree file; optionally with a damaged stretch before record `garbage_after`."""
    out = bytearray(header(42, variable=True))
    for i, r in enumerate(recs):
        if garbage_after is not None and i == garbage_after:
            out += garbage if garbage is not None else bytes(range(256)) * 3
        out += b"\xfa\xfa" + struct.pack("<II", len(r) + 10, len(r)) + r
    out += b"\xfd\xfd" + struct.pack("<II", 30, 0) + b"\0" * 20
    return bytes(out)


def cdr_header(reclen: int, phone: str, when: dt.datetime, group: str = "000100", site: str = "100") -> bytearray:
    r = bytearray(reclen)
    put(r, 1, group, 7)
    put(r, 8, site.ljust(8), 9)
    put(r, 17, phone, 11)
    struct.pack_into("<I", r, 28, datetime_to_minutes(when))
    put(r, 32, when.strftime("%y%m%d"), 7)
    put(r, 39, when.strftime("%H%M"), 5)
    return r


def coin(phone: str, when: dt.datetime, total: int, box: int, status: bytes = bytes.fromhex("0001006000020000"),
         **kw) -> bytes:
    """CDR/COIN.DAT record: lifetime coin total and cash in box, in cents."""
    r = cdr_header(88, phone, when, **kw)
    r[56:64] = status
    struct.pack_into("<II", r, 64, total, box)
    return bytes(r)


def commerr(phone: str, when: dt.datetime, code: int, **kw) -> bytes:
    """CDR/COMMERR.DAT record: one failed call to a phone."""
    r = cdr_header(130, phone, when, **kw)
    r[44] = code
    return bytes(r)


def callacct(phone: str, when: dt.datetime, start: dt.date, end: dt.date, counters: list[int],
             group: str = "000100", site: str = "100") -> bytes:
    """CDR/CALLACCT.DAT record. The counter layout from offset 46 is invented (it is still ❓)."""
    r = cdr_header(552, phone, when, group=group, site=site)
    put(r, 32, start.strftime("%y%m%d"), 7)
    put(r, 39, end.strftime("%y%m%d"), 7)
    for k, n in enumerate(counters[:(552 - 48) // 2]):
        struct.pack_into("<H", r, 48 + 2 * k, n)
    return bytes(r)


def audit(phone: str, when: dt.datetime, calls, group: str = "000100", site: str = "100",
          extra: bytes = b"") -> bytes:
    """CDR/AUDITCDR.DAT record: one poll's batch of calls.

    `calls` holds (dialed, datetime) or (dialed, datetime, detail16). The 16 detail
    bytes are still undecoded in real data; the demo puts duration / charge /
    deposit / type there only so the column isn't empty.
    """
    r = bytearray(42)
    put(r, 1, group, 7)
    put(r, 8, site.ljust(8), 9)
    put(r, 17, phone, 11)
    struct.pack_into("<I", r, 28, datetime_to_minutes(when))
    put(r, 32, when.strftime("%Y%m%d"), 9)
    r[41] = len(calls)
    for c in calls:
        dialed, t = c[0], c[1]
        e = bytearray(47)
        put(e, 0, dialed.ljust(16), 17)
        put(e, 17, t.strftime("%Y%m%d"), 9)
        put(e, 26, t.strftime("%H%M"), 5)
        e[31:47] = c[2] if len(c) > 2 else struct.pack("<IIII", 60, 50, 50, 1)
        r += e
    return bytes(r + extra)


def site(phone: str, desc: str, group: str = "000100", city: str = "SPRINGFIELD", state: str = "CA",
         firmware: str = "DD7019-0023R", *, site_no: str = "100", rate: str = "CALIFORNIA",
         options: str = "CALIFORNIA", installed: str = "08/12/09", location: str | None = None,
         address: str = "1 MAIN ST", zip_code: str = "99999-    ", firmware_target: str | None = None) -> bytes:
    """SITES/SITES.DAT record (2456 bytes)."""
    r = bytearray(2456)
    put(r, 1, phone, 11)
    put(r, 12, desc.ljust(60), 61)
    put(r, 86, rate, 13)
    put(r, 112, options, 13)
    put(r, 383, group, 7)
    put(r, 390, site_no.ljust(8), 9)
    put(r, 896, "1200", 5)
    put(r, 903, installed, 9)
    put(r, 980, (location or desc)[:30].ljust(30), 31)
    put(r, 1011, address.ljust(30), 31)
    put(r, 1042, city.ljust(20), 21)
    put(r, 1063, state, 3)
    put(r, 1066, zip_code, 11)
    put(r, 1250, firmware, 13)
    put(r, 1263, firmware_target or firmware, 13)
    return bytes(r)


def syspar(company: str = "Test Payphone Co.", lic: str = "1234567890", date: str = "04/20/00") -> bytes:
    """SYSTEM/X3SYSPAR.DAT record, scrambled as ExpressNet stores it."""
    p = bytearray(211)
    put(p, 1, date, 9)
    put(p, 19, company, 31)
    put(p, 50, lic, 11)
    return scramble_syspar(bytes(p))


def user(name: str, password: str, desc: str = "TEST USER", salt: int = 0x0E) -> bytes:
    """SYSTEM/PASSWORD.DAT record with an encoded password."""
    r = bytearray(83)
    put(r, 1, name.ljust(12), 13)
    put(r, 14, desc, 41)
    r[KEY_SALT_OFFSET] = salt
    enc = encode_password(password, password_key(r))
    r[PW_OFFSET:PW_OFFSET + 8] = enc.ljust(8, b"\0")
    return bytes(r)


def modem(name: str, desc: str, init: str, answer: str, dials: tuple[str, str, str], reset: str) -> bytes:
    """PARAM/MODEMS.DAT record (230 bytes)."""
    r = bytearray(230)
    put(r, 1, name, 21)
    put(r, 22, desc, 41)
    put(r, 63, init, 41)
    put(r, 104, answer, 41)
    for off, d in zip((145, 166, 187), dials):
        put(r, off, d, 21)
    put(r, 208, reset, 21)
    return bytes(r)


def port(no: int, modem_rec: bytes, line: str) -> bytes:
    """LOCAL/PORTS.DAT record (313 bytes): a copy of a modem profile plus the port's line."""
    r = bytearray(313)
    r[1] = no
    r[3:3 + 229] = modem_rec[1:230]      # the port keeps its own copy of the modem strings
    put(r, 232, line, 14)
    return bytes(r)


def group(no: str) -> bytes:
    r = bytearray(22)
    put(r, 1, no, 7)
    return bytes(r)


def poll_list(name: str, desc: str, start: str) -> bytes:
    r = bytearray(231)
    put(r, 1, name, 13)
    put(r, 14, desc, 61)
    put(r, 79, start, 6)
    return bytes(r)


def named(reclen: int, name: str) -> bytes:
    """PARAM/CALLCOST.DAT or PARAM/MISCOPT.DAT record: only the name is known."""
    r = bytearray(reclen)
    put(r, 1, name, 13)
    return bytes(r)


# --- the demo route --------------------------------------------------------------------

UPMS_INIT = "ATM1L0E0Q0V1X3S10=255S0=0S9=2S7=100&D2"

PLACES = [  # (city, state, area code, zip) -- invented; 555-01xx numbers are reserved for fiction
    ("LAKEVIEW", "CA", "714", "92800"), ("MILLBROOK", "CA", "949", "92600"),
    ("CEDAR FLATS", "TX", "210", "78200"), ("HARLOW", "TX", "512", "78700"),
    ("DRY CREEK", "NV", "702", "89100"), ("NORTH FORK", "NV", "775", "89500"),
]
GROUPS = ["000100", "000200", "000300", "000400"]
SPOTS = [
    "BUS DEPOT NORTH", "LAUNDROMAT", "COUNTY COURT LOBBY", "HOSPITAL ER WAITING", "TRUCK STOP DIESEL ISLE",
    "PUBLIC LIBRARY", "GAS STATION PUMP 4", "MOTEL OFFICE", "CITY PARK RESTROOMS", "AIRPORT BAGGAGE CLAIM",
    "GROCERY FRONT ENTRANCE", "BOWLING ALLEY", "CORNER DINER", "COLLEGE DORM LOBBY", "FERRY LANDING",
    "JAIL VISITOR ROOM", "MARINA FUEL DOCK", "REST AREA EASTBOUND", "TRAIN STATION PLATFORM 2",
    "STADIUM GATE C", "SENIOR CENTER", "FLEA MARKET", "CAR WASH", "LIQUOR STORE",
]
STREETS = ["ELM ST", "HARBOR BLVD", "MAIN ST", "RIVER RD", "OAK AVE", "STATION WAY", "MARKET ST", "PINE DR"]

# comm error codes, see xnet.COMM_ERRORS
DEAD_CODES = [4, 4, 4, 5, 5, 6]                  # carrier time-out, BUSY, no carrier
FLAKY_CODES = [5, 8, 10, 4, 13, 37, 48, 39]      # occasional failures of a working phone
NEW_SITE_CODES = [14, 11, 13]                    # security code / phone number problems

KINDS = ("ok", "outage", "amber", "red", "red_unpolled", "never")


@dataclass
class _Site:
    i: int
    phone: str
    desc: str
    group: str
    site_no: str
    city: str
    state: str
    zip: str
    kind: str
    calls_per_day: float
    removed_at: dt.datetime | None = None
    dead_from: dt.datetime | None = None          # no calls, no answer from this night on
    unpolled_from: dt.datetime | None = None      # taken off the poll list
    outage: tuple[dt.datetime, dt.datetime] | None = None
    first_night: dt.datetime | None = None
    status: bytes = b""
    total: int = 0
    box: int = 0
    collect_every: int = 30
    next_collect: dt.datetime | None = None
    pending: list = field(default_factory=list)  # calls not yet collected by a poll
    ca_next: dt.datetime | None = None
    ca_counts: list = field(default_factory=lambda: [0] * 12)


def _poisson(rng: random.Random, lam: float) -> int:
    k, p, limit = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _kinds(n: int) -> list[str]:
    never = 1 if n >= 4 else 0
    amber = max(1, n // 10) if n >= 3 else 0
    red = max(1, n * 3 // 20) if n >= 2 else 0
    unpolled = 1 if n >= 8 else 0
    outage = 1 if n >= 6 else 0
    kinds = ["never"] * never + ["amber"] * amber + ["red"] * red + ["red_unpolled"] * unpolled
    kinds += ["outage"] * outage
    kinds += ["ok"] * (n - len(kinds))
    return kinds[:n]


def _dialed(rng: random.Random, area: str) -> tuple[str, int, int]:
    """A synthetic dialed number, its call type and the coins it took (cents)."""
    x = rng.random()
    if x < 0.62:
        return f"555{rng.randint(100, 199):04d}", 1, 50
    if x < 0.74:
        npa = rng.choice(["714", "210", "702", "949", "512", "775"])
        return f"1{npa}555{rng.randint(100, 199):04d}", 2, rng.choice([100, 125, 150, 200, 250])
    if x < 0.86:
        return f"1800555{rng.randint(100, 199):04d}", 3, 0
    return rng.choice(["911", "411", "0", "5551212"]), 4, rng.choice([0, 0, 50])


def make_demo(root: str | Path, sites: int = 20, days: int = 400, seed: int = 1,
              end: dt.datetime = DEMO_END) -> Path:
    """Write a synthetic ExpressNet folder to root/XNET and return its path.

    The route has `sites` payphones in four groups. Most answer every night;
    a few died at different dates (carrier time-outs, BUSY), one was down for
    a while and repaired, one was taken off the poll list, one new phone has
    never been polled successfully, and a few site records were deleted but
    their history stays in the CDR files. Cash boxes fill and get emptied.
    AUDITCDR.DAT has one damaged stretch and one record of the wrong size.
    """
    rng = random.Random(seed)
    x = Path(root) / "XNET"
    for d in ("SITES", "CDR", "LOCAL", "PARAM", "SYSTEM"):
        (x / d).mkdir(parents=True, exist_ok=True)

    start = end - dt.timedelta(days=days)
    n_removed = max(1, sites // 7)
    spots = SPOTS[:]
    rng.shuffle(spots)
    kinds = _kinds(sites)
    rng.shuffle(kinds)
    kinds += ["removed"] * n_removed

    roster: list[_Site] = []
    for i, kind in enumerate(kinds):
        city, state, npa, zip_code = PLACES[i % len(PLACES)]
        s = _Site(i=i, phone=f"{npa}555{100 + i:04d}", desc=spots[i % len(spots)],
                  group=GROUPS[(i * 7) % len(GROUPS)], site_no=str(110 + 10 * i), city=city, state=state,
                  zip=zip_code, kind=kind, calls_per_day=rng.choice([0.4, 0.7, 1.0, 1.5, 2.2, 3.0]))
        s.status = bytes([0, 1, 0, rng.choice([0x40, 0x60, 0x68]), 0, rng.choice([0, 2]), 0, 0])
        s.total = rng.randint(400, 9000) * 100
        s.box = rng.randint(0, 4000)
        s.collect_every = rng.randint(21, 42)
        s.next_collect = start + dt.timedelta(days=rng.randint(1, s.collect_every))
        s.ca_next = start + dt.timedelta(days=rng.randint(3, 14))
        s.first_night = start + dt.timedelta(days=1)
        span = max(days, 30)
        if kind == "amber":
            s.dead_from = end - dt.timedelta(days=rng.randint(6, 25))
        elif kind == "red":
            s.dead_from = end - dt.timedelta(days=rng.randint(45, max(46, span * 3 // 4)))
        elif kind == "red_unpolled":
            s.dead_from = end - dt.timedelta(days=rng.randint(60, max(61, span // 2)))
            s.unpolled_from = s.dead_from + dt.timedelta(days=rng.randint(10, 20))
        elif kind == "outage":
            a = end - dt.timedelta(days=rng.randint(max(91, span // 2), max(92, span * 2 // 3)))
            s.outage = (a, a + dt.timedelta(days=rng.randint(25, 50)))
        elif kind == "never":
            s.first_night = end - dt.timedelta(days=rng.randint(12, 20))
            s.total = s.box = 0
        elif kind == "removed":
            s.removed_at = end - dt.timedelta(days=rng.randint(min(60, span // 2), max(61, span - 30)))
            s.group = GROUPS[i % len(GROUPS)]
        roster.append(s)

    busy = next((s for s in roster if s.kind == "ok"), roster[0])
    busy.calls_per_day = 5.5                          # one busy phone, so call lists run to several pages

    coins, errs, auds, cas = [], [], [], []

    def collect_audit(s: _Site, when: dt.datetime):
        while s.pending:
            batch, s.pending = s.pending[:255], s.pending[255:]
            auds.append((when, audit(s.phone, when, batch, group=s.group, site=s.site_no)))

    night = start + dt.timedelta(days=1)
    while night <= end:
        day_before = (night - dt.timedelta(days=1)).date()
        for k, s in enumerate(roster):
            kw = {"group": s.group, "site": s.site_no}
            if night < s.first_night or (s.removed_at and night >= s.removed_at):
                continue
            dead = (s.dead_from is not None and night >= s.dead_from) or (
                s.outage is not None and s.outage[0] <= night < s.outage[1])
            # yesterday's calls (a dead phone takes none)
            if not dead:
                for _ in range(_poisson(rng, s.calls_per_day)):
                    t = dt.datetime.combine(day_before, dt.time(rng.randint(6, 23), rng.randint(0, 59)))
                    dialed, ctype, cents = _dialed(rng, s.phone[:3])
                    if s.kind != "never":
                        s.total += cents
                        s.box += cents
                    secs = rng.randint(20, 900) if ctype != 4 else rng.randint(10, 120)
                    s.pending.append((dialed, t, struct.pack("<IIII", secs, cents, cents, ctype)))
                    s.ca_counts[ctype] += 1
                s.pending.sort(key=lambda c: c[1])
            # the collector empties the box (between polls)
            if night >= s.next_collect and not dead:
                s.box = rng.randint(0, 150)          # coins that came in after the box was emptied
                s.next_collect = night + dt.timedelta(days=s.collect_every + rng.randint(-5, 5))
            elif s.box > 9000 and not dead:
                s.box = 0                            # "cash volume full" -- emptied early
            if s.unpolled_from and night >= s.unpolled_from:
                continue
            t = night.replace(hour=1, minute=0) + dt.timedelta(minutes=2 * k + rng.randint(0, 1))
            if s.kind == "never":
                for _ in range(rng.randint(1, 2)):
                    errs.append(commerr(s.phone, t, rng.choice(NEW_SITE_CODES), **kw))
                    t += dt.timedelta(minutes=rng.randint(3, 9))
                continue
            if dead:
                for _ in range(rng.randint(1, 3)):
                    errs.append(commerr(s.phone, t, rng.choice(DEAD_CODES), **kw))
                    t += dt.timedelta(minutes=rng.randint(4, 12))
                continue
            if rng.random() < 0.05:                  # a bad night for a working phone
                errs.append(commerr(s.phone, t, rng.choice(FLAKY_CODES), **kw))
                if rng.random() < 0.5:
                    continue                         # try again tomorrow
                t = night.replace(hour=rng.choice([8, 9]), minute=rng.randint(0, 59))   # manual re-poll
            coins.append((t, coin(s.phone, t, s.total, s.box, status=s.status, **kw)))
            collect_audit(s, t)
            if night >= s.ca_next:
                cas.append((t, callacct(s.phone, t, day_before, night.date(), s.ca_counts, **kw)))
                s.ca_counts = [0] * 12
                s.ca_next = night + dt.timedelta(days=rng.randint(7, 14))
        night += dt.timedelta(days=1)

    # --- files ---
    live = [s for s in roster if s.kind != "removed"]
    gone = [s for s in roster if s.kind == "removed"]
    rate_names = {"CA": "STANDARD-CA", "TX": "STANDARD-TX", "NV": "STANDARD-NV"}

    def site_rec(s: _Site) -> bytes:
        years = rng.randint(1, 14)
        installed = (end - dt.timedelta(days=365 * years + rng.randint(0, 300))) if s.kind != "never" \
            else s.first_night - dt.timedelta(days=2)
        fw = "DD7019-0023R" if s.i % 5 else "DD7019-0021R"
        return site(s.phone, s.desc, group=s.group, city=s.city, state=s.state, firmware=fw,
                    firmware_target="DD7019-0023R", site_no=s.site_no, rate=rate_names[s.state],
                    options="NIGHT-POLL" if s.i % 3 else "STANDARD", installed=installed.strftime("%m/%d/%y"),
                    address=f"{100 + 37 * s.i} {STREETS[s.i % len(STREETS)]}", zip_code=f"{s.zip[:3]}{s.i:02d}-    ")

    site_recs = [site_rec(s) for s in live]
    # deleted site records sit between live ones, as c-tree reuses and frees slots
    for j, s in enumerate(gone):
        site_recs.insert(min(len(site_recs), 3 + 5 * j), deleted(site_rec(s), link=0))
    (x / "SITES/SITES.DAT").write_bytes(fixed_file(2456, site_recs))

    coins.sort(key=lambda c: c[0])
    (x / "CDR/COIN.DAT").write_bytes(fixed_file(88, [c for _, c in coins], deleted=2))
    (x / "CDR/COMMERR.DAT").write_bytes(fixed_file(130, errs))
    cas.sort(key=lambda c: c[0])
    (x / "CDR/CALLACCT.DAT").write_bytes(fixed_file(552, [c for _, c in cas]))

    auds.sort(key=lambda a: a[0])
    audit_recs = [a for _, a in auds]
    damage_at = None
    if audit_recs:
        # one record whose size doesn't match its call count (seen in real files)
        mid = len(audit_recs) // 3
        audit_recs[mid] = audit_recs[mid] + b"\0" * 5
        damage_at = len(audit_recs) * 2 // 3
    # one stretch of unrelated bytes, as from a cross-linked FAT cluster
    junk = bytes(b if b not in (0xFA, 0xFD) else 0x90 for b in rng.randbytes(4096))
    (x / "CDR/AUDITCDR.DAT").write_bytes(var_file(audit_recs, garbage_after=damage_at, garbage=junk))

    (x / "SYSTEM/X3SYSPAR.DAT").write_bytes(fixed_file(211, [syspar("Demo Payphone Co.", "0000012345")]))
    (x / "SYSTEM/PASSWORD.DAT").write_bytes(fixed_file(83, [
        user("PROTEL", "PROTELX3", "FACTORY LOGIN", salt=0x5A),
        user("OFFICE", "COINS42", "OFFICE MANAGER", salt=0x21),
        user("TECH", "FIXIT", "FIELD TECHNICIAN", salt=0x77),
    ]))
    upms = modem("UPMS1200", "Protel UPMS1200 1200 bps PSK modem", UPMS_INIT, "ATA",
                 ("ATDT", "ATDT", "ATDT"), "ATZ")
    generic = modem("GENERIC", "Hayes compatible modem at 300 bps", UPMS_INIT.replace("L0", "L2")[:-3], "ATA",
                    ("ATDT", "ATDT", "ATDT"), "ATZ")
    (x / "PARAM/MODEMS.DAT").write_bytes(fixed_file(230, [upms, generic]))
    (x / "LOCAL/PORTS.DAT").write_bytes(fixed_file(313, [port(1, upms, "555-0100"), port(2, generic, "555-0101")]))
    (x / "LOCAL/GROUPS.DAT").write_bytes(fixed_file(22, [group(g) for g in GROUPS]))
    (x / "LOCAL/POLLIST.DAT").write_bytes(fixed_file(231, [
        poll_list("NIGHTLY", "Every phone, every night", "01:00"),
        poll_list("MORNING", "Re-poll phones that failed overnight", "08:00"),
        poll_list("INCOMING", "Window for phones that call in", "03:00"),
    ]))
    (x / "PARAM/CALLCOST.DAT").write_bytes(fixed_file(36781, [named(36781, n) for n in rate_names.values()]))
    (x / "PARAM/MISCOPT.DAT").write_bytes(fixed_file(420, [named(420, n) for n in ("STANDARD", "NIGHT-POLL")]))
    return x
