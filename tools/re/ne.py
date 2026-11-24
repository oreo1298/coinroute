#!/usr/bin/env python3
"""Helper for studying XNET.EXE (a 16-bit Windows-style NE executable run under
Borland's RTM DOS extender). Bring your own copy of the EXE; never commit it.

    python tools/re/ne.py XNET.EXE segs                  # segment table
    python tools/re/ne.py XNET.EXE modules               # source files named in each segment
    python tools/re/ne.py XNET.EXE find "BUSY"           # where a string lives (segment:offset)
    python tools/re/ne.py XNET.EXE strings 59            # readable strings in segment 59
    python tools/re/ne.py XNET.EXE callers 29 0x3ff      # far calls into segment 29 offset 0x3ff
    python tools/re/ne.py XNET.EXE disasm 29 0x3ff 0x5a6 # 16-bit disassembly with fixups (needs ndisasm)

Facts used: string literals live in the *code* segments next to the functions
that use them; DGROUP (globals) is the last large data segment (245 in v1.51);
far calls/pointers are left as 0000:FFFF in the code and resolved through each
segment's relocation table, which `disasm` prints beside the instruction.
"""
from __future__ import annotations

import re
import shutil
import signal
import struct
import subprocess
import sys


class NE:
    def __init__(self, path):
        d = self.d = open(path, "rb").read()
        if d[:2] != b"MZ":
            raise ValueError("not an MZ/NE executable")
        ne = self.ne = struct.unpack_from("<I", d, 0x3C)[0]
        if d[ne:ne + 2] != b"NE":
            raise ValueError("no NE header")
        f = struct.unpack_from("<BBHHIHHHHIIHHHHHHHHIHHHB", d, ne + 2)
        self.nseg, segtab, align = f[11], f[14], f[21]
        self.segs = []
        for i in range(self.nseg):
            off, ln, fl, _ = struct.unpack_from("<HHHH", d, ne + segtab + 8 * i)
            ln = ln if ln or not off else 0x10000
            self.segs.append({"n": i + 1, "foff": off << align, "len": ln,
                              "data": bool(fl & 1), "reloc": bool(fl & 0x100)})

    def seg(self, n):
        return self.segs[n - 1]

    def bytes_of(self, n):
        s = self.seg(n)
        return self.d[s["foff"]:s["foff"] + s["len"]]

    def seg_of(self, fo):
        for s in self.segs:
            if s["foff"] <= fo < s["foff"] + s["len"]:
                return s, fo - s["foff"]
        return None, None

    def relocs(self, n):
        s = self.seg(n)
        if not s["reloc"]:
            return []
        p = s["foff"] + s["len"]
        count = struct.unpack_from("<H", self.d, p)[0]
        out = []
        for i in range(count):
            atype, rtype, off, a, b = struct.unpack_from("<BBHHH", self.d, p + 2 + 8 * i)
            out.append({"atype": atype, "rtype": rtype, "off": off, "a": a, "b": b})
        return out


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    ne, cmd, args = NE(argv[1]), argv[2], argv[3:]
    if cmd == "segs":
        for s in ne.segs:
            print(f"{s['n']:4d} {'DATA' if s['data'] else 'CODE'} file@{s['foff']:#09x} len {s['len']:6d}"
                  f"{' +relocs' if s['reloc'] else ''}")
    elif cmd == "modules":
        for s in ne.segs:
            if s["data"]:
                continue
            names = sorted(set(m.group().decode() for m in re.finditer(rb"\.\.\\[A-Z0-9_\\]+\.C", ne.bytes_of(s["n"]))))
            if names:
                print(f"{s['n']:4d} {' '.join(names)}")
    elif cmd == "find":
        pat = args[0].encode("latin-1")
        for m in re.finditer(re.escape(pat), ne.d):
            s, o = ne.seg_of(m.start())
            print(f"seg {s['n'] if s else '-':>4}:{o if o is not None else 0:04x}  {ne.d[m.start():m.start() + 60]!r}")
    elif cmd == "strings":
        b = ne.bytes_of(int(args[0]))
        for m in re.finditer(rb"[\x20-\x7e]{4,}", b):
            t = m.group().decode("latin-1")
            if sum(c.isalpha() for c in t) >= 3:
                print(f"{m.start():04x} {t!r}")
    elif cmd == "callers":
        tseg, toff = int(args[0]), int(args[1], 0)
        for s in ne.segs:
            if s["data"]:
                continue
            for r in ne.relocs(s["n"]):
                if r["rtype"] in (0, 4) and r["atype"] == 3 and r["a"] == tseg and r["b"] == toff:
                    print(f"seg {s['n']}:{r['off'] - 1:04x} calls {tseg}:{toff:04x}")
    elif cmd == "disasm":
        if not shutil.which("ndisasm"):
            sys.exit("ndisasm not found (install nasm)")
        n, a, b = int(args[0]), int(args[1], 0), int(args[2], 0)
        code = ne.bytes_of(n)
        rel = {r["off"]: r for r in ne.relocs(n)}
        out = subprocess.run(["ndisasm", "-b16", "-o", str(a), "-"], input=code[a:b],
                             capture_output=True, check=True).stdout.decode()
        for line in out.splitlines():
            off = int(line.split()[0], 16)
            note = ""
            for k in range(off, off + 6):
                if k in rel:
                    r = rel[k]
                    note = f"   ; fixup -> seg {r['a']}:{r['b']:04x} (type {r['atype']}/{r['rtype']})"
            print(line + note)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # quiet when piped into head
    sys.exit(main(sys.argv))
