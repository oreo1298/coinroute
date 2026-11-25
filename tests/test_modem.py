"""coinroute.modem against tools/fake_upms.py, in-process on a virtual clock."""
import argparse
import os
import sys

import pytest

pytest.importorskip("fcntl", reason="the fake modem (tools/fake_upms.py) is POSIX-only")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import fake_upms as fu  # noqa: E402

from coinroute import modem as mm  # noqa: E402
from coinroute.simphone import SimPhone  # noqa: E402


class Port:
    has_lines = False
    can_set_baud = False

    def __init__(self):
        self.out = bytearray()

    def write(self, d):
        self.out += d

    def set_baud(self, b):
        pass

    def lines(self):
        return None

    def set_line(self, name, on):
        pass


class FakeUpmsLine:
    """A transport whose far end is fake_upms; time only moves when the caller waits."""

    def __init__(self, **kw):
        self.t = 1000.0
        o = dict(baud="auto", dial="connect", connect_speed=1200, dial_delay=3.0, answer_delay=2.0,
                 fail_delay=8.0, guard=0.5, hangup_after=None, ring_every=None, max_rings=10,
                 carrier_pin="none", dte_ready_pin="none", echo="on", strict=False, no_plus_ok=False)
        o.update(kw)
        self.port = Port()
        log = fu.Log(None, console=False, clock=self.monotonic)
        self.fake = fu.Modem(self.port, log, argparse.Namespace(**o), clock=self.monotonic)

    def monotonic(self):
        return self.t

    def _step(self, dt=0.05):
        self.t += dt
        self.fake.tick()

    def write(self, data):
        self.fake.feed(data)

    def read(self, n, timeout):
        end = self.t + timeout
        while not self.port.out and self.t < end:
            self._step()
        out = bytes(self.port.out[:n])
        del self.port.out[:n]
        return out

    def sleep(self, s):
        end = self.t + s
        while self.t < end:
            self._step()


def test_reset_and_dial_connects():
    line = FakeUpmsLine()
    m = mm.Modem(line)
    m.reset()
    r = m.dial("5550123")
    assert r.connected and r.result == "CONNECT 1200" and r.speed == 1200 and r.error_code == 0
    assert line.fake.mode == "online"
    m.hangup()
    assert line.fake.mode == "command" and not line.fake.call_up and not m.online


@pytest.mark.parametrize("outcome,code", [("busy", 5), ("nocarrier", 6), ("noanswer", 4)])
def test_failed_dials_map_to_expressnet_codes(outcome, code):
    m = mm.Modem(FakeUpmsLine(dial=outcome))
    m.reset()
    r = m.dial("5550123", timeout=90)
    assert not r.connected and r.error_code == code


def test_poll_through_modem_and_simulated_phone():
    line = FakeUpmsLine()
    phone = SimPhone(security_code=4821)
    phone.memory[0x0FF8:0x1000] = bytes(range(1, 9))
    line.fake.far_end = phone.feed
    m = mm.Modem(line)
    m.reset()
    assert m.dial("5550123").connected
    link = m.link(timeout=2.0)
    link.security(4821)
    assert link.read_memory(0x0FF8, 8) == bytes(range(1, 9))
    m.hangup()
    assert not line.fake.call_up


def test_answer_incoming_call():
    line = FakeUpmsLine()
    m = mm.Modem(line)
    m.reset()
    line.fake.start_ring()
    assert m.wait_for_ring(10)
    assert m.answer().connected


def test_modem_errors_are_reported():
    m = mm.Modem(FakeUpmsLine(strict=True))  # strict: commands it does not know get ERROR
    with pytest.raises(mm.ModemError, match="ERROR"):
        m.at("AT&Q99", timeout=1)
    silent = mm.Modem(FakeUpmsLine())
    silent.t.fake.feed = lambda data: None   # a modem that never answers
    with pytest.raises(mm.ModemError, match="no answer"):
        silent.at("AT", timeout=1)


@pytest.mark.skipif(not hasattr(os, "openpty") or sys.platform != "linux", reason="needs Linux ptys")
def test_probe_cli_against_fake_upms_with_simulated_phone(tmp_path):
    """The whole bench: `coinroute probe` -> pty -> fake_upms.py -> simulated phone, in real time."""
    import subprocess
    import time
    tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "fake_upms.py")
    link = tmp_path / "modem"
    proc = subprocess.Popen([sys.executable, tool, "--pty", "--pty-link", str(link), "--simphone", "4821",
                             "--dial-delay", "0.2", "--guard", "0.2", "--echo", "off", "--quiet",
                             "--log", str(tmp_path / "fake.log")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            if link.exists():
                break
            time.sleep(0.05)
        out = subprocess.run([sys.executable, "-m", "coinroute", "probe", "5550123", "--port", str(link),
                              "--code", "4821", "--read", "0100", "4", "--reply-timeout", "2"],
                             capture_output=True, text=True, timeout=60)
        assert "CONNECT" in out.stdout, out.stderr
        assert "security code accepted" in out.stdout
        assert "0100: 00 00 00 00" in out.stdout
    finally:
        proc.terminate()
        proc.wait(5)
