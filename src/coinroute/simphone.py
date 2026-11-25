"""A simulated Protel payphone for testing the protocol without hardware.

It answers the commands in coinroute.protocol the way docs/PROTOCOL.md says a
phone does, with a 64 KB memory image behind 'D', 'X' and 'S'. Where the real
phone's behaviour is not known yet, the simulator makes a choice and says so:

- a refused or unknown command is answered with body 'E' (assumed);
- memory reads and writes are refused until the right security code ('Q');
- `greeting()` is the status frame a phone sends by itself after CONNECT, with
  the fields ExpressNet is known to check (phone number, type letters, clock);
  the other status bytes are zero.

Standard library only. `SimTransport` connects a `protocol.Link` straight to a
`SimPhone` in-process; `serve(fd, phone)` runs one over a pty or serial line.
"""
from __future__ import annotations

import os
import select
from collections import deque

from .protocol import ACK, FrameReader, frame, security_code_bytes, status_body

NAK = ord("E")  # assumed: what the phone answers to a refused command


class SimPhone:
    def __init__(self, security_code: str | int = "0000", memory: bytes | None = None,
                 phone: str = "5550100000", phone_type: str = "DA"):
        self.memory = bytearray(memory or bytes(0x10000))
        if len(self.memory) < 0x10000:
            self.memory += bytes(0x10000 - len(self.memory))
        self.security_code = security_code_bytes(security_code)
        self.phone = phone
        self.phone_type = phone_type
        self.unlocked = False
        self.clock: str | None = None
        self.reader = FrameReader()
        self.received: list[bytes] = []  # bodies of the commands it accepted or refused
        self.drop_replies = 0  # fault injection: ignore the next N commands
        self.corrupt_replies = 0  # fault injection: spoil the check byte of the next N replies

    def greeting(self) -> bytes:
        """The status frame sent right after CONNECT."""
        import time

        t = time.localtime()
        clock = time.strftime("%y%m%d", t) + str((t.tm_wday + 1) % 7 + 1) + time.strftime("%H%M%S", t)
        return frame(status_body(self.phone, self.phone_type, clock))

    # --- one command -> one reply ---

    def handle(self, body: bytes) -> bytes | None:
        """The reply frame for a command body, or None for no answer."""
        self.received.append(body)
        if self.drop_replies:
            self.drop_replies -= 1
            return None
        reply = self._answer(body)
        out = frame(reply)
        if self.corrupt_replies:
            self.corrupt_replies -= 1
            out = out[:-1] + bytes([out[-1] ^ 0xFF])
        return out

    def _answer(self, body: bytes) -> bytes:
        letter, args = chr(body[0]), body[1:]
        if letter == "Q" and len(args) == 2:
            self.unlocked = bytes(args) == self.security_code
            return bytes([ACK if self.unlocked else NAK])
        if letter == "P" and len(args) == 13 and args.isdigit():
            self.clock = args.decode("ascii")
            return bytes([ACK])
        if letter in "ILNORB" and not args:
            return bytes([ACK])
        if not self.unlocked:
            return bytes([NAK])
        if letter == "D" and len(args) == 3:  # reply: the command echoed, then the data (assumed echo)
            addr, n = args[0] << 8 | args[1], args[2]
            return bytes(body) + bytes(self.memory[addr:addr + n])
        if letter == "X" and len(args) == 4:
            addr, n = (args[0] << 16 | args[1] << 8 | args[2]) & 0xFFFF, args[3]
            return bytes(body) + bytes(self.memory[addr:addr + n])
        if letter == "S" and len(args) >= 3:
            addr = args[0] << 8 | args[1]
            self.memory[addr:addr + len(args) - 2] = args[2:]
            return bytes([ACK])
        if letter in "KT" and len(args) == 2:
            return bytes([ACK])
        return bytes([NAK])

    # --- byte stream side ---

    def feed(self, data: bytes) -> bytes:
        """Bytes from the PC in, reply bytes out (frames with a bad check byte are ignored)."""
        out = b""
        for f in self.reader.feed(data):
            if f.ok and f.body:
                r = self.handle(f.body)
                if r:
                    out += r
        return out


class SimTransport:
    """A protocol.Transport wired straight to a SimPhone (no threads, no timing)."""

    def __init__(self, phone: SimPhone, greet: bool = False):
        """`greet`: the call has just connected, so the phone's status frame is waiting to be read."""
        self.phone = phone
        self.pending: deque[int] = deque(phone.greeting() if greet else b"")

    def write(self, data: bytes) -> None:
        self.pending.extend(self.phone.feed(data))

    def read(self, n: int, timeout: float) -> bytes:
        out = bytes(self.pending.popleft() for _ in range(min(n, len(self.pending))))
        return out


def serve(fd: int, phone: SimPhone, stop_after: float | None = None) -> None:
    """Answer frames arriving on a file descriptor (pty or serial) until EOF or `stop_after` seconds idle."""
    while True:
        r, _, _ = select.select([fd], [], [], stop_after)
        if not r:
            return
        try:
            data = os.read(fd, 1024)
        except OSError:  # Linux reports a pty whose other end closed as EIO: the line hung up
            return
        if not data:
            return
        out = phone.feed(data)
        if out:
            os.write(fd, out)
