"""Driving the Protel UPMS1200 (or a Hayes-compatible modem) for polling.

Standard library only. Works over any transport with `write(bytes)` and
`read(n, timeout) -> bytes`; time passes through `sleep`, so tests can run
against tools/fake_upms.py on a virtual clock.

    m = Modem(transport)
    m.reset()
    r = m.dial("5550123")
    if r.connected:
        link = m.link()            # coinroute.protocol.Link on the same line
        ...
    m.hangup()

Result codes map to ExpressNet's communication errors (xnet.COMM_ERRORS), so a
failed call can be stored the same way ExpressNet stored it.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .protocol import Link

RESULTS = ("OK", "CONNECT", "RING", "NO CARRIER", "ERROR", "NO DIALTONE", "BUSY", "NO ANSWER")


@dataclass(frozen=True)
class Profile:
    name: str
    init: str
    answer: str = "ATA"
    dial: str = "ATDT"
    reset: str = "ATZ"


# ExpressNet's stored profiles (PARAM/MODEMS.DAT); the generic one runs at 300 bps.
UPMS1200 = Profile("UPMS1200", "ATM1L0E0Q0V1X3S10=255S0=0S9=2S7=100&D2")
GENERIC = Profile("GENERIC", "ATM1L2E0Q0V1X3S10=255S0=0S9=2S7=100")


@dataclass
class CallResult:
    connected: bool
    result: str  # the modem's words, e.g. "CONNECT 1200", "BUSY", or "" on time-out
    error_code: int = 0  # index into xnet.COMM_ERRORS, 0 when connected

    @property
    def speed(self) -> int | None:
        parts = self.result.split()
        return int(parts[1]) if self.connected and len(parts) > 1 and parts[1].isdigit() else None


# how a failed call is recorded (ExpressNet's own codes)
FAILURE_CODES = {
    "BUSY": 5,          # BUSY detected
    "NO CARRIER": 6,    # No carrier detected
    "NO ANSWER": 4,     # Time-out on carrier detect
    "": 4,              # no result before the time-out
    "NO DIALTONE": 80,  # Communication fails
    "ERROR": 80,
}


class ModemError(Exception):
    pass


class Modem:
    def __init__(self, transport, profile: Profile = UPMS1200, guard: float = 1.1,
                 sleep: Callable[[float], None] | None = None):
        self.t = transport
        self.profile = profile
        self.guard = guard
        self.sleep = sleep or getattr(transport, "sleep", None) or time.sleep
        self.buf = bytearray()
        self.online = False
        self.log: list[tuple[str, bytes]] = []

    # --- lines ---

    def _write(self, data: bytes) -> None:
        self.t.write(data)
        self.log.append(("tx", data))

    def _clock(self) -> float:
        return getattr(self.t, "monotonic", time.monotonic)()

    def _take_line(self) -> str | None:
        """The next CR/LF-terminated line already in the buffer ('' for an empty one), or None."""
        ends = [i for i in (self.buf.find(b"\r"), self.buf.find(b"\n")) if i >= 0]
        if not ends:
            return None
        i = min(ends)
        line = bytes(self.buf[:i]).decode("latin-1").strip()
        del self.buf[:i + 1]
        return line

    def _read_line(self, timeout: float) -> str | None:
        deadline = self._clock() + timeout
        while True:
            line = self._take_line()
            if line:
                return line
            if line is not None:  # an empty line: keep going
                continue
            left = deadline - self._clock()
            if left <= 0:
                return None
            data = self.t.read(256, min(left, 0.5))
            if data:
                self.log.append(("rx", data))
                self.buf += data

    def _wait_result(self, timeout: float, sent: str = "") -> str:
        deadline = self._clock() + timeout
        while True:
            left = deadline - self._clock()
            if left <= 0:
                return ""
            line = self._read_line(left)
            if line is None:
                return ""
            if sent and line == sent:  # command echo
                continue
            if any(line == r or line.startswith(r + " ") for r in RESULTS):
                return line

    def at(self, cmd: str, timeout: float = 3.0) -> str:
        """Send a command in command mode and return its result; raise unless it is OK."""
        self.buf.clear()
        self._write(cmd.encode("ascii") + b"\r")
        res = self._wait_result(timeout, cmd)
        if res != "OK":
            raise ModemError(f"{cmd}: {res or 'no answer from the modem'}")
        return res

    # --- calls ---

    def reset(self) -> None:
        """Hang up if needed, reset, and load the profile's init string."""
        if self.online:
            self.hangup()
        self.at(self.profile.reset)
        self.at(self.profile.init)

    def dial(self, number: str, timeout: float = 60.0) -> CallResult:
        cmd = self.profile.dial + number
        self.buf.clear()
        self._write(cmd.encode("ascii") + b"\r")
        res = self._wait_result(timeout, cmd)
        return self._call_result(res)

    def answer(self, timeout: float = 30.0) -> CallResult:
        self.buf.clear()
        self._write(self.profile.answer.encode("ascii") + b"\r")
        return self._call_result(self._wait_result(timeout, self.profile.answer))

    def wait_for_ring(self, timeout: float) -> bool:
        return self._wait_result(timeout) == "RING"

    def _call_result(self, res: str) -> CallResult:
        if res.startswith("CONNECT"):
            self.online = True
            return CallResult(True, res)
        return CallResult(False, res, FAILURE_CODES.get(res, 7 if res else 4))

    def hangup(self) -> None:
        """Escape to command mode (+++ between guard times) and hang up with ATH0."""
        if self.online:
            self.sleep(self.guard)
            self._write(b"+++")
            self.sleep(self.guard)
            self._wait_result(2.0)  # OK (or nothing, if the line already dropped)
        self.online = False
        try:
            self.at("ATH0")
        except ModemError:
            pass

    def link(self, timeout: float = 3.0) -> Link:
        """A protocol Link over the connected line, starting with any bytes already received."""
        return Link(_DataTransport(self), timeout=timeout)


class _DataTransport:
    """The modem's line in data mode: bytes the line reader already took come first."""

    def __init__(self, modem: Modem):
        self.m = modem

    def write(self, data: bytes) -> None:
        self.m._write(data)

    def read(self, n: int, timeout: float) -> bytes:
        if self.m.buf:
            out = bytes(self.m.buf[:n])
            del self.m.buf[:n]
            return out
        data = self.m.t.read(n, timeout)
        if data:
            self.m.log.append(("rx", data))
        return data


class SerialLine:
    """A serial port (or pty) as a transport: raw mode, fixed speed, select-based reads. POSIX only."""

    BAUDS = {300: "B300", 1200: "B1200", 2400: "B2400", 9600: "B9600", 19200: "B19200"}

    def __init__(self, path: str, baud: int = 1200):
        import os
        import termios
        import tty

        self._os = os
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
        tty.setraw(self.fd)
        attrs = termios.tcgetattr(self.fd)
        speed = getattr(termios, self.BAUDS[baud])
        attrs[4] = attrs[5] = speed
        attrs[2] |= termios.CLOCAL | termios.CREAD
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[self._os.write(self.fd, view):]

    def read(self, n: int, timeout: float) -> bytes:
        import select

        r, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
        if not r:
            return b""
        try:
            return self._os.read(self.fd, n)
        except OSError:
            return b""

    def close(self) -> None:
        self._os.close(self.fd)
