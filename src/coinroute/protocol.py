"""The PC-to-payphone protocol spoken after the modems connect, as far as it is known.

See docs/PROTOCOL.md for where each fact comes from and how sure it is. In
short: both sides send frames

    'T'  L  body...  check

where L is the number of bytes after it (the body plus the check byte) and the
check byte is the bitwise NOT of the 8-bit sum of every byte before it,
including 'T' and L. The first body byte of a command is a letter (D, S, Q …);
a reply whose first body byte is 'G' means the command was accepted.

Standard library only. The transport is anything with `write(bytes)` and
`read(n, timeout) -> bytes` (a serial port wrapper, a pty, or the simulated
phone in coinroute.simphone).
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

SYNC = ord("T")
ACK = ord("G")
RETRIES = 4  # ExpressNet gives each command four tries before it records a failure


class ProtocolError(Exception):
    """A command was not accepted after all retries (or the reply was malformed)."""


def checksum(data: bytes) -> int:
    """NOT of the 8-bit sum of `data` (ExpressNet appends this to every frame)."""
    return ~sum(data) & 0xFF


def frame(body: bytes) -> bytes:
    """Wrap a body (command letter + arguments, or a reply) in a frame."""
    if not 1 <= len(body) <= 254:
        raise ValueError("frame body must be 1-254 bytes")
    head = bytes([SYNC, len(body) + 1]) + body
    return head + bytes([checksum(head)])


@dataclass
class Frame:
    body: bytes
    ok: bool  # check byte matched

    @property
    def letter(self) -> str:
        return chr(self.body[0]) if self.body else ""

    @property
    def is_ack(self) -> bool:
        return bool(self.body) and self.body[0] == ACK


class FrameReader:
    """Collects frames from a byte stream: skips anything before 'T', waits for the full length."""

    def __init__(self):
        self.buf = bytearray()
        self.skipped = 0

    def feed(self, data: bytes) -> Iterator[Frame]:
        self.buf += data
        while True:
            start = self.buf.find(SYNC)
            if start < 0:
                self.skipped += len(self.buf)
                self.buf.clear()
                return
            if start:
                self.skipped += start
                del self.buf[:start]
            if len(self.buf) < 2:
                return
            length = self.buf[1]
            if length < 2:  # no room for a body and a check byte: not a frame start
                self.skipped += 1
                del self.buf[:1]
                continue
            total = 2 + length
            if len(self.buf) < total:
                return
            raw = bytes(self.buf[:total])
            del self.buf[:total]
            yield Frame(raw[2:-1], checksum(raw[:-1]) == raw[-1])


# --- commands -------------------------------------------------------------------------------
# Letters and argument layouts are the ones ExpressNet builds (docs/PROTOCOL.md has the
# evidence and what each is used for).


def cmd_read(addr_hi: int, addr_lo: int, count: int) -> bytes:
    """'D': read `count` bytes of phone memory at a two-byte address (sent high byte first)."""
    return frame(bytes([ord("D"), addr_hi, addr_lo, count]))


def cmd_read_ext(a0: int, a1: int, a2: int, count: int) -> bytes:
    """'X': read with a three-byte address (phones with more memory)."""
    return frame(bytes([ord("X"), a0, a1, a2, count]))


def cmd_write(addr_hi: int, addr_lo: int, data: bytes) -> bytes:
    """'S': write bytes to phone memory. ExpressNet sends 1 or 6 data bytes depending on phone type."""
    return frame(bytes([ord("S"), addr_hi, addr_lo]) + bytes(data))


def security_code_bytes(code: str | int) -> bytes:
    """A 4-digit security code as the phone receives it: packed BCD in reading order ("1234" -> 12 34).

    ExpressNet keeps the code in the site record as that 16-bit value and sends its low byte, then
    its high byte, which is the same two bytes (docs/PROTOCOL.md). An int is taken as its decimal
    digits, so 1234 and "1234" mean the same code.
    """
    digits = f"{code:04d}" if isinstance(code, int) else str(code).strip()
    if len(digits) != 4 or not digits.isdigit():
        raise ValueError(f"security code must be 4 digits, not {code!r}")
    return bytes.fromhex(digits)


def cmd_security(code: str | int) -> bytes:
    """'Q' + the security code (see security_code_bytes)."""
    return frame(b"Q" + security_code_bytes(code))


def cmd_set_clock(t: time.struct_time | None = None) -> bytes:
    """'P' + 13 digits. Field order is probable, not confirmed: yymmdd, weekday (1 = Sunday), hhmmss."""
    t = t or time.localtime()
    digits = time.strftime("%y%m%d", t) + str((t.tm_wday + 1) % 7 + 1) + time.strftime("%H%M%S", t)
    return frame(b"P" + digits.encode("ascii"))


def cmd_clear_audit(a: int, b: int) -> bytes:
    """'K': clear audit CDRs (two argument bytes; ExpressNet passes record numbers)."""
    return frame(bytes([ord("K"), a, b]))


def cmd_clear_billing(a: int, b: int) -> bytes:
    """'T': clear billing CDRs."""
    return frame(bytes([ord("T"), a, b]))


def cmd_simple(letter: str) -> bytes:
    """Commands without arguments: I, L, N, O, R, B."""
    if letter not in "ILNORB":
        raise ValueError(f"not an argument-less command: {letter!r}")
    return frame(letter.encode("ascii"))


# --- the phone's status ---------------------------------------------------------------------
# After CONNECT the phone sends one frame by itself. Offsets are into the body (after 'T' and L).
# Where each comes from, and how sure it is: docs/PROTOCOL.md, "The status frame".

# Offsets are in the newest layout; older phones' statuses are normalised to it first, as
# ExpressNet does (normalize_status).
STATUS_PHONE = slice(10, 20)   # the phone's own number, 10 ASCII digits
STATUS_TYPE = slice(27, 29)    # two letters, e.g. "DA", "DD", "CD"; "##" = not set
STATUS_MODEL = slice(29, 33)   # digits after the letters (firmware model, e.g. 7019)
STATUS_CLOCK = slice(43, 56)   # yymmdd, weekday, hhmmss (13 ASCII digits); absent in short statuses
SHORT_STATUS = 0x25            # L of the short status older phones send (no clock)
# older status formats, by first body byte: where ExpressNet inserts empty bytes, and how many
STATUS_FORMS = {ord("@"): (4, 5), ord("A"): (2, 7)}

# the type letter's second character -> the site phone types (1-5) ExpressNet accepts for it
TYPE_ACCEPTS = {"A": {1, 4, 5}, "D": {2, 3, 5}, "C": {1, 2, 3, 4}}


def normalize_status(body: bytes) -> bytes:
    """Bring an older status format to the newest layout by inserting empty bytes, as ExpressNet does."""
    at, n = STATUS_FORMS.get(body[0], (0, 0)) if body else (0, 0)
    return bytes(body[:at]) + bytes(n) + bytes(body[at:]) if n else bytes(body)


@dataclass
class Status:
    body: bytes  # normalised (newest layout)
    raw: bytes = b""  # as the phone sent it

    @property
    def phone(self) -> str:
        return self.body[STATUS_PHONE].decode("latin-1")

    @property
    def phone_type(self) -> str:
        return self.body[STATUS_TYPE].decode("latin-1")

    @property
    def form(self) -> str:
        """'@' or 'A' for the older formats; the newest format's first byte otherwise."""
        return chr((self.raw or self.body)[0])

    @property
    def model(self) -> int | None:
        """The number after the type letters (e.g. 7019 for a DD7019 phone), if present."""
        d = self.body[STATUS_MODEL]
        return int(d) if len(d) == 4 and d.isdigit() else None

    @property
    def clock(self) -> str | None:
        """The phone's clock as 'yymmddwhhmmss', or None when the status has none."""
        c = self.body[STATUS_CLOCK]
        return c.decode("ascii") if len(c) == 13 and c.isdigit() else None

    def matches(self, *numbers: str) -> bool:
        """Is this the phone we meant to call? (ExpressNet error 11 when not.)"""
        return any(self.phone == n[:10] for n in numbers if n)

    def type_ok(self, site_type: int) -> bool:
        """Does the phone's type agree with the site's type number? (ExpressNet error 12 when not.)"""
        return site_type in TYPE_ACCEPTS.get(self.phone_type[1:2], set())


