# Notes on XNET.EXE and the payphone protocol

What is known about ExpressNet 3 v1.51's program, for interoperability work.
Facts and short labels only; no Protel code is reproduced here. Bring your own
copy of the program to study; never commit it.

## The program

- `XNET.EXE` (1.6 MB, 1999) is a **16-bit protected-mode NE executable** built
  with **Borland C++** (runtime "Copyright 1993 Borland Intl."), run under
  Borland's DOS extender: `RTM.EXE` + `DPMI16BI.OVL`. Needs a 286+ and
  `SHARE.EXE` (file locking).
- About 1.2 MB of code in 247 segments; DGROUP (globals) is segment 245.
- Libraries: **FairCom c-tree** (all `.DAT`/`.IDX` files), what looks like
  **Vermont Views** (text-mode UI; `VC\SRC\VC*.C`), StratosWare **MemCheck**.
- **String literals sit in the code segments** right after the functions that
  use them, and assert/trace strings name the C source file (`..\MODEM.C`), so
  179 of Protel's source-file names are visible. `tools/re/ne.py ... modules`
  lists them per segment.
- Command line: `/d<path>` and `/s:n` (swap file), `/c` (create deleted data
  files), `/h`, `/v`, and **`/m:n` = milliseconds between modem service calls**
  (the first knob to try if the modem misbehaves in a VM).
- Built-in **Communication Monitor** (F8 on the modem activity screen) and
  trace settings ("Edit Trace Flags", `TraceOn.txt`, `TraceBuffer=`); worth
  testing as a free source of protocol captures.

### Segment map (v1.51) — where to look

| Segment | Source file(s) | Contents |
|---|---|---|
| 17 | EDITPORT.C | port setup screens |
| 19 | EDITPASS.C | logins; calls the password encoder at 0D42, 1658, 220D, 243A; default `PROTELX3` |
| 29 | EDREPSF.C, ENCRYPT.C, ERRORS.C | password encoder at 29:03FF (key table: 19 bytes at DGROUP:0E9E) |
| 46 | LOCCONF.C | local system configuration |
| 47 | MANPOLL.C | manual polling |
| 52 | MODSTAT.C | modem status screen; the list of call steps (below) |
| 53 | MODEM.C | modem handling |
| 59 | PHCONECT.C | connect: parses `BUSY` / `NO CARRIER` / `CONNECT`; `(c) PROTEL INC` banner; `T?Q??` |
| 60 | PHDDCTF.C | rate-table (CTF) downloads to DD phones |
| 61 | PHENDCAL.C, PHFLGVER.C | end of call: `+++`, `ATH0`, `OK`; commands `T?N`, `T?O`, `T?S…`, `T?I`, `T?L`, `T?D…`; `RING` |
| 65 | PHPOLL.C | poll sequence; modem reset `+++` → `OK` |
| 98–101 | SDF*.C | delimited exports (table-driven; category names in 98) |
| 109 | SITEUTI6.C | the 115 communication result messages (`xnet.COMM_ERRORS`) |
| 123 | UART.C | its own UART driver (talks to the serial chip directly) |
| 124 | UPMS8K.C | rate-file generator for CD/DD phone types |

Segment numbers will differ in other ExpressNet versions; re-derive with
`ne.py modules`.

### Tables found so far (v1.51)

Located while decoding Milestone 3 fields; described here, not copied.

- **Trouble flags.** Three 45-entry tables of far pointers in segments 154
  (names), 155 (two-letter codes) and 156 (report legend lines), all in
  alphabetical order of the names. A helper in segment 2 returns entry *i* of
  one of them; another tests flag *i* in a record using a table of
  (mask, byte) pairs at DGROUP 0x190. The trouble report (segment 95,
  REPTROUB.C) loops *i* = 0–44 and tests both the report's selection and the
  coin record (from byte 48). FORMATS.md lists the resulting bits.
- **Export columns.** ExpressNet's delimited exports are table-driven: DGROUP
  0x3950–0x3ef0 holds column descriptors (name, type letter C/N/D, width) for
  the audit, coin, comm-error, billing and call-accounting exports. Their
  order names the undecoded bytes of each record.
- **Call-accounting categories.** Segment 238 holds 84 far pointers to the
  category names in segment 98 (SDFACCT.C), ending with "END OF ROUTES". The
  export loops over 83 categories, reading three u16 per category at
  46 + 6·*i* of the CALLACCT record.
- **Security codes.** The site editor (segment 21) asks for several 4-digit
  codes (security access, coin collection, coin box, CDR retrieval, credit
  card). They are not stored as plain digits in SITES.DAT; still to locate.

### The connect and status code (v1.51)

- **Per-port buffers** are allocated when ExpressNet starts (segment 111): a
  1 KB raw receive buffer, a 1 KB frame buffer (DGROUP 0x97EA, one far pointer
  per port), a 1,221-byte call structure filled from the site record when a
  call is set up (0x9790; KEYBOAR2.C copies the firmware-to-download name and
  the two words at site bytes 1276 and 1278 into it), and a 65-byte status copy
  (0x99FA). DGROUP 0x97FA holds a 256-byte block per port; from offset 12 it
  keeps the first 64 bytes of the status frame, `'T'` included.
