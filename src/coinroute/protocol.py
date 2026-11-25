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

STATUS_PHONE = slice(10, 20)   # the phone's own number, 10 ASCII digits
STATUS_TYPE = slice(27, 29)    # two letters, e.g. "DA", "DD", "CD"; "##" = not set
STATUS_CLOCK = slice(43, 56)   # yymmdd, weekday, hhmmss (13 ASCII digits); absent in short statuses
SHORT_STATUS = 0x25            # L of the short status older phones send (no clock)

# the type letter's second character -> the site phone types (1-5) ExpressNet accepts for it
TYPE_ACCEPTS = {"A": {1, 4, 5}, "D": {2, 3, 5}, "C": {1, 2, 3, 4}}


@dataclass
class Status:
    body: bytes

    @property
    def phone(self) -> str:
        return self.body[STATUS_PHONE].decode("latin-1")

    @property
    def phone_type(self) -> str:
        return self.body[STATUS_TYPE].decode("latin-1")

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
    if len(body) < STATUS_TYPE.stop:
        raise ProtocolError(f"status string not complete ({len(body)} bytes)")
    return Status(bytes(body))


def status_body(phone: str, phone_type: str = "DA", clock: str | None = None, flags: bytes = b"") -> bytes:
    """A status body with the known fields filled in (for the simulator and tests). Unknown bytes are 0."""
    b = bytearray(STATUS_CLOCK.stop if clock else 36)
    b[0] = ord("S")  # assumed: the first byte's meaning is not known
    b[1:1 + len(flags)] = flags
    b[STATUS_PHONE] = phone.encode("ascii")[:10].ljust(10, b"0")
    b[STATUS_TYPE] = phone_type.encode("ascii")[:2]
    if clock:
        b[STATUS_CLOCK] = clock.encode("ascii")
    return bytes(b)


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
        reply = self.command(cmd_read(addr >> 8, addr & 0xFF, count))
        return reply.body[1:]

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