def parse_status(body: bytes) -> Status:
    norm = normalize_status(body)
    if len(norm) < STATUS_TYPE.stop:
        raise ProtocolError(f"status string not complete ({len(body)} bytes)")
    return Status(norm, bytes(body))


def status_body(phone: str, phone_type: str = "DA", clock: str | None = None, flags: bytes = b"",
                model: int | None = 7019) -> bytes:
    """A newest-format status body with the known fields filled in (simulator, tests). Unknown bytes are 0."""
    b = bytearray(STATUS_CLOCK.stop if clock else 36)
    b[0] = ord("S")  # assumed: the newest format's first byte is not known (older ones use '@' / 'A')
    b[1:1 + len(flags)] = flags
    b[STATUS_PHONE] = phone.encode("ascii")[:10].ljust(10, b"0")
    b[STATUS_TYPE] = phone_type.encode("ascii")[:2]
    if model is not None:
        b[STATUS_MODEL] = f"{model:04d}".encode("ascii")
    if clock:
        b[STATUS_CLOCK] = clock.encode("ascii")
    return bytes(b)


# --- reading memory and call records ---------------------------------------------------------
# A read reply is not checked for 'G': its data follows an echo of the command (the letter and
# its argument bytes), so it starts at body byte 4 after 'D' and byte 5 after 'X'.
READ_DATA = {ord("D"): 4, ord("X"): 5}

CDR_COUNTS = 0x0FF8           # 8 bytes: billing (first, count) twice, then audit (first, count) twice
BILLING_RECORD, BILLING_SLOTS = 45, 52    # a ring of 52 records of 45 bytes
AUDIT_RECORD, AUDIT_SLOTS = 23, 125       # a ring of 125 records of 23 bytes


