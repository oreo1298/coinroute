#!/usr/bin/env python3
"""Tests for fake_upms.py: unit tests with a simulated port/clock, then a live pty run."""
import argparse
import os
import subprocess
import sys
import termios
import time
import tty
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
sys.path.insert(0, TOOLS)
import fake_upms as fu  # noqa: E402

INIT = b"ATM1L0E0Q0V1X3S10=255S0=0S9=2S7=100&D2\r"   # ExpressNet's stored init string


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakePort:
    has_lines = True
    can_set_baud = True

    def __init__(self):
        self.out = bytearray()
        self.baud = 9600
        self.bauds = []
        self.pins = {}
        self.inlines = {"DSR": True, "CD": True, "CTS": True, "RI": False}

    def set_baud(self, b):
        self.baud = b
        self.bauds.append(b)

    def write(self, d):
        self.out += d

    def lines(self):
        return dict(self.inlines)

    def set_line(self, name, on):
        self.pins[name] = on

    def take(self):
        d = bytes(self.out)
        self.out.clear()
        return d


def opts(**kw):
    base = dict(baud="auto", dial="connect", connect_speed=1200, dial_delay=3.0, answer_delay=2.0,
                fail_delay=8.0, guard=0.5, hangup_after=None, ring_every=None, max_rings=10,
                carrier_pin="dtr", dte_ready_pin="dsr", echo="on", strict=False, no_plus_ok=False)
    base.update(kw)
    return argparse.Namespace(**base)


class Rig:
    def __init__(self, **kw):
        self.clock = Clock()
        self.port = FakePort()
        self.log = fu.Log(None, console=False, clock=self.clock)
        self.notes = []
        orig = self.log._w
        self.log._w = lambda text: (self.notes.append(text), orig(text))
        self.m = fu.Modem(self.port, self.log, opts(**kw), clock=self.clock)

    def send(self, data, step=0.002):
        for b in data:
            self.clock.t += step
            self.m.feed(bytes([b]))
        self.m.tick()

    def wait(self, sec, step=0.05):
        end = self.clock.t + sec
        while self.clock.t < end:
            self.clock.t += step
            self.m.tick()

    def log_text(self):
        return "\n".join(self.notes)


