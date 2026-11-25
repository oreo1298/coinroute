"""tools/serial_tap.py: copies both ways, logs, and decodes the phone frames afterwards."""
import importlib.util
import io
import os
import threading
from contextlib import redirect_stdout
from pathlib import Path

import pytest

tty = pytest.importorskip("tty", reason="serial_tap.py uses termios (POSIX only)")

from coinroute import protocol as p  # noqa: E402
from coinroute.simphone import SimPhone  # noqa: E402

pytestmark = pytest.mark.skipif(not hasattr(os, "openpty"), reason="needs ptys")


def load_tap():
    path = Path(__file__).parent.parent / "tools" / "serial_tap.py"
    spec = importlib.util.spec_from_file_location("serial_tap", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_until(fd, needle, limit=4096):
    """Read from fd until `needle` has arrived."""
    got = b""
    while needle not in got and len(got) < limit:
        got += os.read(fd, 256)
    return got


def test_tap_passes_bytes_and_logs_frames(tmp_path):
    tap = load_tap()
    pc_m, pc_s = os.openpty()        # the PC (ExpressNet) talks on pc_s
    mo_m, mo_s = os.openpty()        # the modem answers on mo_s
    for fd in (pc_s, mo_s):
        tty.setraw(fd)
    log_path = tmp_path / "poll.log"
    log = open(log_path, "w", encoding="ascii")
    t = threading.Thread(target=tap.tap, args=(pc_m, mo_m, log, True), daemon=True)
    t.start()

    os.write(pc_s, b"ATDT5550100\r")
    assert b"ATDT5550100" in read_until(mo_s, b"\r")
    os.write(mo_s, b"\r\nCONNECT 1200\r\n")
    assert b"CONNECT" in read_until(pc_s, b"1200")

    phone = SimPhone(security_code="4821")
    os.write(pc_s, p.cmd_security("4821"))
    os.write(mo_s, phone.feed(_frame_from(mo_s)))
    reply = os.read(pc_s, 64)
    assert list(p.FrameReader().feed(reply))[0].is_ack

    for fd in (pc_s, mo_s):
        os.close(fd)
    t.join(3)
    log.close()
    text = log_path.read_text(encoding="ascii")
    assert "PC>MODEM" in text and "MODEM>PC" in text

    out = io.StringIO()
    with redirect_stdout(out):
        tap.decode(str(log_path))
    lines = out.getvalue().splitlines()
    assert any("PC>MODEM 'Q'" in ln and "51 48 21" in ln for ln in lines)
    assert any("MODEM>PC 'G'" in ln for ln in lines)
    for fd in (pc_m, mo_m):
        os.close(fd)


def _frame_from(fd):
    """Read one whole frame's bytes from fd."""
    r = p.FrameReader()
    got = b""
    while True:
        chunk = os.read(fd, 64)
        got += chunk
        if list(r.feed(chunk)):
            return got
