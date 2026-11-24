#!/usr/bin/env python3
"""
fake_upms.py - a stand-in for Protel's UPMS1200 payphone modem, for testing
ExpressNet (XNET.EXE) inside a VM without the real modem or a phone line.

What it does
  * Answers the Hayes AT commands ExpressNet sends (reset, init string, ATDT,
    ATA, +++ and ATH0) with the result codes ExpressNet looks for:
    OK, CONNECT 1200, BUSY, NO CARRIER, RING.
  * Raises and drops "carrier" on a real serial port's DTR/RTS pins, so through
    a null-modem cable the VM sees carrier detect come and go like a real modem.
  * Finds ExpressNet's serial speed by itself, the way a real modem autobauds on
    "AT" (ExpressNet can change speed per call: 300 or 1200 baud phones).
  * Logs every byte in both directions with timestamps, so we learn exactly what
    ExpressNet sends, including anything Protel-specific.
  * Lets you play the phone network from the keyboard: make the line ring,
    choose what the next dial does, drop the call, or type bytes to ExpressNet.

It does NOT speak the payphone protocol (yet). After CONNECT, ExpressNet talks
to a "phone" that never answers and should give up with an error such as
"Sync char not detected". That is expected.

Wiring (recommended): two serial ports on the Rehome host joined by a
FULL-HANDSHAKE null-modem cable (TX<->RX, DTR->DSR+DCD, RTS<->CTS both ways).
    port A -> the VM:         rehome set VM --serial COM1=/dev/ttyS0
    port B -> this script:    ./fake_upms.py --port /dev/ttyUSB0

Quick software-only check (no cable; carrier/DSR signals can't be simulated):
    ./fake_upms.py --pty              (prints a /dev/pts/N path)
    rehome set VM --serial COM1=/dev/pts/N

While it runs, type `help` for the keyboard commands (ring, drop, next busy...).

Python 3.8+, standard library only, Linux.
"""
import argparse
import codecs
import datetime
import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
import tty

VERSION = "1.0"

BAUDS = {300: termios.B300, 1200: termios.B1200, 2400: termios.B2400,
         4800: termios.B4800, 9600: termios.B9600, 19200: termios.B19200,
         38400: termios.B38400, 57600: termios.B57600, 115200: termios.B115200}
HUNT_ORDER = [9600, 1200, 300, 2400, 19200, 4800]
OUTCOMES = ("connect", "busy", "nocarrier", "noanswer", "nodialtone")

CR, LF, BS, DEL = 13, 10, 8, 127

# Hayes numeric result codes (ATV0)
CODES = {"OK": 0, "CONNECT": 1, "RING": 2, "NO CARRIER": 3, "ERROR": 4,
         "CONNECT 1200": 5, "NO DIALTONE": 6, "BUSY": 7, "NO ANSWER": 8,
         "CONNECT 2400": 10, "CONNECT 4800": 11, "CONNECT 9600": 12}

DEFAULT_S = {0: 0, 1: 0, 2: 43, 3: 13, 4: 10, 5: 8, 6: 2, 7: 50, 8: 2,
             9: 6, 10: 14, 11: 95, 12: 50}


# --------------------------------------------------------------------------- ports