class UnitTests(unittest.TestCase):
    def test_expressnet_startup_sequence(self):
        r = Rig()
        r.wait(1)
        r.send(b"+++")
        r.wait(0.6)
        self.assertEqual(r.port.take(), b"+++\r\nOK\r\n")           # echoed, then OK
        r.send(b"ATZ\r")
        self.assertEqual(r.port.take(), b"ATZ\r\r\nOK\r\n")
        r.send(INIT)
        self.assertEqual(r.port.take(), INIT + b"\r\nOK\r\n")        # echo still on while it arrives
        self.assertEqual(r.m.r["E"], 0)
        self.assertEqual(r.m.S[7], 100)
        self.assertEqual(r.m.S[10], 255)
        self.assertEqual(r.m.r["&D"], 2)
        self.assertNotIn("UNKNOWN", r.log_text())

    def test_dial_connect_escape_hangup(self):
        r = Rig()
        r.send(b"ATE0\r")
        r.port.take()
        r.send(b"ATDT5551234\r")
        self.assertEqual(r.port.take(), b"")
        self.assertEqual(r.m.mode, "dialing")
        self.assertFalse(r.port.pins["DTR"])
        r.wait(3.1)
        self.assertEqual(r.port.take(), b"\r\nCONNECT 1200\r\n")
        self.assertEqual(r.m.mode, "online")
        self.assertTrue(r.port.pins["DTR"])                         # carrier raised
        r.send(b"\x02T1Q\x03")                                      # data for the phone
        self.assertEqual(r.port.take(), b"")                        # no echo online
        r.wait(0.6)
        r.send(b"+++", step=0.05)
        r.wait(0.3)
        self.assertEqual(r.port.take(), b"")                        # guard not yet elapsed
        r.wait(0.4)
        self.assertEqual(r.port.take(), b"\r\nOK\r\n")
        self.assertTrue(r.m.call_up)
        r.send(b"ATH0\r")
        self.assertEqual(r.port.take(), b"\r\nOK\r\n")
        self.assertFalse(r.m.call_up)
        self.assertFalse(r.port.pins["DTR"])                        # carrier dropped
        self.assertIn("T1Q", r.log_text())

    def test_plus_inside_data_is_not_an_escape(self):
        r = Rig(echo="off")
        r.send(b"ATD1\r")
        r.wait(3.1)
        r.port.take()
        r.wait(0.6)
        r.send(b"+++x", step=0.05)                                  # data right after +++
        r.wait(1.0)
        self.assertEqual(r.port.take(), b"")
        self.assertEqual(r.m.mode, "online")
        r.send(b"a+++", step=0.05)                                  # no guard before +++
        r.wait(1.0)
        self.assertEqual(r.port.take(), b"")
        self.assertEqual(r.m.mode, "online")

    def test_dial_outcomes(self):
        for outcome, text in (("busy", b"BUSY"), ("nocarrier", b"NO CARRIER"),
                              ("noanswer", b"NO ANSWER"), ("nodialtone", b"NO DIALTONE")):
            r = Rig(echo="off", dial=outcome)
            r.send(b"ATDT5551234\r")
            r.wait(10)
            self.assertEqual(r.port.take(), b"\r\n" + text + b"\r\n", outcome)
            self.assertEqual(r.m.mode, "command")
            self.assertFalse(r.m.call_up)

    def test_next_dial_override_is_one_shot(self):
        r = Rig(echo="off")
        fu.keyboard(r.m, "next busy")
        r.send(b"ATDT1\r")
        r.wait(3)
        self.assertIn(b"BUSY", r.port.take())
        r.send(b"ATDT1\r")
        r.wait(3.1)
        self.assertIn(b"CONNECT 1200", r.port.take())

    def test_ring_and_answer(self):
        r = Rig(echo="off")
        fu.keyboard(r.m, "ring")
        r.wait(0.1)
        self.assertEqual(r.port.take(), b"\r\nRING\r\n")
        r.wait(6.0)
        self.assertEqual(r.port.take(), b"\r\nRING\r\n")
        r.send(b"ATA\r")
        r.wait(2.1)
        self.assertEqual(r.port.take(), b"\r\nCONNECT 1200\r\n")
        self.assertEqual(r.m.mode, "online")
        fu.keyboard(r.m, "drop")
        self.assertEqual(r.port.take(), b"\r\nNO CARRIER\r\n")
        self.assertFalse(r.port.pins["DTR"])

    def test_auto_answer_with_s0(self):
        r = Rig(echo="off")
        r.send(b"ATS0=2\r")
        r.port.take()
        fu.keyboard(r.m, "ring")
        r.wait(6.1)
        r.wait(2.1)
        self.assertEqual(r.port.take(), b"\r\nRING\r\n\r\nRING\r\n\r\nCONNECT 1200\r\n")

    def test_numeric_quiet_query_and_unknown(self):
        r = Rig(echo="off")
        r.send(b"ATS7=100\r")
        r.port.take()
        r.send(b"ATS7?\r")
        self.assertEqual(r.port.take(), b"\r\n100\r\n\r\nOK\r\n")
        r.send(b"AT%C1\\N3\r")
        self.assertEqual(r.port.take(), b"\r\nOK\r\n")
        self.assertIn("UNKNOWN COMMAND(S) %C1, \\N3", r.log_text())
        r.send(b"ATV0\r")
        self.assertEqual(r.port.take(), b"0\r")
        r.send(b"ATD5\r")
        r.wait(3.1)
        self.assertEqual(r.port.take(), b"5\r")                     # CONNECT 1200 = 5
        r.m.hangup("test")                                          # back to command mode
        r.send(b"ATQ1\r")
        r.send(b"ATH0\r")
        self.assertEqual(r.port.take(), b"")                        # Q1: no result codes

    def test_strict_mode(self):
        r = Rig(echo="off", strict=True)
        r.send(b"AT%C1\r")
        self.assertEqual(r.port.take(), b"\r\nERROR\r\n")

    def test_connect_300(self):
        r = Rig(echo="off", connect_speed=300)
        r.send(b"ATDT1\r")
        r.wait(3.1)
        self.assertEqual(r.port.take(), b"\r\nCONNECT\r\n")

    def test_autobaud_hunts_then_locks(self):
        r = Rig()
        r.send(b"\x80\xf8\x00\x80")                                 # what 1200-baud "AT" looks like at 9600
        self.assertEqual(r.port.bauds[-1], 1200)
        r.send(b"AT\r")
        self.assertEqual(r.port.take(), b"AT\r\r\nOK\r\n")
        self.assertIn("speed locked at 1200 baud", r.log_text())
        r.send(b"\xff\xfe\xfd")                                     # ExpressNet changed speed
        self.assertEqual(r.port.bauds[-1], 300)

    def test_dtr_drop_hangs_up_with_d2(self):
        r = Rig(echo="off")
        r.wait(0.1)
        r.send(b"ATDT1\r")
        r.wait(3.1)
        r.port.take()
        self.assertTrue(r.m.call_up)
        r.port.inlines["DSR"] = False                              # ExpressNet drops DTR
        r.wait(0.1)
        self.assertFalse(r.m.call_up)
        self.assertFalse(r.port.pins["DTR"])
        self.assertIn("ExpressNet dropped DTR (&D2)", r.log_text())

    def test_character_aborts_dial(self):
        r = Rig(echo="off")
        r.send(b"ATDT1\r")
        r.wait(1)
        r.send(b"x")
        self.assertEqual(r.port.take(), b"\r\nNO CARRIER\r\n")
        self.assertEqual(r.m.mode, "command")

    def test_hangup_after(self):
        r = Rig(echo="off", hangup_after=5)
        r.send(b"ATDT1\r")
        r.wait(3.1)
        r.port.take()
        r.wait(5.1)
        self.assertEqual(r.port.take(), b"\r\nNO CARRIER\r\n")

    def test_ato_resumes(self):
        r = Rig(echo="off")
        r.send(b"ATDT1\r")
        r.wait(3.6)
        r.send(b"+++", step=0.05)
        r.wait(0.6)
        r.port.take()
        r.send(b"ATO\r")
        self.assertEqual(r.port.take(), b"\r\nCONNECT 1200\r\n")
        self.assertEqual(r.m.mode, "online")


