"""coinroute.protocol and the simulated payphone (coinroute.simphone)."""
import os
import select
import threading
import time

import pytest

from coinroute import protocol as p
from coinroute.simphone import SimPhone, SimTransport, serve


def test_frame_layout_and_check_byte():
    f = p.frame(b"I")
    assert f[:3] == b"T\x02I" and f[3] == (~(0x54 + 0x02 + 0x49)) & 0xFF
    assert p.checksum(f[:-1]) == f[-1]


@pytest.mark.parametrize("packet,length", [
    (p.cmd_read(0x0F, 0xF8, 8), 5),                    # ExpressNet: T%cD%c%c%c with 5
    (p.cmd_read_ext(0x0F, 0xF8, 0x00, 8), 6),          # T%cX%c%c%c%c with 6
    (p.cmd_clear_audit(1, 2), 4),                      # T%cK%c%c with 4
    (p.cmd_security(1234), 4),                         # T%cQ%c%c with 4
    (p.cmd_write(0x00, 0x20, bytes(6)), 10),           # T%cS + 8 bytes with 10
    (p.cmd_write(0x00, 0x20, bytes(1)), 5),            # T%cS%c%c%c with 5
    (p.cmd_set_clock(time.localtime(0)), 15),          # T%cP%s with 15
    (p.cmd_simple("O"), 2),                            # T%cO with 2
])
def test_length_byte_matches_expressnet(packet, length):
    assert packet[0] == ord("T") and packet[1] == length == len(packet) - 2


def test_security_code_is_little_endian():
    assert p.cmd_security(0x1234)[2:5] == b"Q\x34\x12"


def test_clock_digits():
    t = time.strptime("2026-05-02 01:05:09", "%Y-%m-%d %H:%M:%S")   # a Saturday
    assert p.cmd_set_clock(t)[3:16] == b"2605027010509"


def test_reader_resyncs_and_flags_bad_frames():
    r = p.FrameReader()
    good = p.frame(b"G123")
    bad = good[:-1] + bytes([good[-1] ^ 1])
    frames = list(r.feed(b"\x00noise" + good[:3]))
    assert frames == [] and r.skipped == 6
    frames = list(r.feed(good[3:] + bad + b"T\x00" + p.frame(b"G")))
    assert [(f.body, f.ok) for f in frames] == [(b"G123", True), (b"G123", False), (b"G", True)]


def test_link_with_simulated_phone():
    phone = SimPhone(security_code=4821)
    phone.memory[0x0FF8:0x1000] = bytes(range(8))
    link = p.Link(SimTransport(phone), timeout=0.05)
    with pytest.raises(p.ProtocolError, match="instead of 'G'"):
        link.read_memory(0x0FF8, 8)              # locked until the security code
    link.security(4821)
    assert link.read_memory(0x0FF8, 8) == bytes(range(8))
    link.write_memory(0x0020, b"\x00" * 6)
    assert phone.memory[0x20:0x26] == bytes(6)
    link.set_clock(time.strptime("2026-05-02 01:05:09", "%Y-%m-%d %H:%M:%S"))
    assert phone.clock == "2605027010509"
    with pytest.raises(p.ProtocolError):
        link.security(1111)


def test_link_retries_like_expressnet():
    phone = SimPhone(security_code=0)
    link = p.Link(SimTransport(phone), timeout=0.01)
    phone.drop_replies = 3
    link.command(p.cmd_simple("I"))              # the 4th try gets through
    assert sum(1 for d, _ in link.log if d == "tx") == 4
    phone.corrupt_replies = 1
    link.command(p.cmd_simple("I"))              # a spoiled check byte is retried
    phone.drop_replies = 4
    with pytest.raises(p.ProtocolError, match="after 4 tries: no reply"):
        link.command(p.cmd_simple("I"))


class FdTransport:
    def __init__(self, fd):
        self.fd = fd

    def write(self, data):
        os.write(self.fd, data)

    def read(self, n, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        return os.read(self.fd, n) if r else b""


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs a pty")
def test_simulated_phone_over_a_pty():
    import tty
    master, slave = os.openpty()
    tty.setraw(slave)
    phone = SimPhone(security_code=7)
    t = threading.Thread(target=serve, args=(master, phone, 1.0), daemon=True)
    t.start()
    link = p.Link(FdTransport(slave), timeout=1.0)
    link.security(7)
    phone.memory[0x100] = 0x42
    assert link.read_memory(0x100, 1) == b"\x42"
    os.close(slave)
    t.join(3)
    os.close(master)
