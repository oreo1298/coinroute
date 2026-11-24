"""Readers for the FairCom c-tree data files written by ExpressNet 3.

Fixed-length files
    A 128-byte header, then records of `reclen` bytes (u16 at header offset
    0x2C). The header occupies the first ceil(128 / reclen) record slots.
    A record whose first byte is 0xFF is deleted (the next bytes link the
    free list). Small files are pre-allocated, so all-zero slots are unused.

Variable-length files (bit 0x01000000 set in the u32 at header offset 0x28)
    After the header comes a chain of blocks. Each block starts with a
    2-byte mark -- FA FA = live record, FD FD = free space -- then a u32
    total block length (including this 10-byte block header) and a u32
    used length, then the record data. Damaged stretches are skipped by
    scanning forward to the next plausible block.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

HEADER_SIZE = 128
VARLEN_FLAG = 0x01000000
LIVE_MARK = b"\xfa\xfa"
FREE_MARK = b"\xfd\xfd"
BLOCK_HEADER = 10


@dataclass
class Header:
    reclen: int
    variable: bool
    raw: bytes


def read_header(data: bytes) -> Header:
    if len(data) < HEADER_SIZE:
        raise ValueError("file is shorter than a c-tree header")
    flags = struct.unpack_from("<I", data, 0x28)[0]
    reclen = struct.unpack_from("<H", data, 0x2C)[0]
    return Header(reclen=reclen, variable=bool(flags & VARLEN_FLAG), raw=data[:HEADER_SIZE])


@dataclass
class ReadStats:
    records: int = 0
    deleted: int = 0
    free_blocks: int = 0
    skipped_bytes: int = 0
    damaged_spots: list = field(default_factory=list)


def fixed_records(data: bytes, reclen: int | None = None, stats: ReadStats | None = None):
    """Yield (slot, record_bytes) for each live record of a fixed-length file."""
    header = read_header(data)
    rl = reclen or header.reclen
    if rl <= 0:
        raise ValueError("record length is zero")
    first = -(-HEADER_SIZE // rl)
    for slot in range(first, len(data) // rl):
        rec = data[slot * rl:(slot + 1) * rl]
        if rec[0] == 0xFF:
            if stats:
                stats.deleted += 1
            continue
        if not any(rec):
            continue
        if stats:
            stats.records += 1
        yield slot, rec


def _next_mark(data: bytes, start: int) -> int:
    a = data.find(LIVE_MARK, start)
    b = data.find(FREE_MARK, start)
    cands = [x for x in (a, b) if x >= 0]
    return min(cands) if cands else -1


def variable_records(data: bytes, stats: ReadStats | None = None):
    """Yield (offset, record_bytes) for each live record of a variable-length file."""
    n = len(data)
    p = HEADER_SIZE
    while p + BLOCK_HEADER <= n:
        mark = data[p:p + 2]
        if mark in (LIVE_MARK, FREE_MARK):
            total, used = struct.unpack_from("<II", data, p + 2)
            if BLOCK_HEADER <= total and p + total <= n and used <= total - BLOCK_HEADER:
                if mark == LIVE_MARK:
                    if stats:
                        stats.records += 1
                    yield p, data[p + BLOCK_HEADER:p + BLOCK_HEADER + used]
                elif stats:
                    stats.free_blocks += 1
                p += total
                continue
        q = _next_mark(data, p + 1)
        end = q if q >= 0 else n
        if stats:
            stats.skipped_bytes += end - p
            if end - p > 64:
                stats.damaged_spots.append((p, end - p))
        if q < 0:
            break
        p = q


def records(data: bytes, stats: ReadStats | None = None):
    """Yield record bytes from either kind of c-tree file."""
    header = read_header(data)
    gen = variable_records(data, stats) if header.variable else fixed_records(data, stats=stats)
    for _, rec in gen:
        yield rec