class LivePtyTest(unittest.TestCase):
    """Runs the real script over a pty, the way the VM's serial port would talk to it."""

    def test_full_session(self):
        link = f"/tmp/fake_upms_test_{os.getpid()}"
        logf = f"/tmp/fake_upms_test_{os.getpid()}.log"
        p = subprocess.Popen([sys.executable, os.path.join(TOOLS, "fake_upms.py"),
                              "--pty", "--pty-link", link, "--dial-delay", "0.5", "--answer-delay", "0.3",
                              "--guard", "0.3", "--log", logf, "--quiet"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            for _ in range(50):
                if os.path.exists(link):
                    break
                time.sleep(0.05)
            fd = os.open(link, os.O_RDWR | os.O_NOCTTY)
            tty.setraw(fd)
            termios.tcflush(fd, termios.TCIOFLUSH)

            def xfer(data, wait):
                if data:
                    os.write(fd, data)
                end, got = time.time() + wait, b""
                while time.time() < end:
                    import select
                    r, _, _ = select.select([fd], [], [], 0.05)
                    if r:
                        got += os.read(fd, 1024)
                return got

            def cmd(line):
                p.stdin.write(line + "\n")
                p.stdin.flush()
                time.sleep(0.1)

            time.sleep(0.4)
            self.assertEqual(xfer(b"+++", 0.8), b"+++\r\nOK\r\n")
            self.assertEqual(xfer(b"ATZ\r", 0.3), b"ATZ\r\r\nOK\r\n")
            self.assertEqual(xfer(INIT, 0.3), INIT + b"\r\nOK\r\n")
            self.assertEqual(xfer(b"ATDT5551234\r", 0.9), b"\r\nCONNECT 1200\r\n")
            self.assertEqual(xfer(b"\x02T1Q\x03", 0.5), b"")
            self.assertEqual(xfer(b"+++", 0.8), b"\r\nOK\r\n")
            self.assertEqual(xfer(b"ATH0\r", 0.3), b"\r\nOK\r\n")
            cmd("next busy")
            self.assertEqual(xfer(b"ATDT5551234\r", 2.5), b"\r\nBUSY\r\n")
            cmd("ring")
            self.assertEqual(xfer(b"", 0.5), b"\r\nRING\r\n")
            self.assertEqual(xfer(b"ATA\r", 0.7), b"\r\nCONNECT 1200\r\n")
            cmd("say \\x02HELLO\\x03")
            self.assertEqual(xfer(b"", 0.4), b"\x02HELLO\x03")
            cmd("drop")
            self.assertEqual(xfer(b"", 0.4), b"\r\nNO CARRIER\r\n")
            os.close(fd)
        finally:
            p.communicate("quit\n", timeout=5)
        log = open(logf).read()
        for want in ("DIAL 5551234", "ONLINE", "\\x02T1Q\\x03", "+++ escape", "HUNG UP (ATH)",
                     "INCOMING CALL", "ANSWER (ATA)", "NO CARRIER"):
            self.assertIn(want, log)
        self.assertFalse(os.path.exists(link))


if __name__ == "__main__":
    unittest.main(verbosity=2)