class SerialPort:
    """A real serial port (/dev/ttyS0, /dev/ttyUSB0 ...)."""
    has_lines = True
    can_set_baud = True

    def __init__(self, path, baud, stopbits):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.saved = termios.tcgetattr(self.fd)
        self.stopbits = stopbits
        self.baud = None
        self.set_baud(baud)
        try:                                        # some adapters/virtual ports have no signal lines
            fcntl.ioctl(self.fd, termios.TIOCMGET, struct.pack("I", 0))
        except OSError:
            self.has_lines = False

    def set_baud(self, baud):
        a = termios.tcgetattr(self.fd)
        a[0] = 0                                    # iflag: no translation or flow control
        a[1] = 0                                    # oflag: raw output
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        if self.stopbits == 2:
            a[2] |= termios.CSTOPB
        a[3] = 0                                    # lflag: no echo, no line editing
        a[4] = a[5] = BAUDS[baud]
        a[6][termios.VMIN] = 0
        a[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, a)
        termios.tcflush(self.fd, termios.TCIFLUSH)
        self.baud = baud

    def read(self):
        try:
            return os.read(self.fd, 4096)
        except (BlockingIOError, InterruptedError):
            return b""

    def write(self, data):
        view = memoryview(data)
        while view:
            try:
                view = view[os.write(self.fd, view):]
            except BlockingIOError:
                select.select([], [self.fd], [], 0.5)

    def lines(self):
        v = struct.unpack("I", fcntl.ioctl(self.fd, termios.TIOCMGET, struct.pack("I", 0)))[0]
        return {"DSR": bool(v & termios.TIOCM_DSR), "CD": bool(v & termios.TIOCM_CAR),
                "CTS": bool(v & termios.TIOCM_CTS), "RI": bool(v & termios.TIOCM_RNG)}

    def set_line(self, name, on):
        bit = {"DTR": termios.TIOCM_DTR, "RTS": termios.TIOCM_RTS}[name]
        fcntl.ioctl(self.fd, termios.TIOCMBIS if on else termios.TIOCMBIC, struct.pack("I", bit))

    def close(self):
        try:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.saved)
        except termios.error:
            pass
        os.close(self.fd)


