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


def test_security_code_is_packed_bcd_in_reading_order():
    # ExpressNet stores "1234" in the site record as bytes 12 34 and sends them in that order
    assert p.cmd_security("1234")[2:5] == b"Q\x12\x34"
    assert p.cmd_security(1234) == p.cmd_security("1234") and p.cmd_security(7)[3:5] == b"\x00\x07"
    for bad in ("123", "12a4", 12345):
        with pytest.raises(ValueError):
            p.security_code_bytes(bad)


def test_status_fields():
    body = p.status_body("5550100123", "DD", "2605027010509")
    st = p.parse_status(body)
    assert (st.phone, st.phone_type, st.clock) == ("5550100123", "DD", "2605027010509")
    assert st.matches("", "5550100123") and not st.matches("5550100999")
    assert st.type_ok(2) and st.type_ok(5) and not st.type_ok(1)
    # the short status of older phones (L = 0x25) has no clock
    short = p.frame(p.status_body("5550100123", "DA"))
    assert short[1] == p.SHORT_STATUS
    st = next(p.FrameReader().feed(short))
    assert p.parse_status(st.body).clock is None and p.parse_status(st.body).type_ok(4)
    with pytest.raises(p.ProtocolError):
        p.parse_status(b"S" * 20)


def test_link_receives_the_status_a_phone_sends_first():
    phone = SimPhone(security_code="4821", phone="5550100123", phone_type="DD")
    link = p.Link(SimTransport(phone, greet=True), timeout=0.1)
    st = link.receive_status()
    assert st.phone == "5550100123" and st.type_ok(3) and st.clock and len(st.clock) == 13
    link.security("4821")
    assert phone.unlocked


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
    with pytest.raises(p.ProtocolError, match="read refused"):
        link.read_memory(0x0FF8, 8)              # locked until the security code
    link.security(4821)
    assert link.read_memory(0x0FF8, 8) == bytes(range(8))
    phone.flags[1] = 0x20 | 0x80  # AE Call Acct Err, CF Bill CDR Full
    link.clear_flags(*p.CLEAR_CALL_ACCOUNTING)
    assert phone.flags[1] == 0x80
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


def test_older_status_formats_are_normalised_like_expressnet():
    new = p.status_body("5550100123", "DD", "2605027010509")
    st = p.parse_status(new)
    assert st.form == "S" and st.model == 7019 and st.phone == "5550100123"
    # an 'A' status lacks body bytes 2-8 of the newest layout; '@' lacks 4-8
    for letter, at, n in (("A", 2, 7), ("@", 4, 5)):
        old = bytes([ord(letter)]) + new[1:at] + new[at + n:]
        st = p.parse_status(old)
        assert st.form == letter and st.raw == old
        assert (st.phone, st.phone_type, st.model) == ("5550100123", "DD", 7019)


def test_read_replies_echo_the_command():
    phone = SimPhone(security_code="4821")
    phone.memory[0x2000:0x2004] = b"\x01\x02\x03\x04"
    link = p.Link(SimTransport(phone), timeout=0.05)
    link.security("4821")
    assert link.read_memory(0x2000, 4) == b"\x01\x02\x03\x04"
    assert link.read_memory_ext(0x002000, 2) == b"\x01\x02"
    reply = list(p.FrameReader().feed(phone.handle(b"D\x20\x00\x02")))[0]
    assert reply.body == b"D\x20\x00\x02\x01\x02"  # data after the echo (body byte 4)


def test_cdr_counts_and_ring_reads():
    c = p.parse_cdr_counts(bytes([50, 5, 50, 5, 120, 10, 120, 10]))
    assert (c.billing_first, c.billing_count, c.audit_first, c.audit_count) == (50, 5, 120, 10)
    assert c.billing_ok and c.audit_ok
    # billing: 52 slots of 45 bytes from 0x3000; 5 records from slot 50 wrap after 2
    assert p.ring_reads(p.billing_base("DD"), p.BILLING_RECORD, p.BILLING_SLOTS, 50, 5) == [
        (0x3000 + 50 * 45, 2), (0x3000, 3)]
    assert p.ring_reads(p.audit_base("DD", 7019), p.AUDIT_RECORD, p.AUDIT_SLOTS, 0, 4) == [(0x3A00, 4)]
    assert p.billing_base("CA") == 0x2000 and p.audit_base("CD", 4100) == 0x3E00
    bad = p.parse_cdr_counts(bytes([1, 2, 1, 3, 0, 0, 0, 0]))  # the two copies disagree
    assert not bad.billing_ok and bad.audit_ok
    assert p.ring_reads(0x3000, 45, 52, 1, 2, ok=False) == [(0x3000, 52)]  # read the whole ring
    phone = SimPhone(security_code="4821")
    phone.memory[p.CDR_COUNTS:p.CDR_COUNTS + 8] = bytes([3, 4, 3, 4, 7, 8, 7, 8])
    link = p.Link(SimTransport(phone), timeout=0.05)
    link.security("4821")
    assert link.cdr_counts().audit_count == 8


def test_status_money_and_flags():
    from coinroute.xnet import TROUBLE_FLAGS, trouble_flags

    flags = bytes([0x01, 0, 0, 0, 0, 0, 0x01, 0, 0])  # E$ (48/0x01) and CB (54/0x01)
    body = p.status_body("5550100123", "DD", "2605027010509", flags=flags, coin_total=123456,
                         percent_full=42, cash_collected=1550)
    st = p.parse_status(body)
    assert (st.coin_total, st.percent_full, st.cash_collected) == (23456, 42, 1550)  # 5-digit counter
    assert st.clock == "2605027010509" and st.flags == flags
    coin = bytes(48) + st.flags  # where ExpressNet stores them in a COIN.DAT record
    assert {c for c, *_ in TROUBLE_FLAGS if c in trouble_flags(coin.ljust(88, b"\0"))} >= {"E$", "CB"}


def test_clear_masks_are_expressnets():
    # the exact 'S' frames ExpressNet sends after each step
    assert p.flag_mask(*p.CLEAR_BILLING) == bytes([0, 0xC0, 0x80, 0, 0, 0, 0, 0])
    assert p.flag_mask(*p.CLEAR_AUDIT) == bytes([0, 0, 0x40, 0, 0, 0, 0, 0])
    assert p.flag_mask(*p.CLEAR_CALL_ACCOUNTING) == bytes([0, 0x20, 0, 0, 0, 0, 0, 0])
    assert p.flag_mask(*p.CLEAR_COIN) == bytes([1, 0, 0, 0, 0, 0, 0, 0])
    assert p.cmd_clear_flags(p.flag_mask("E$"))[:4] == b"T\x0aS\x01"


def test_record_reads_in_expressnets_chunks():
    runs = p.ring_reads(0x3A00, p.AUDIT_RECORD, p.AUDIT_SLOTS, 120, 13)  # wraps after 5
    assert p.record_reads(runs, p.AUDIT_RECORD, p.AUDIT_PER_READ) == [
        (0x3A00 + 120 * 23, 5 * 23), (0x3A00, 8 * 23)]
    runs = p.ring_reads(0x3000, p.BILLING_RECORD, p.BILLING_SLOTS, 0, 12)
    assert [n for _, n in p.record_reads(runs, p.BILLING_RECORD, p.BILLING_PER_READ)] == [225, 225, 90]