@dataclass
class CdrCounts:
    billing_first: int
    billing_count: int
    audit_first: int
    audit_count: int
    billing_ok: bool  # both copies agree and the numbers fit the ring
    audit_ok: bool


def parse_cdr_counts(data: bytes) -> CdrCounts:
    if len(data) < 8:
        raise ProtocolError(f"CDR counts need 8 bytes, got {len(data)}")
    b1, b2, a1, a2 = data[0:2], data[2:4], data[4:6], data[6:8]
    return CdrCounts(
        b1[0], b1[1], a1[0], a1[1],
        b1 == b2 and b1[0] <= BILLING_SLOTS and b1[1] <= BILLING_SLOTS,
        a1 == a2 and a1[0] <= AUDIT_SLOTS and a1[1] <= AUDIT_SLOTS,
    )


def billing_base(phone_type: str) -> int:
    return 0x2000 if phone_type == "CA" else 0x3000


def audit_base(phone_type: str, model: int | None) -> int:
    return 0x3E00 if phone_type == "CD" and model is not None and model < 4204 else 0x3A00


def ring_reads(base: int, size: int, slots: int, first: int, count: int, ok: bool = True) -> list[tuple[int, int]]:
    """(address, records) runs to read `count` records from slot `first` of a ring, wrapping at the end.

    When the counts can't be trusted (`ok` false) ExpressNet reads the whole ring from slot 0.
    """
    if not ok:
        first, count = 0, slots
    first, count = min(first, slots), min(count, slots)
    head = min(count, slots - first)
    runs = [(base + first * size, head)] if head else []
    if count > head:
        runs.append((base, count - head))
    return runs


# --- talking to a phone ---------------------------------------------------------------------


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...

    def read(self, n: int, timeout: float) -> bytes: ...


class Link:
    """Sends commands and waits for replies, with ExpressNet's retry rule."""

    def __init__(self, transport: Transport, timeout: float = 3.0, retries: int = RETRIES):
        self.t = transport
        self.timeout = timeout
        self.retries = retries
        self.reader = FrameReader()
        self.log: list[tuple[str, bytes]] = []  # ('tx'|'rx', bytes) for traces and tests

    def receive(self, timeout: float | None = None) -> Frame | None:
        """The next whole frame from the phone, or None on time-out."""
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            for f in self.reader.feed(b""):
                return f
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            data = self.t.read(256, left)
            if data:
                self.log.append(("rx", data))
                for f in self.reader.feed(data):
                    return f

    def command(self, packet: bytes, *, need_ack: bool = True) -> Frame:
        """Send a framed command; return the reply. Retries on time-out, bad check byte or no ACK."""
        last = "no reply"
        for _ in range(self.retries):
            self.t.write(packet)
            self.log.append(("tx", packet))
            reply = self.receive()
            if reply is None:
                last = "no reply"
                continue
            if not reply.ok:
                last = "reply failed its check byte"
                continue
            if need_ack and not reply.is_ack:
                last = f"phone answered {reply.body[:1]!r} instead of 'G'"
                continue
            return reply
        raise ProtocolError(f"command {chr(packet[2])!r} failed after {self.retries} tries: {last}")

    # convenience wrappers
    def read_memory(self, addr: int, count: int) -> bytes:
        """'D': `count` bytes at a 16-bit address."""
        reply = self.command(cmd_read(addr >> 8, addr & 0xFF, count), need_ack=False)
        return self._read_data(reply, ord("D"), count)

    def read_memory_ext(self, addr: int, count: int) -> bytes:
        """'X': `count` bytes at a 24-bit address (phones with more memory)."""
        reply = self.command(cmd_read_ext(addr >> 16, (addr >> 8) & 0xFF, addr & 0xFF, count),
                             need_ack=False)
        return self._read_data(reply, ord("X"), count)

    @staticmethod
    def _read_data(reply: Frame, letter: int, count: int) -> bytes:
        data = reply.body[READ_DATA[letter]:]
        if len(data) < count:
            raise ProtocolError(f"read refused or reply too short ({len(data)} of {count} bytes, "
                                f"reply {reply.body[:1]!r})")
        return data[:count]

    def cdr_counts(self) -> CdrCounts:
        return parse_cdr_counts(self.read_memory(CDR_COUNTS, 8))

    def write_memory(self, addr: int, data: bytes) -> None:
        self.command(cmd_write(addr >> 8, addr & 0xFF, data))

    def security(self, code: str | int) -> None:
        self.command(cmd_security(code))

    def receive_status(self, timeout: float | None = None) -> Status:
        """The status frame a phone sends by itself after CONNECT (ExpressNet's "Receive phone status")."""
        f = self.receive(timeout)
        if f is None:
            raise ProtocolError("no status from the phone (sync character not detected)")
        if not f.ok:
            raise ProtocolError("status string checksum bad")
        return parse_status(f.body)

    def set_clock(self, t: time.struct_time | None = None) -> None:
        self.command(cmd_set_clock(t))
