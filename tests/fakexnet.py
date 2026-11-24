"""Build small synthetic ExpressNet folders for tests. No customer data."""
from __future__ import annotations

import datetime as dt
import struct
from pathlib import Path

from coinroute.fields import datetime_to_minutes
from coinroute.scramble import KEY_SALT_OFFSET, PW_OFFSET, encode_password, password_key, scramble_syspar


def put(buf: bytearray, off: int, text: str, width: int):
    b = text.encode("latin-1")[:width - 1]
    buf[off:off + width] = b + b"\0" * (width - len(b))


def header(reclen: int, variable: bool = False) -> bytearray:
    h = bytearray(128)
    struct.pack_into("<I", h, 0x28, 0x81 | (0x01000000 if variable else 0))
    struct.pack_into("<H", h, 0x2C, reclen)
    return h


def fixed_file(reclen: int, recs: list[bytes], deleted: int = 0) -> bytes:
    first = -(-128 // reclen)
    out = bytearray(header(reclen))
    out += b"\xff" * (first * reclen - 128)
    for r in recs:
        assert len(r) == reclen
        out += r
    for _ in range(deleted):
        out += b"\xff" + b"\0" * (reclen - 1)
    return bytes(out)


def var_file(recs: list[bytes], garbage_after: int | None = None) -> bytes:
    out = bytearray(header(42, variable=True))
    for i, r in enumerate(recs):
        if garbage_after is not None and i == garbage_after:
            out += bytes(range(256)) * 3          # damaged stretch
        out += b"\xfa\xfa" + struct.pack("<II", len(r) + 10, len(r)) + r
    out += b"\xfd\xfd" + struct.pack("<II", 30, 0) + b"\0" * 20
    return bytes(out)


def cdr_header(reclen, phone, when: dt.datetime, group="000100", site="100"):
    r = bytearray(reclen)
    put(r, 1, group, 7)
    put(r, 8, site.ljust(8), 9)
    put(r, 17, phone, 11)
    struct.pack_into("<I", r, 28, datetime_to_minutes(when))
    put(r, 32, when.strftime("%y%m%d"), 7)
    put(r, 39, when.strftime("%H%M"), 5)
    return r


def coin(phone, when, total, box, **kw):
    r = cdr_header(88, phone, when, **kw)
    r[56:64] = bytes.fromhex("0001006000020000")
    struct.pack_into("<II", r, 64, total, box)
    return bytes(r)


def commerr(phone, when, code, **kw):
    r = cdr_header(130, phone, when, **kw)
    r[44] = code
    return bytes(r)


def audit(phone, when, calls, group="000100", site="100"):
    r = bytearray(42)
    put(r, 1, group, 7)
    put(r, 8, site.ljust(8), 9)
    put(r, 17, phone, 11)
    struct.pack_into("<I", r, 28, datetime_to_minutes(when))
    put(r, 32, when.strftime("%Y%m%d"), 9)
    r[41] = len(calls)
    for dialed, t in calls:
        e = bytearray(47)
        put(e, 0, dialed.ljust(16), 17)
        put(e, 17, t.strftime("%Y%m%d"), 9)
        put(e, 26, t.strftime("%H%M"), 5)
        struct.pack_into("<IIII", e, 31, 60, 50, 50, 1)
        r += e
    return bytes(r)


def site(phone, desc, group="000100", city="SPRINGFIELD", state="CA", firmware="DD7019-0023R"):
    r = bytearray(2456)
    put(r, 1, phone, 11)
    put(r, 12, desc.ljust(60), 61)
    put(r, 86, "CALIFORNIA", 13)
    put(r, 112, "CALIFORNIA", 13)
    put(r, 383, group, 7)
    put(r, 390, "100".ljust(8), 9)
    put(r, 896, "1200", 5)
    put(r, 903, "08/12/09", 9)
    put(r, 980, desc[:30].ljust(30), 31)
    put(r, 1011, "1 MAIN ST".ljust(30), 31)
    put(r, 1042, city.ljust(20), 21)
    put(r, 1063, state, 3)
    put(r, 1066, "99999-    ", 11)
    put(r, 1250, firmware, 13)
    put(r, 1263, firmware, 13)
    return bytes(r)


def syspar(company="Test Payphone Co.", lic="1234567890", date="04/20/00"):
    p = bytearray(211)
    put(p, 1, date, 9)
    put(p, 19, company, 31)
    put(p, 50, lic, 11)
    return scramble_syspar(bytes(p))


def user(name, password, desc="TEST USER", salt=0x0E):
    r = bytearray(83)
    put(r, 1, name.ljust(12), 13)
    put(r, 14, desc, 41)
    r[KEY_SALT_OFFSET] = salt
    enc = encode_password(password, password_key(r))
    r[PW_OFFSET:PW_OFFSET + 8] = enc.ljust(8, b"\0")
    return bytes(r)


def build(root: Path, *, damaged_audit=False) -> Path:
    """Make a small XNET folder: 2 phones, one answering, one dead."""
    x = root / "XNET"
    for d in ("SITES", "CDR", "LOCAL", "PARAM", "SYSTEM"):
        (x / d).mkdir(parents=True, exist_ok=True)
    end = dt.datetime(2026, 5, 2, 1, 5)
    (x / "SITES/SITES.DAT").write_bytes(fixed_file(2456, [site("7145550101", "COURT LOBBY"),
                                                          site("2105550199", "POOL PHONE", group="000200",
                                                               city="SHELBYVILLE", state="TX")], deleted=2))
    coins = [coin("7145550101", end - dt.timedelta(days=d), 100000 + 25 * (40 - d), 5000 + 25 * (40 - d))
             for d in range(40, -1, -1)]
    coins.append(coin("2105550199", end - dt.timedelta(days=100), 20000, 1500, group="000200"))
    (x / "CDR/COIN.DAT").write_bytes(fixed_file(88, coins, deleted=3))
    errs = [commerr("2105550199", end - dt.timedelta(days=d), 4, group="000200") for d in range(10)]
    errs.append(commerr("2105550199", end - dt.timedelta(days=50), 5, group="000200"))
    (x / "CDR/COMMERR.DAT").write_bytes(fixed_file(130, errs))
    t = dt.datetime(2026, 4, 30, 14, 0)
    auds = [audit("7145550101", end - dt.timedelta(days=1), [("5551212", t), ("911", t)]),
            audit("7145550101", end, [("5550000", t)])]
    (x / "CDR/AUDITCDR.DAT").write_bytes(var_file(auds, garbage_after=1 if damaged_audit else None))
    (x / "SYSTEM/X3SYSPAR.DAT").write_bytes(fixed_file(211, [syspar()]))
    (x / "SYSTEM/PASSWORD.DAT").write_bytes(fixed_file(83, [user("PROTEL", "SECRET12")]))
    return x
