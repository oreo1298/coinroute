"""Adding, editing and removing sites in a route database (ExpressNet's Edit Site Record).

Edits change the site's record bytes at the same offsets ExpressNet uses (FORMATS.md) and then
decode them again, so the `sites` columns and the `raw` bytes always agree. Every change is
logged in `site_changes`; a removed site's record is kept in `removed_sites` and can be put
back. The ExpressNet folder is never touched. Standard library only.

    with SiteEditor(db_path) as ed:
        ed.update("2105550108", {"description": "AIRPORT GATE 4", "security_code": "1234"})
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from dataclasses import dataclass
from os import PathLike

from . import xnet
from .demo import put

RECORD_LEN = 2456


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    offset: int
    width: int  # bytes in the record, including the closing NUL
    pad: bool = False  # ExpressNet pads this one with spaces
    digits: bool = False

    @property
    def max_len(self) -> int:
        return self.width - 1


# the editable fields of SITES.DAT, in the order of the site screen
FIELDS = [
    Field("description", "Description", 12, 61, pad=True),
    Field("group_no", "Group", 383, 7),
    Field("site_no", "Site number", 390, 9, pad=True),
    Field("location_name", "Location", 980, 31, pad=True),
    Field("address", "Address", 1011, 31, pad=True),
    Field("city", "City", 1042, 21, pad=True),
    Field("state", "State", 1063, 3),
    Field("zip", "ZIP", 1066, 11),
    Field("rate_record", "Call costing record", 86, 13),
    Field("options_record", "Options record", 112, 13),
    Field("speed", "Phone speed", 896, 5, digits=True),
    Field("install_date", "Installed (mm/dd/yy)", 903, 9),
    Field("firmware_target", "Firmware to download", 1263, 13),
]
SECURITY = Field("security_code", "Security code", 1278, 3, digits=True)
BY_KEY = {f.key: f for f in [*FIELDS, SECURITY]}

SCHEMA = """
CREATE TABLE IF NOT EXISTS site_changes (id INTEGER PRIMARY KEY, phone TEXT, at TEXT, action TEXT,
    field TEXT, old TEXT, new TEXT);
CREATE TABLE IF NOT EXISTS removed_sites (phone TEXT PRIMARY KEY, at TEXT, raw BLOB);
"""
COLUMNS = ["phone", "description", "group_no", "site_no", "rate_record", "options_record", "speed",
           "install_date", "location_name", "address", "city", "state", "zip", "firmware_current",
           "firmware_target", "raw"]


class SiteError(ValueError):
    """An edit that can't be saved; the message says why, for the person typing."""


