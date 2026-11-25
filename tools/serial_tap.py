"""Record what ExpressNet and the modem say to each other, byte for byte.

Sits between the PC side and the real UPMS1200 and copies bytes both ways,
logging each chunk with a timestamp and direction. Standard library only
(Linux / macOS).

    # ExpressNet in an emulator (DOSBox-X, 86Box …) whose COM port is a pty:
    python tools/serial_tap.py --pty --modem /dev/ttyUSB0 --baud 1200 --log poll.log
    #   -> prints the pty path; point the emulator's COM1 at it, then poll one phone.

    # Two real serial ports (an old PC on one, the modem on the other):
    python tools/serial_tap.py --pc /dev/ttyUSB1 --modem /dev/ttyUSB0 --baud 1200 --log poll.log

    # Afterwards: show the phone frames in a log (needs coinroute importable)
    python tools/serial_tap.py --decode poll.log

The log holds a real phone's traffic, including its security code: keep it out
of git (`*.log` is ignored) and share it only as you would the XNET folder.
"""
from __future__ import annotations

import argparse
import os
import re
import select
import sys
import termios
import time
import tty

BAUDS = {300: termios.B300, 1200: termios.B1200, 2400: termios.B2400, 9600: termios.B9600,
         19200: termios.B19200}


def open_port(path: str, baud: int) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    attrs[4] = attrs[5] = BAUDS[baud]
    attrs[2] |= termios.CLOCAL | termios.CREAD
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def fmt(data: bytes) -> str:
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{data.hex(' ')}  |{text}|"


def tap(pc: int, modem: int, log, quiet: bool = False) -> None:
    t0 = time.monotonic()
    names = {pc: "PC>MODEM", modem: "MODEM>PC"}
    other = {pc: modem, modem: pc}
    while True:
        r, _, _ = select.select([pc, modem], [], [])
        for fd in r:
            try:
                data = os.read(fd, 1024)
            except OSError:
                data = b""
            if not data:
                return
            os.write(other[fd], data)
            line = f"{time.monotonic() - t0:10.3f} {names[fd]} {fmt(data)}"
            log.write(line + "\n")
            log.flush()
            if not quiet:
                print(line)


def decode(path: str) -> None:
    """Print the phone-link frames found in a tap log, per direction, after the first CONNECT."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from coinroute.protocol import FrameReader

    readers = {"PC>MODEM": FrameReader(), "MODEM>PC": FrameReader()}
    connected = False
    rx = re.compile(r"\s*([\d.]+) (PC>MODEM|MODEM>PC) ((?:[0-9a-f]{2} ?)+)\s+\|")
    for line in open(path, encoding="latin-1"):
        m = rx.match(line)
        if not m:
            continue
        t, who, hexes = m.group(1), m.group(2), bytes.fromhex(m.group(3))
        if not connected:
            connected = who == "MODEM>PC" and b"CONNECT" in hexes
            continue
        for f in readers[who].feed(hexes):
            mark = "" if f.ok else "   (check byte wrong)"
            print(f"{t:>10} {who} {f.letter!r:4} {f.body.hex(' ')}{mark}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--modem", help="serial device of the UPMS1200")
    ap.add_argument("--pc", help="serial device the PC is on")
    ap.add_argument("--pty", action="store_true", help="make a pty for the PC side (an emulator's COM port)")
    ap.add_argument("--baud", type=int, default=1200, choices=sorted(BAUDS))
    ap.add_argument("--log", default="serial_tap.log")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--decode", metavar="LOG", help="show the frames in a log instead of tapping")
    a = ap.parse_args(argv)
    if a.decode:
        decode(a.decode)
        return 0
    if not a.modem or not (a.pc or a.pty):
        ap.error("give --modem and either --pc or --pty")
    modem = open_port(a.modem, a.baud)
    if a.pty:
        pc, slave = os.openpty()
        tty.setraw(slave)
        print(f"PC side: point the emulator's COM port at {os.ttyname(slave)}", flush=True)
    else:
        pc = open_port(a.pc, a.baud)
    with open(a.log, "a", encoding="ascii") as log:
        log.write(f"# serial_tap {time.strftime('%Y-%m-%d %H:%M:%S')} baud {a.baud}\n")
        try:
            tap(pc, modem, log, a.quiet)
        except KeyboardInterrupt:
            pass
    print(f"log: {a.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