- **PHONE.C (segment 62)** is the frame layer: one routine feeds received bytes
  in and, once a `'T'` is seen, takes the next byte as the length; another
  re-scans the buffer for `'T'` after noise; a third, when the count runs out,
  copies the whole frame (`'T'`, `L`, body, check) into the frame buffer.
- **PHCONECT.C (segment 59)** runs the connect steps as a per-port state
  machine and stores the step's error code per port (DGROUP 0x9A06). The
  status checks are in order: frame complete and check byte good (8–10), the
  short-status padding (L = 0x25), the copy into the per-port block, the
  phone-number compare (10 digits at frame offset 12 against two numbers in
  the call structure; 11), and the type-letter compare (12). The `Q` command
  is built there from the code word with low-byte and high-byte helpers that
  live in segment 53, next to a hex-text-to-bytes converter.
- **MODSTAT.C (segment 52)** shows the status clock as `%c%c/%c%c/%c%c` and
  `%c%c:%c%c:%c%c`, which gives the clock's field order.

### The phone firmware (`PARAM/SOFTWARE/DD*.*`)

The files are download frames (layout in PROTOCOL.md). Rebuilt into memory
images, the DD70 and DD88 families are 65C02 code: bank A is the fixed bank at
0xC000–0xFFFF with the vectors (reset, NMI, IRQ) and a `PROTEL` mark just
below them; banks B–E load at 0x8000. Interrupts arrive on NMI, which switches
banks and dispatches through a small table on a status register in zero page
(a custom I/O chip, registers around $20–$2F). DD43 files load at different
addresses and were not studied. The firmware never loads `'T'` as an
immediate, so the framing is probably table-driven or done by the I/O chip;
finding the phone's command handler is the next step there.

## The modem side (known)

The PC talks Hayes AT to a Protel UPMS1200 (or a generic modem at 300 bps):

- Stored profile: init `ATM1L0E0Q0V1X3S10=255S0=0S9=2S7=100&D2`, answer `ATA`,
  dial `ATDT` (three dial strings: separate ones for 300 and 1200 bps), reset
  `ATZ`. Generic profile uses `L2` and no `&D2`.
- Port settings in LOCAL/PORTS.DAT include `1200`, `9600` and `N82`; stop bits
  are set automatically from the phone's speed, so the DTE speed can change per
  call.
- Call: reset (`+++`, `ATZ`, init — each answered `OK`) → `ATDT<number>` →
  `CONNECT` (or `BUSY` / `NO CARRIER`) → phone session → `+++` → `OK` →
  `ATH0` → `OK`. Incoming: `RING` → `ATA` → `CONNECT`.
- It watches carrier detect ("Carrier, but no CONNECT", "Time-out on carrier
  detect").
- Protel boards' 1200 bps mode is PSK and needs the UPMS1200; with a generic
  modem ExpressNet uses 300 bps (Protel Modem = N, Polling Speed 300).

`tools/fake_upms.py` implements this side for bench tests.

## The phone side (hints, not yet a spec)

Session steps, in the order ExpressNet shows them: Ring in · Resetting the
modem · Dialing · Waiting for connect · **Receive phone status** · **Send
security code** · Clear status flags · Software download check / Get SW version
· (firmware: disable/erase/lock/unlock download device, send blocks, verify 16K
blocks) · Send parameter blocks · **Collect CDR counts** · **Get billing CDR n**
· **Get audit CDR n** · Get / reset call accounting · Reset phone clock ·
Enabling/disabling options · CTF (rate table) initiate/update/check/end · Hang
up and reset.

Failure messages imply the framing: a **sync character**, then a **status
string with a checksum** that carries the phone number and phone type (checked
against the site record), a **security code** command, per-record **checksums**
on CDRs, and **CRC** on blocks ("TX fails CRC", "RX fails CRC", "Crc8K").

Command formats seen as format strings: `T%cQ%c%c`, `T%cS%c%c%c`,
`T%cS` + 8 bytes, `T%cI`, `T%cL`, `T%cD%c%c%c`, `T%cN`, `T%cO`, and `Txd%c%c`
— a `T`, one byte (probably a port/sequence character), a command letter,
arguments. Error texts also mention "L command", "V command" and "K command"
(clear audit CDR).

Firmware/rate download files (PARAM/SOFTWARE/DD*.*) start with a text line
`NAME.EXT SIZE BLOCKS\r\n` followed by binary blocks that begin `T\x08C…`,
i.e. they appear to be stored already in download-command form.

**Next steps (Milestone 4):** the status bytes still marked ❓ in
PROTOCOL.md, the refusal reply, and the addresses the CDR and coin steps read
(PHBILAUD.C, segment 57, reads reply bytes 4–14 of its `D` replies); then one
real poll logged through a serial sniffer to confirm.

## Tools

- `tools/re/ne.py` — segment table, per-segment source files, string search,
  callers of a far function, relocation-annotated disassembly (needs `ndisasm`
  from nasm).
- PhilTel (philtel.org) documents running ExpressNet and the UPMS1200 for
  hobby use; useful background.