def digits_only(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def encode(raw: bytearray, key: str, value: str) -> None:
    """Write one field's value into a site record (validated)."""
    f = BY_KEY[key]
    value = (value or "").strip()
    if key == "security_code":
        if value and (len(value) != 4 or not value.isdigit()):
            raise SiteError("The security code is 4 digits.")
        raw[1278:1280] = bytes.fromhex(value) if value else bytes(2)
        return
    if key == "install_date" and value:
        try:
            _dt.datetime.strptime(value, "%m/%d/%y")
        except ValueError:
            raise SiteError("Installed: use mm/dd/yy, e.g. 04/20/09.") from None
    if f.digits and value and not value.isdigit():
        raise SiteError(f"{f.label}: digits only.")
    if key in ("group_no", "state", "rate_record", "options_record", "firmware_target"):
        value = value.upper()
    if len(value) > f.max_len:
        raise SiteError(f"{f.label}: at most {f.max_len} characters.")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        raise SiteError(f"{f.label}: plain letters, digits and punctuation only.") from None
    put(raw, f.offset, value.ljust(f.max_len) if f.pad else value, f.width)


def blank_record(phone: str) -> bytearray:
    r = bytearray(RECORD_LEN)
    put(r, 1, phone, 11)
    for f in FIELDS:
        if f.pad:
            put(r, f.offset, "".ljust(f.max_len), f.width)
    return r


class SiteEditor:
    def __init__(self, db_path: str | PathLike, now=None):
        self.con = sqlite3.connect(str(db_path))
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self.now = now or (lambda: _dt.datetime.now().strftime("%Y-%m-%d %H:%M"))

    def close(self) -> None:
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- checks ---------------------------------------------------------------------------------

    def _check_links(self, values: dict) -> None:
        for key, table in (("rate_record", "rate_records"), ("options_record", "options_records")):
            name = (values.get(key) or "").strip().upper()
            if name and not self.con.execute(f"SELECT 1 FROM {table} WHERE name=?", (name,)).fetchone():
                raise SiteError(f"There is no {BY_KEY[key].label.lower()} called {name}.")

    def _store(self, phone: str, raw: bytes, insert: bool) -> None:
        d = xnet.site_record(bytes(raw))
        vals = [d[c] if c != "raw" else bytes(raw) for c in COLUMNS]
        if insert:
            self.con.execute(f"INSERT INTO sites ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                             vals)
        else:
            sets = ", ".join(f"{c}=?" for c in COLUMNS[1:])
            self.con.execute(f"UPDATE sites SET {sets} WHERE phone=?", vals[1:] + [phone])

    def _log(self, phone: str, action: str, field: str = "", old: str = "", new: str = "") -> None:
        masked = field == "security_code"
        self.con.execute("INSERT INTO site_changes (phone, at, action, field, old, new) VALUES (?,?,?,?,?,?)",
                         (phone, self.now(), action, field, "••••" if masked and old else old,
                          "••••" if masked and new else new))

    # --- edits ----------------------------------------------------------------------------------

    def values(self, phone: str) -> dict:
        """The editable fields of a site as text."""
        r = self.con.execute("SELECT raw FROM sites WHERE phone=?", (phone,)).fetchone()
        if r is None:
            raise SiteError(f"No site {phone}.")
        d = xnet.site_record(bytes(r["raw"]))
        raw = bytes(r["raw"])
        out = {f.key: xnet.cstr(raw[f.offset:f.offset + f.width]).rstrip() for f in FIELDS}
        out["zip"] = out["zip"].rstrip("- ")  # ExpressNet's ZIP+4 mask leaves a dash on 5-digit ZIPs
        out["security_code"] = d["security_code"] or ""
        return out

    def update(self, phone: str, changes: dict) -> list[str]:
        """Change some fields of a site; returns the keys that changed."""
        r = self.con.execute("SELECT raw FROM sites WHERE phone=?", (phone,)).fetchone()
        if r is None:
            raise SiteError(f"No site {phone}.")
        before = self.values(phone)
        changes = {k: v for k, v in changes.items() if k in BY_KEY and (v or "").strip() != before.get(k, "")}
        self._check_links(changes)
        raw = bytearray(r["raw"])
        if len(raw) < RECORD_LEN:
            raw += bytes(RECORD_LEN - len(raw))
        for k, v in changes.items():
            encode(raw, k, v)
        if changes:
            self._store(phone, raw, insert=False)
            for k, v in changes.items():
                self._log(phone, "edit", k, before.get(k, ""), (v or "").strip())
            self.con.commit()
        return list(changes)

    def add(self, phone: str, values: dict) -> str:
        """A new site; `phone` is its 10-digit number. Returns the digits."""
        phone = digits_only(phone)
        if len(phone) != 10:
            raise SiteError("The phone number is 10 digits (area code and number).")
        if self.con.execute("SELECT 1 FROM sites WHERE phone=?", (phone,)).fetchone():
            raise SiteError(f"There is already a site {phone}.")
        self._check_links(values)
        raw = blank_record(phone)
        for k, v in values.items():
            if k in BY_KEY and v:
                encode(raw, k, v)
        self._store(phone, raw, insert=True)
        self._log(phone, "add")
        self.con.execute("DELETE FROM removed_sites WHERE phone=?", (phone,))
        self.con.commit()
        return phone

    def remove(self, phone: str) -> None:
        """Take a site off the route. Its history stays (as a removed site) and the record is kept."""
        r = self.con.execute("SELECT raw FROM sites WHERE phone=?", (phone,)).fetchone()
        if r is None:
            raise SiteError(f"No site {phone}.")
        self.con.execute("INSERT OR REPLACE INTO removed_sites (phone, at, raw) VALUES (?,?,?)",
                         (phone, self.now(), r["raw"]))
        self.con.execute("DELETE FROM sites WHERE phone=?", (phone,))
        self._log(phone, "remove")
        self.con.commit()

    def restore(self, phone: str) -> None:
        r = self.con.execute("SELECT raw FROM removed_sites WHERE phone=?", (phone,)).fetchone()
        if r is None:
            raise SiteError(f"{phone} was not removed in coinroute, so there is no record to put back.")
        if self.con.execute("SELECT 1 FROM sites WHERE phone=?", (phone,)).fetchone():
            raise SiteError(f"There is already a site {phone}.")
        self._store(phone, r["raw"], insert=True)
        self.con.execute("DELETE FROM removed_sites WHERE phone=?", (phone,))
        self._log(phone, "restore")
        self.con.commit()

    def changes(self, phone: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT phone, at, action, field, old, new FROM site_changes"
        args: tuple = ()
        if phone:
            q += " WHERE phone=?"
            args = (phone,)
        return [dict(r) for r in self.con.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]
