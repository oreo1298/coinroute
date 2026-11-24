"""Small helpers for ExpressNet's C structs: strings, numbers, dates."""
from __future__ import annotations

import datetime as _dt
import struct

# ExpressNet stores times as u32 minutes since 1899-12-31 00:00 (local time).
EPOCH = _dt.datetime(1899, 12, 31)


def cstr(b: bytes) -> str:
    """A fixed-width, NUL-terminated, space-padded C string."""
    return b.split(b"\0", 1)[0].decode("latin-1").rstrip()


def u8(b: bytes, o: int) -> int:
    return b[o]


def u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def minutes_to_datetime(m: int) -> _dt.datetime | None:
    return EPOCH + _dt.timedelta(minutes=m) if m else None


def datetime_to_minutes(t: _dt.datetime) -> int:
    return int((t - EPOCH).total_seconds() // 60)


def iso(t: _dt.datetime | None) -> str | None:
    return t.strftime("%Y-%m-%d %H:%M") if t else None


def yymmdd(s: str) -> str | None:
    """'260502' -> '2026-05-02'. Years 70-99 are 19xx."""
    s = s.strip()
    if len(s) != 6 or not s.isdigit():
        return None
    yy = int(s[:2])
    return f"{1900 + yy if yy >= 70 else 2000 + yy:04d}-{s[2:4]}-{s[4:6]}"


def yyyymmdd(s: str) -> str | None:
    s = s.strip()
    if len(s) != 8 or not s.isdigit():
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def mmddyy(s: str) -> str | None:
    """'08/12/09' -> '2009-08-12'."""
    s = s.strip()
    if len(s) != 8 or s[2] != "/" or s[5] != "/" or not (s[:2] + s[3:5] + s[6:]).isdigit():
        return None
    yy = int(s[6:])
    return f"{1900 + yy if yy >= 70 else 2000 + yy:04d}-{s[:2]}-{s[3:5]}"


def hhmm(s: str) -> str | None:
    s = s.strip()
    if len(s) != 4 or not s.isdigit():
        return None
    return f"{s[:2]}:{s[2:]}"


def phone_display(digits: str) -> str:
    d = digits.strip()
    return f"{d[:3]}-{d[3:6]}-{d[6:]}" if len(d) == 10 and d.isdigit() else d