class PtyPort:
    """A virtual serial port. No modem control lines, no real speed."""
    has_lines = False
    can_set_baud = False

    def __init__(self, link=None):
        self.fd, self.slave_fd = pty.openpty()
        self.path = os.ttyname(self.slave_fd)
        tty.setraw(self.slave_fd)
        fl = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        fcntl.fcntl(self.fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        self.link = link
        if link:
            if os.path.islink(link):
                os.unlink(link)
            os.symlink(self.path, link)
        self.baud = None

    def set_baud(self, baud):
        pass

    def read(self):
        try:
            return os.read(self.fd, 4096)
        except (BlockingIOError, InterruptedError):
            return b""
        except OSError:                             # EIO: other side not open
            return b""

    def write(self, data):
        view = memoryview(data)
        while view:
            try:
                view = view[os.write(self.fd, view):]
            except BlockingIOError:
                select.select([], [self.fd], [], 0.5)

    def lines(self):
        return None

    def set_line(self, name, on):
        pass

    def close(self):
        if self.link and os.path.islink(self.link):
            os.unlink(self.link)
        os.close(self.fd)
        os.close(self.slave_fd)


# --------------------------------------------------------------------------- log

class Log:
    def __init__(self, path, console=True, clock=time.monotonic):
        self.path = path
        self.f = open(path, "a", buffering=1) if path else None
        self.console = console
        self.clock = clock
        self.t0 = clock()

    def _w(self, text):
        stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"{stamp} {self.clock() - self.t0:9.3f}  {text}"
        if self.f:
            self.f.write(line + "\n")
        if self.console:
            print(line, flush=True)

    def note(self, msg):
        self._w("     " + msg)

    def warn(self, msg):
        self._w(" !!  " + msg)

    def data(self, arrow, mode, data):
        txt = "".join(chr(c) if 32 <= c < 127 else
                      {13: "\\r", 10: "\\n"}.get(c, f"\\x{c:02x}") for c in data)
        hexs = data.hex(" ")
        if len(data) <= 24:
            self._w(f"{arrow} [{mode:<8}] {txt:<34} | {hexs}")
        else:
            self._w(f"{arrow} [{mode:<8}] {len(data)} bytes: {txt}")
            for i in range(0, len(data), 32):
                self._w(f"{'':6}{'':12}  {data[i:i + 32].hex(' ')}")

    def close(self):
        if self.f:
            self.f.close()


# --------------------------------------------------------------------------- modem

class Modem:
    def __init__(self, port, log, opts, clock=time.monotonic):
        self.port, self.log, self.o, self.now = port, log, opts, clock
        self.reset_settings()
        self.mode = "command"           # command | dialing | answering | online
        self.call_up = False            # a call exists (online, or escaped to command mode)
        self.carrier = None
        self.cmdbuf = bytearray()
        self.last_cmd = ""
        self.last_rx = -1e9
        self.plus = 0
        self.pending = None             # (when, outcome) for dialing/answering
        self.ringing = False
        self.rings = 0
        self.next_ring = 0.0
        self.next_auto_ring = clock() + (opts.ring_every or 0)
        self.next_dial = opts.dial      # one-shot override from the keyboard
        self.online_since = None
        self.auto = opts.baud == "auto" and port.can_set_baud
        self.locked = not self.auto
        self.garbage = 0
        self.garbage_t = 0.0
        self.prev_lines = None
        self.next_line_poll = 0.0
        self.rx = bytearray()
        self.rx_mode = "command"
        self.rx_t = 0.0
        self.unknown_seen = set()
        self.set_carrier(False, quiet=True)

    # -- settings ----------------------------------------------------------
    def reset_settings(self):
        self.r = {"E": 1 if self.o.echo == "on" else 0, "Q": 0, "V": 1, "X": 4,
                  "M": 1, "L": 2, "&C": 1, "&D": 2}
        self.S = dict(DEFAULT_S)

    # -- output ------------------------------------------------------------
    def send(self, data, what):
        self.flush_rx()
        self.port.write(data)
        self.log.data("->XNET", what, data)

    def result(self, name):
        if self.r["Q"]:
            self.log.note(f"(result {name} suppressed: ATQ1)")
            return
        if self.r["V"]:
            self.send(b"\r\n" + name.encode() + b"\r\n", "result")
        else:
            code = CODES.get(name, 1 if name.startswith("CONNECT") else 4)
            self.send(str(code).encode() + b"\r", "result")

    def info(self, text):
        if not self.r["Q"]:
            self.send(b"\r\n" + text.encode() + b"\r\n", "info")

    def connect_text(self):
        speed = self.o.connect_speed
        return "CONNECT" if speed == 300 or self.r["X"] == 0 else f"CONNECT {speed}"

    def set_carrier(self, on, quiet=False):
        if on == self.carrier:
            return
        self.carrier = on
        if not self.port.has_lines:
            if not quiet:
                self.log.note(f"carrier {'ON' if on else 'OFF'} (this port has no signal lines to show it)")
            return
        pin = self.o.carrier_pin.upper()
        if pin == "NONE":
            self.port.set_line("DTR", True)
            self.port.set_line("RTS", True)
            if not quiet:
                self.log.note(f"carrier {'ON' if on else 'OFF'} (--carrier-pin none: DTR/RTS stay high)")
            return
        other = "RTS" if pin == "DTR" else "DTR"
        self.port.set_line(other, True)
        self.port.set_line(pin, on)
        if not quiet:
            self.log.note(f"carrier {'ON' if on else 'OFF'}  (our {pin} {'high' if on else 'low'})")

    # -- receive -----------------------------------------------------------
    def feed(self, data):
        t = self.now()
        for b in data:
            self._byte(b, t)

    def _log_rx(self, b, t):
        if self.rx and (self.rx_mode != self.mode or t - self.rx_t > 0.1 or len(self.rx) >= 64):
            self.flush_rx()
        self.rx_mode = self.mode
        self.rx.append(b)
        self.rx_t = t

    def flush_rx(self):
        if self.rx:
            self.log.data("XNET->", self.rx_mode, bytes(self.rx))
            self.rx.clear()

    def _byte(self, b, t):
        gap = t - self.last_rx
        self.last_rx = t
        if self.mode == "online":
            self._log_rx(b, t)
            if b == self.S[2]:                      # '+' escape character
                if self.plus and gap < self.o.guard:
                    self.plus += 1
                else:
                    self.plus = 1 if gap >= self.o.guard else 0
                if self.plus > 3:
                    self.plus = 0
            else:
                self.plus = 0
            return
        if self.mode in ("dialing", "answering"):
            self._log_rx(b, t)
            self.flush_rx()
            self.log.note(f"character received while {self.mode}: call attempt aborted")
            self.pending = None
            self.mode = "command"
            self.result("NO CARRIER")
            return
        # command mode
        if self.auto and not (32 <= b < 127 or b in (CR, LF, BS, DEL)):
            self._log_rx(b, t)
            if t - self.garbage_t > 2:
                self.garbage = 0
            self.garbage += 1
            self.garbage_t = t
            if self.garbage >= 3:
                self.hunt()
            return
        self._log_rx(b, t)
        if self.r["E"]:
            self.port.write(bytes([b]))             # echo (not logged, to keep the log readable)
        if b == self.S[3]:
            self.flush_rx()
            self.line_done()
        elif b == LF:
            pass
        elif b in (self.S[5], DEL):
            if self.cmdbuf:
                self.cmdbuf.pop()
        else:
            self.cmdbuf.append(b)
            if bytes(self.cmdbuf[-2:]).upper() == b"A/":
                self.cmdbuf.clear()
                self.flush_rx()
                self.log.note(f"A/ repeats last command: AT{self.last_cmd}")
                self.run(self.last_cmd)
            elif len(self.cmdbuf) > 120:
                self.cmdbuf.clear()
                self.log.warn("command line too long; discarded")
                self.result("ERROR")

    def hunt(self):
        old = self.port.baud
        i = HUNT_ORDER.index(old) if old in HUNT_ORDER else -1
        new = HUNT_ORDER[(i + 1) % len(HUNT_ORDER)]
        self.flush_rx()
        self.port.set_baud(new)
        self.garbage = 0
        self.cmdbuf.clear()
        self.locked = False
        self.log.warn(f"unreadable bytes at {old} baud -> trying {new} baud")

    # -- command line ------------------------------------------------------
    def line_done(self):
        line = bytes(self.cmdbuf).decode("latin1")
        self.cmdbuf.clear()
        i = line.upper().find("AT")
        if i < 0:
            if line.strip():
                self.log.note(f"ignored (no AT prefix): {line!r}")
            return
        self.garbage = 0
        if not self.locked:
            self.locked = True
            self.log.note(f"speed locked at {self.port.baud} baud")
        body = line[i + 2:]
        self.last_cmd = body
        self.run(body)

    def run(self, s):
        i, n = 0, len(s)
        out, unknown = [], []

        def num():
            nonlocal i
            m = re.match(r"\d+", s[i:])
            if not m:
                return None
            i += m.end()
            return int(m.group())

        while i < n:
            c = s[i].upper()
            i += 1
            if c == " ":
                continue
            if c == "D":
                self.flush_unknown(unknown, "AT" + s)
                return self.dial(s[i:])
            if c == "A":
                self.flush_unknown(unknown, "AT" + s)
                return self.answer()
            if c == "O":
                num()
                self.flush_unknown(unknown, "AT" + s)
                if self.call_up:
                    self.mode = "online"
                    self.plus = 0
                    self.log.note("back online (ATO)")
                    return self.result(self.connect_text())
                return self.result("NO CARRIER")
            if c == "Z":
                num()
                self.reset_settings()
                self.hangup("ATZ")
                self.log.note("settings reset (ATZ)")
                continue
            if c == "H":
                if (num() or 0) == 0:
                    self.hangup("ATH")
                continue
            if c in "EQVXML":
                self.r[c] = num() or 0
                continue
            if c in "BCNPTWY":                      # accepted, no effect here
                num()
                continue
            if c == "I":
                v = num() or 0
                out.append({0: "1200", 3: "FAKE UPMS1200 (fake_upms.py)"}.get(v, f"FAKE UPMS1200 I{v}"))
                continue
            if c == "S":
                reg = num()
                if reg is None:
                    return self.result("ERROR")
                if i < n and s[i] == "=":
                    i += 1
                    self.S[reg] = (num() or 0) & 0xFF
                elif i < n and s[i] == "?":
                    i += 1
                    out.append(f"{self.S.get(reg, 0):03d}")
                continue
            if c == "&":
                c2 = s[i].upper() if i < n else ""
                i += 1
                v = num()
                if c2 == "F":
                    self.reset_settings()
                elif c2 in ("C", "D"):
                    self.r["&" + c2] = v or 0
                else:
                    unknown.append(f"&{c2}{'' if v is None else v}")
                continue
            if c in "+#":                           # extended commands: take the rest
                unknown.append(c + s[i:])
                break
            if c in "\\%*-$":
                c2 = s[i] if i < n else ""
                i += 1
                v = num()
                unknown.append(f"{c}{c2}{'' if v is None else v}")
                continue
            v = num()
            unknown.append(f"{c}{'' if v is None else v}")

        self.flush_unknown(unknown, "AT" + s)
        if unknown and self.o.strict:
            return self.result("ERROR")
        for o in out:
            self.info(o)
        self.result("OK")

    def flush_unknown(self, unknown, line):
        if unknown:
            self.log.warn(f"UNKNOWN COMMAND(S) {', '.join(unknown)}  in {line!r}"
                          f"  -> answering {'ERROR' if self.o.strict else 'OK'}")

    # -- calls -------------------------------------------------------------
    def dial(self, rest):
        number = "".join(ch for ch in rest if ch.isdigit() or ch in "*#")
        outcome, self.next_dial = self.next_dial, self.o.dial
        self.ringing = False
        if self.call_up:
            self.hangup("new dial")
        if rest.rstrip().endswith(";"):
            self.log.note(f"DIAL {number} (stay in command mode ';')")
            return self.result("OK")
        delay = {"connect": self.o.dial_delay, "busy": 2.0, "nodialtone": 1.0,
                 "nocarrier": min(self.S[7], self.o.fail_delay),
                 "noanswer": min(self.S[7], self.o.fail_delay)}[outcome]
        self.log.note(f"DIAL {number or '(no number)'}  -> will answer {outcome.upper()} in {delay:g}s")
        self.mode = "dialing"
        self.pending = (self.now() + delay, outcome)

    def answer(self):
        self.log.note(f"ANSWER (ATA){' after ' + str(self.rings) + ' ring(s)' if self.ringing else ''}")
        self.ringing = False
        if self.call_up:
            self.hangup("answer")
        self.mode = "answering"
        self.pending = (self.now() + self.o.answer_delay, "connect")

    def go_online(self):
        self.mode = "online"
        self.call_up = True
        self.plus = 0
        self.online_since = self.now()
        self.set_carrier(True)
        self.result(self.connect_text())
        self.log.note("ONLINE - everything ExpressNet sends now is meant for the payphone")

    def hangup(self, why):
        was = self.call_up or self.mode in ("dialing", "answering", "online")
        self.pending = None
        self.call_up = False
        self.online_since = None
        self.mode = "command"
        self.ringing = False
        self.set_carrier(False)
        if was:
            self.log.note(f"HUNG UP ({why})")

    def drop(self, why):
        """The far end goes away: carrier drops and NO CARRIER is reported."""
        if not (self.call_up or self.mode in ("dialing", "answering")):
            self.log.note("drop: there is no call")
            return
        self.flush_rx()
        self.hangup(why)
        self.result("NO CARRIER")

    def start_ring(self):
        if self.call_up or self.mode != "command":
            self.log.note("ring: line is busy with a call; hang up first")
            return
        self.ringing = True
        self.rings = 0
        self.next_ring = self.now()
        self.log.note("INCOMING CALL - ringing until ExpressNet answers (ATA)")

    # -- timers --------------------------------------------------------------
    def tick(self):
        t = self.now()
        if self.rx and t - self.rx_t > 0.1:
            self.flush_rx()

        if self.pending and t >= self.pending[0]:
            outcome = self.pending[1]
            self.pending = None
            self.mode = "command"
            if outcome == "connect":
                self.go_online()
            else:
                self.result({"busy": "BUSY", "nocarrier": "NO CARRIER",
                             "noanswer": "NO ANSWER", "nodialtone": "NO DIALTONE"}[outcome])

        if self.mode == "online" and self.plus == 3 and t - self.last_rx >= self.o.guard:
            self.plus = 0
            self.flush_rx()
            self.mode = "command"
            self.log.note("+++ escape: command mode, call still up (ATH0 hangs up, ATO resumes)")
            self.result("OK")

        if (self.mode == "command" and not self.o.no_plus_ok and self.cmdbuf
                and bytes(self.cmdbuf).endswith(b"+++") and b"AT" not in bytes(self.cmdbuf).upper()
                and t - self.last_rx >= self.o.guard):
            self.cmdbuf.clear()
            self.flush_rx()
            self.log.note("+++ while already in command mode -> OK")
            self.result("OK")

        if (self.mode == "online" and self.o.hangup_after and self.online_since
                and t - self.online_since >= self.o.hangup_after):
            self.drop(f"far end hung up after {self.o.hangup_after:g}s (--hangup-after)")

        if self.ringing and t >= self.next_ring:
            if self.mode != "command" or self.call_up:
                self.ringing = False
            else:
                self.rings += 1
                self.S[1] = self.rings
                self.result("RING")
                if self.S[0] and self.rings >= self.S[0]:
                    self.answer()
                elif self.rings >= self.o.max_rings:
                    self.ringing = False
                    self.log.note(f"caller gave up after {self.rings} rings (not answered)")
                else:
                    self.next_ring = t + 6.0

        if (self.o.ring_every and t >= self.next_auto_ring):
            self.next_auto_ring = t + self.o.ring_every
            if self.mode == "command" and not self.call_up and not self.ringing:
                self.start_ring()

        if self.port.has_lines and t >= self.next_line_poll:
            self.next_line_poll = t + 0.05
            self.poll_lines()

    def poll_lines(self):
        cur = self.port.lines()
        if cur is None:
            return
        prev, self.prev_lines = self.prev_lines, cur
        if prev is None:
            self.log.note("signals from ExpressNet's side: " + "  ".join(
                f"{k}={'on' if v else 'off'}" for k, v in cur.items()))
            return
        changed = [k for k in cur if cur[k] != prev[k]]
        if not changed:
            return
        self.log.note("signal change: " + "  ".join(f"{k} {'on' if cur[k] else 'off'}" for k in changed))
        pin = {"dsr": "DSR", "cd": "CD", "cts": "CTS"}.get(self.o.dte_ready_pin)
        if pin in changed and prev[pin] and not cur[pin]:
            d = self.r["&D"]
            if d == 1 and self.mode == "online":
                self.mode = "command"
                self.log.note("ExpressNet dropped DTR (&D1): command mode, call still up")
                self.result("OK")
            elif d == 2:
                self.hangup("ExpressNet dropped DTR (&D2)")
            elif d == 3:
                self.hangup("ExpressNet dropped DTR (&D3)")
                self.reset_settings()

    def status(self):
        lines = self.port.lines() if self.port.has_lines else None
        return (f"mode={self.mode} call={'up' if self.call_up else 'none'} "
                f"carrier={'on' if self.carrier else 'off'} "
                f"speed={self.port.baud or 'n/a'}{'' if self.locked else ' (hunting)'} "
                f"next dial={self.next_dial} ringing={self.ringing} "
                f"E{self.r['E']} Q{self.r['Q']} V{self.r['V']} X{self.r['X']} "
                f"&C{self.r['&C']} &D{self.r['&D']} S0={self.S[0]} S7={self.S[7]}"
                + (" | lines: " + " ".join(f"{k}={'on' if v else 'off'}" for k, v in lines.items())
                   if lines else ""))


# --------------------------------------------------------------------------- keyboard

HELP = """keyboard commands:
  ring              incoming call: send RING until ExpressNet answers (ATA)
  drop              the far end hangs up: carrier off + NO CARRIER
  next <outcome>    what the NEXT dial does: connect busy nocarrier noanswer nodialtone
  always <outcome>  what EVERY dial does from now on
  say <text>        send text to ExpressNet (\\r \\n \\xHH escapes work)
  hex <hh hh ...>   send raw bytes to ExpressNet
  status            show modem state and signal lines
  help | quit"""


def keyboard(modem, line):
    parts = line.strip().split(None, 1)
    if not parts:
        return True
    cmd, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    if cmd in ("quit", "exit", "q"):
        return False
    if cmd == "help":
        print(HELP, flush=True)
    elif cmd == "ring":
        modem.start_ring()
    elif cmd == "drop":
        modem.drop("dropped from the keyboard")
    elif cmd in ("next", "always"):
        if arg not in OUTCOMES:
            print(f"outcome must be one of: {' '.join(OUTCOMES)}", flush=True)
        else:
            modem.next_dial = arg
            if cmd == "always":
                modem.o.dial = arg
            modem.log.note(f"{'next' if cmd == 'next' else 'every'} dial -> {arg.upper()}")
    elif cmd == "say":
        modem.send(codecs.decode(arg, "unicode_escape").encode("latin1"), "typed")
    elif cmd == "hex":
        try:
            modem.send(bytes.fromhex(arg), "typed")
        except ValueError:
            print("hex: give bytes like  02 54 31 03", flush=True)
    elif cmd == "status":
        print(modem.status(), flush=True)
    else:
        print(f"unknown command {cmd!r}; type help", flush=True)
    return True


# --------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Fake Protel UPMS1200 modem for testing ExpressNet without the real modem.",
        epilog="Type 'help' while it runs for keyboard commands.")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--port", metavar="DEV",
                       help="serial port null-modem-wired to the VM's port, e.g. /dev/ttyUSB0")
    where.add_argument("--pty", action="store_true",
                       help="create a virtual port instead (no carrier/DSR signals)")
    ap.add_argument("--pty-link", metavar="PATH", help="also make a symlink to the virtual port")
    ap.add_argument("--baud", default="auto",
                    help="auto (default: detect from AT) or a fixed speed such as 1200 or 9600")
    ap.add_argument("--stopbits", type=int, choices=(1, 2), default=1)
    ap.add_argument("--dial", choices=OUTCOMES, default="connect", help="what every dial does (default connect)")
    ap.add_argument("--connect-speed", type=int, default=1200, help="speed in CONNECT messages (default 1200)")
    ap.add_argument("--dial-delay", type=float, default=3.0, help="seconds from ATDT to CONNECT (default 3)")
    ap.add_argument("--answer-delay", type=float, default=2.0, help="seconds from ATA to CONNECT (default 2)")
    ap.add_argument("--fail-delay", type=float, default=8.0,
                    help="seconds before NO CARRIER / NO ANSWER (capped by S7; default 8)")
    ap.add_argument("--guard", type=float, default=0.5, help="+++ guard time in seconds (default 0.5)")
    ap.add_argument("--hangup-after", type=float, metavar="SEC", help="far end hangs up SEC seconds after CONNECT")
    ap.add_argument("--ring-every", type=float, metavar="SEC", help="simulate an incoming call every SEC seconds")
    ap.add_argument("--max-rings", type=int, default=10, help="rings before an unanswered caller gives up")
    ap.add_argument("--carrier-pin", choices=("dtr", "rts", "none"), default="dtr",
                    help="our output that shows carrier to ExpressNet (default dtr = their DCD+DSR "
                         "on a standard null-modem cable; none = always on)")
    ap.add_argument("--dte-ready-pin", choices=("dsr", "cd", "cts", "none"), default="dsr",
                    help="our input that carries ExpressNet's DTR (default dsr)")
    ap.add_argument("--echo", choices=("on", "off"), default="on", help="command echo after reset (default on)")
    ap.add_argument("--strict", action="store_true",
                    help="answer ERROR to commands this fake doesn't know (default OK)")
    ap.add_argument("--no-plus-ok", action="store_true",
                    help="don't answer OK to +++ sent while already in command mode")
    ap.add_argument("--log", metavar="FILE", help="log file (default fake_upms_YYYYmmdd_HHMMSS.log)")
    ap.add_argument("--quiet", action="store_true", help="log to the file only, not the screen")
    ap.add_argument("--version", action="version", version=f"fake_upms {VERSION}")
    o = ap.parse_args(argv)

    if o.baud != "auto":
        try:
            o.baud = int(o.baud)
            BAUDS[o.baud]
        except (ValueError, KeyError):
            ap.error(f"--baud must be auto or one of {sorted(BAUDS)}")
    log_path = o.log or datetime.datetime.now().strftime("fake_upms_%Y%m%d_%H%M%S.log")
    log = Log(log_path, console=not o.quiet)

    try:
        if o.pty:
            port = PtyPort(o.pty_link)
        else:
            port = SerialPort(o.port, 9600 if o.baud == "auto" else o.baud, o.stopbits)
    except OSError as e:
        hint = ""
        if e.errno == 13:
            hint = " (add your user to the dialout group - uucp on Arch - and log in again)"
        sys.exit(f"fake_upms: cannot open {o.port}: {e.strerror}{hint}")

    log.note(f"fake_upms {VERSION} - fake Protel UPMS1200 - log: {os.path.abspath(log_path)}")
    if o.pty:
        log.note(f"virtual port: {port.path}" + (f"  (link {o.pty_link})" if o.pty_link else ""))
        log.note(f"give it to the VM:  rehome set VM --serial COM1={port.path}")
        log.note("pty mode cannot show carrier/DSR to ExpressNet; use two real ports for the full test")
    else:
        log.note(f"port {o.port}, speed {'auto (starting at 9600)' if o.baud == 'auto' else o.baud}, "
                 f"carrier on {o.carrier_pin.upper()}, ExpressNet's DTR read from {o.dte_ready_pin.upper()}")
        if not port.has_lines:
            log.warn(f"{o.port} has no modem signal lines (DTR/RTS/DSR/CD): carrier can't be shown "
                     "to ExpressNet. Use a real UART or an FTDI/PL2303-type adapter for the full test.")
    log.note(f"every dial answers {o.dial.upper()}; type 'help' for keyboard commands")

    modem = Modem(port, log, o)
    running = [True]

    def stop(*_):
        running[0] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)

    use_stdin = sys.stdin is not None and not sys.stdin.closed
    try:
        while running[0]:
            watch = [port.fd] + ([sys.stdin] if use_stdin else [])
            try:
                r, _, _ = select.select(watch, [], [], 0.02)
            except InterruptedError:
                r = []
            if use_stdin and sys.stdin in r:              # keyboard first, so "next busy" wins a race
                line = sys.stdin.readline()
                if not line:
                    use_stdin = False
                elif not keyboard(modem, line):
                    break
            if port.fd in r:
                data = port.read()
                if data:
                    modem.feed(data)
            modem.tick()
    except KeyboardInterrupt:
        pass
    finally:
        modem.flush_rx()
        modem.set_carrier(False, quiet=True)
        log.note("stopped")
        port.close()
        log.close()
        print(f"log saved: {os.path.abspath(log_path)}", file=sys.stderr)


if __name__ == "__main__":
    main()
