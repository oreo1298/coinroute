"""Make a small sample of an ExpressNet folder that coinroute can still read.

For handing real data to a development session: drops what coinroute doesn't
read (indexes, backups, the program), keeps only recent history in the big
log files, removes deleted records, and by default masks the last four digits
of dialed numbers and leaves out logins and billing (card) records.

The result is NOT usable by ExpressNet itself (no index files). The source
folder is only read.
"""
from __future__ import annotations

import datetime as _dt
import io
import os
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import ctree
from .fields import datetime_to_minutes, minutes_to_datetime, u32

FULL_HISTORY_FILES = {"CDR/COIN.DAT", "CDR/CALLACCT.DAT"}   # small once deleted slots are gone
WINDOW_FILES = {"CDR/COMMERR.DAT"}                            # repetitive: recent window only
AUDIT_FILE = "CDR/AUDITCDR.DAT"
PRIVATE_FILES = {"SYSTEM/PASSWORD.DAT"}
BILLING_FILES = {"CDR/BILLCDR.DAT"}
SKIP_SUFFIXES = (".IDX", ".OLD", ".IOL", ".SMM", ".LNK", ".BAD")
PROGRAM_SUFFIXES = (".EXE", ".OVL", ".PIF", ".MSG", ".NDX")
PROGRAM_DIRS = ("PARAM/SOFTWARE/",)


@dataclass
class SampleReport:
    since: str = ""
    kept: dict = field(default_factory=dict)      # rel path -> (records kept, records total) or bytes
    skipped: list = field(default_factory=list)
    bytes_in: int = 0
    bytes_out: int = 0


def _rel_files(root: Path):
    for dirpath, _, names in os.walk(root):
        for n in names:
            p = Path(dirpath) / n
            yield p, p.relative_to(root).as_posix()


def _data_end(root_files: dict) -> _dt.datetime | None:
    end = 0
    for rel in ("CDR/COIN.DAT", "CDR/COMMERR.DAT"):
        p = root_files.get(rel)
        if p is None:
            continue
        for rec in ctree.records(p.read_bytes()):
            end = max(end, u32(rec, 28))
    return minutes_to_datetime(end) if end else None


def _fixed_subset(data: bytes, keep) -> tuple[bytes, int, int]:
    h = ctree.read_header(data)
    first = -(-ctree.HEADER_SIZE // h.reclen)
    out = bytearray(data[:first * h.reclen])
    kept = total = 0
    for _, rec in ctree.fixed_records(data):
        total += 1
        if keep(rec):
            out += rec
            kept += 1
    return bytes(out), kept, total


def _mask_number(field_bytes: bytes) -> bytes:
    b = bytearray(field_bytes)
    digits = [i for i, c in enumerate(b) if 48 <= c <= 57]
    if len(digits) >= 7:
        for i in digits[-4:]:
            b[i] = ord("X")
    return bytes(b)


def _audit_subset(data: bytes, since_min: int, mask: bool, older: int) -> tuple[bytes, int, int]:
    """All audit batches since `since_min`, plus about `older` earlier ones spread evenly."""
    recs = [rec for _, rec in ctree.variable_records(data) if len(rec) >= 42]
    total = len(recs)
    old = [i for i, r in enumerate(recs) if u32(r, 28) < since_min]
    step = max(1, -(-len(old) // older)) if older else 0
    keep_old = set(old[::step]) if step else set()
    out = bytearray(data[:ctree.HEADER_SIZE])
    kept = 0
    for i, rec in enumerate(recs):
        if u32(rec, 28) < since_min and i not in keep_old:
            continue
        r = bytearray(rec)
        if mask:
            for k in range(r[41]):
                o = 42 + 47 * k
                if o + 17 <= len(r):
                    r[o:o + 17] = _mask_number(bytes(r[o:o + 17]))
        out += b"\xfa\xfa" + struct.pack("<II", len(r) + 10, len(r)) + r
        kept += 1
    return bytes(out), kept, total


def make_sample(src: str | os.PathLike, dest: str | os.PathLike, *, days: int | None = 365,
                older_audit: int = 5000, mask_numbers: bool = True, with_program: bool = False,
                with_billing: bool = False, with_logins: bool = False) -> SampleReport:
    """Write a sample of the XNET folder `src` to `dest` (a .zip file or a new folder)."""
    from .importer import find_xnet_root
    root = find_xnet_root(Path(src))
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists; choose a new name")
    files = {rel.upper(): (p, rel) for p, rel in _rel_files(root)}
    by_upper = {k: v[0] for k, v in files.items()}

    rep = SampleReport()
    since_min = 0
    if days is not None:
        end = _data_end(by_upper)
        if end:
            since = end - _dt.timedelta(days=days)
            since_min = datetime_to_minutes(since)
            rep.since = since.strftime("%Y-%m-%d")

    outputs: list[tuple[str, bytes]] = []
    for up, (path, rel) in sorted(files.items()):
        size = path.stat().st_size
        rep.bytes_in += size
        if up.endswith(SKIP_SUFFIXES):
            rep.skipped.append(f"{rel} (not read by coinroute)")
            continue
        if (up.endswith(PROGRAM_SUFFIXES) or up.startswith(PROGRAM_DIRS)) and not with_program:
            rep.skipped.append(f"{rel} (Protel program file; --with-program to include)")
            continue
        if up in PRIVATE_FILES and not with_logins:
            rep.skipped.append(f"{rel} (logins; --with-logins to include)")
            continue
        if up in BILLING_FILES and not with_billing:
            rep.skipped.append(f"{rel} (billing records may hold card numbers; --with-billing to include)")
            continue
        data = path.read_bytes()
        if up in FULL_HISTORY_FILES:
            new, kept, total = _fixed_subset(data, lambda r: True)
            rep.kept[rel] = (kept, total)
        elif up in WINDOW_FILES:
            new, kept, total = _fixed_subset(data, lambda r: u32(r, 28) >= since_min)
            rep.kept[rel] = (kept, total)
        elif up == AUDIT_FILE:
            new, kept, total = _audit_subset(data, since_min, mask_numbers, older_audit)
            rep.kept[rel] = (kept, total)
        else:
            new = data
            rep.kept[rel] = None
        outputs.append(("XNET/" + rel, new))

    notes = [
        "coinroute sample of an ExpressNet folder - for development only.",
        f"Coin polls and call accounting: all. Comm errors from: {rep.since or 'all'}.",
        f"Audit calls: all from {rep.since or 'the start'}, plus about {older_audit} older batches spread evenly.",
        f"Dialed numbers masked (last 4 digits): {'yes' if mask_numbers else 'no'}",
        "Index files are omitted, so ExpressNet itself cannot open this copy.",
        "Do not commit this to git.",
        "",
        "Left out:",
        *("  " + s for s in rep.skipped),
    ]
    outputs.append(("SAMPLE_README.txt", "\r\n".join(notes).encode()))

    if dest.suffix.lower() == ".zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for name, blob in outputs:
                z.writestr(name, blob)
        dest.write_bytes(buf.getvalue())
        rep.bytes_out = dest.stat().st_size
    else:
        for name, blob in outputs:
            p = dest / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(blob)
            rep.bytes_out += len(blob)
    return rep
