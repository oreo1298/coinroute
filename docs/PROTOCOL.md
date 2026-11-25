# The payphone protocol

What coinroute knows about how ExpressNet talks to a Protel phone once the two
modems are connected. Worked out by studying ExpressNet 3 v1.51 and the phone
firmware files it ships (`PARAM/SOFTWARE/DD*.*`) in a session — nothing of
Protel's is in this repo — and written down in our own words.
**Nothing here has been checked against a real phone yet**; a capture of one
real poll would confirm it (see the end).

Confidence: ✅ read directly from how ExpressNet builds or checks the bytes, or
from Protel's own stored frames · 🟡 probable · ❓ unknown.

## Layers

1. **Modem** (Hayes AT to the UPMS1200): reset, dial, `CONNECT` / `BUSY` /
   `NO CARRIER`, `+++` and `ATH0` to hang up. See REVERSE_ENGINEERING.md and
   `tools/fake_upms.py`. ✅
2. **Phone link**: frames as below, exchanged after `CONNECT`.

## Frames ✅

Both directions use the same shape:

```
'T'  L  body…  C
```

- `'T'` (0x54) starts every frame. ExpressNet's receiver scans its buffer for
  `'T'`, which is what its "sync char not detected" error refers to.
- `L` is the number of bytes that follow it: the body plus the check byte. The
  receiver uses it to know how many bytes to wait for.
- The body of a command starts with a command letter; arguments follow.
- `C` is the check byte: the bitwise NOT of the 8-bit sum of every byte before
  it, `'T'` and `L` included.

ExpressNet's own commands all follow the length rule except one: for "A" type
phones the clear-coin-flag command carries L = 6 for a 5-byte remainder. Treat
that as a quirk of the original until a capture shows otherwise.

The check-byte rule is confirmed by Protel's own data: the firmware download
files hold several thousand ready-made frames, and every one of their check
bytes follows it. ✅

Replies come back in the same shape: ExpressNet's receiver waits for `'T'`,
takes the next byte as the length, and collects that many bytes before it
looks at the reply. ✅ After a write, clear or download command, a reply whose
first body byte is **`'G'`** means "accepted". ✅ **Read replies are different:**
ExpressNet never checks them for `'G'`; it takes the data from body byte 4
after a `D` and byte 5 after an `X`, i.e. after an echo of the command letter
and its argument bytes. ✅ where the data starts; 🟡 that the bytes before it
are an echo. What a phone sends when it refuses is ❓ (the simulator assumes
`'E'`).

## Timing and retries ✅

- Before sending, ExpressNet waits until the modem's receive buffer is empty,
  for at most about 4 BIOS clock ticks (~0.22 s), and logs "Receive buffer not
  empty" if it never empties.
- Each command is tried **4 times**. After the fourth failure the step's error
  code (the `COMM_ERRORS` list) is recorded and the poll moves on or ends.
- Reply time-outs are computed per command from the port speed: about the time
  to receive the expected number of bytes, plus a fixed margin of a few
  seconds. 🟡 (exact constants ❓)

## Phone types ✅

Each site has a phone type letter — **A**, **C** or **D** (the program speaks of
DA, DD and BC/CD phones). The same step can use a different command for each
type, e.g. clearing the coin flag:

| Type | Command sent |
|---|---|
| A | `S 01 00 00` |
| C | `I` |
| D (and others) | `S 01 00 00 00 00 00 00 00` |

The phone reports its type as **two letters** in its status (below), e.g.
`DA`, `DD` or `CD`; the site has a type *number* 1–5. ExpressNet compares the
second letter with the site's number and stops with "Phone type mismatch"
unless they agree:

| Second letter | Site type numbers accepted |
|---|---|
| `A` | 1, 4, 5 |
| `D` | 2, 3, 5 |
| `C` | 1, 2, 3, 4 |

A phone whose letters are `##` (type not set) can be treated as `DA` when an
ExpressNet setting allows it. ✅

## The status frame

The first frame after `CONNECT` comes from the phone, unasked. ExpressNet waits
for it (error 8 if no `'T'` arrives, 9 if it never completes, 10 if its check
byte is wrong, each after a few retries), keeps its first 64 bytes per port,
and reads these fields from it.

There are three formats, told apart by the first body byte. ExpressNet brings
the two older ones to the newest layout by inserting empty bytes, and reads
every field at the newest layout's offsets: ✅

| First byte | Format | Normalised by inserting |
|---|---|---|
| `@` | oldest | 5 zero bytes at body position 4 |
| `A` | older | 7 zero bytes at body position 2 |
| anything else | newest | nothing |

Offsets below are into the body (after `'T'` and `L`) in the newest layout:

| Body bytes | Field | |
|---|---|---|
| 0 | first byte; its meaning is not known | ❓ |
| 1–9 | the phone's **trouble flags**, stored unchanged as COIN.DAT bytes 48–56 (FORMATS.md has the table). The poll steps test their own flags here: Bill CDR Full/Limit/Err before collecting billing records, Clock Reset before the clock step, CTB rate bad before a rate download | ✅ |
| 10–19 | the phone's own number, 10 ASCII digits; must equal one of the two numbers ExpressNet dialled from (else error 11) | ✅ |
| 21–25 | the phone's running coin total, cents, 5 ASCII digits ("CurrAccumInPhone" in ExpressNet's trace). ExpressNet adds the change since the last poll to the lifetime total, so the counter may wrap | ✅ |
| 27–28 | phone type letters, see above (else error 12) | ✅ |
| 29–32 | the firmware model number after the letters (e.g. `7019` in `DD7019`); the call-record step compares it with 4204 | ✅ that it is read as a number; 🟡 that it is the model |
| 33 | checked for zero by the software-download step | ❓ |
| 39–41 | coin box percent full, 3 ASCII digits ("PercentFull") | ✅ |
| 43–55 | the phone's clock, 13 ASCII digits `yymmdd` `w` `hhmmss` — the same layout as the `P` command; the modem-status screen shows it as Date and Time | ✅ |
| 57–61 | cash collected when the box was last emptied, cents, 5 ASCII digits ("CashCollected") | ✅ |

There are two sizes. Older phones send a **short status (L = 0x25)** that ends
before the clock; ExpressNet pads its copy with spaces there. Longer statuses
carry the clock; their exact length is ❓ (ExpressNet keeps at most 64 bytes).
For `A`-format phones with a long status, ExpressNet moves body bytes 20–25 to
56–61 (where the newest format keeps the cash collected).

ExpressNet's coin record for the poll (STORCOIN.C) is built from this frame:
the flags above, the record counts from the CDR step (bytes 44–47), its own
per-call flags (57–63), and the lifetime total, cash in box and amount
collected (64, 68, 72) worked out from the three money fields and the previous
poll.

## Commands

| Letter | Arguments | L | Used for (from the step's error code) | |
|---|---|---|---|---|
| `D` | address high, address low, count | 5 | reading phone memory: CDR counts, audit and billing records, speed dials | ✅ layout, 🟡 meaning of bytes |
| `X` | three address bytes, count | 6 | the same reads on phones with more memory | ✅ layout |
| `S` | a mask over the trouble-flag bytes: 8 bytes (3 on A type phones) | 10 / 5 | **clear trouble flags**: the bits set in the mask are cleared | ✅ (below) |
| `Q` | the 4-digit security code as two packed-BCD bytes in reading order (`1234` → `12 34`) | 4 | security code (errors 13/14 follow it) | ✅ (see below) |
| `P` | 13 ASCII digits | 15 | set the phone's clock | ✅ layout and order: yymmdd, weekday, hhmmss (the status shows the clock the same way) |
| `K` | two bytes from the site's rate record (below) | 4 | clear audit CDRs | ✅ |
| `T` | the same two bytes | 4 | clear billing CDRs | ✅ |
| `I` | — | 2 | clear coin flag on C type phones | ✅ |
| `L`, `N`, `O` | — | 2 | end-of-call steps | 🟡 |
| `R` | — | 2 | call-accounting step | 🟡 |
| `B` | — | 2 | speed dials | 🟡 |

`S` is not a memory write, as first thought. Its bytes line up with the
trouble-flag bytes (COIN.DAT 48–55), and each step sends exactly the bits of
its own flags: ✅

| After | `S` bytes | Flags cleared |
|---|---|---|
| reading the cash collected | `01 00 00 00 00 00 00 00` | E$ Cash Emptied |
| collecting billing records | `00 C0 80 00 00 00 00 00` | CF Bill CDR Full, CL Bill CDR Limit, CE Bill CDR Err |
| collecting audit records | `00 00 40 00 00 00 00 00` | NE Audit CDR Err |
| resetting call accounting | `00 20 00 00 00 00 00 00` | AE Call Acct Err |

Other addresses seen: the 8-byte CDR counts at 0FF8, a 4-byte read at FF1F
before clearing status flags, a 2-byte read at 2E06 for speed dials (🟡).

There is also a `Txd` + two bytes frame sent during connection set-up ❓.

### The security code ✅

ExpressNet keeps each site's code in the site record (SITES.DAT bytes
1278–1279) as a 16-bit value whose hex digits are the code's digits, and sends
its low byte then its high byte. Stored as bytes, that is simply the code in
packed BCD: `1234` is sent as `51 12 34` after `'T'` and `L`. Checked on a real
installation: for every site, those two bytes are the BCD form of the site's
security access code in its options record (MISCOPT.DAT).

### Downloads (`C`) ✅

The firmware files in `PARAM/SOFTWARE` are a text header line
(`NAME.EXT SIZE BLOCKS`) followed by ready-made frames, one per line (each
ends with CR LF, which is not sent). All use command `C`:

| Body bytes | Data frames (L = 136) | Control frames (L = 8) |
|---|---|---|
| 0 | `C` | `C` |
| 1–2 | address, high byte first (0x8000–0xBF80, steps of 128) | 0 0 |
| 3 | 2 | operation: 6, 1 at the start; 3 at the end of each bank; 0, 5 at the very end |
| 4 | bank letter `A`–`E` | bank letter, or `Z` for the whole download |
| 5–6 | a count that falls by one every 256 bytes ❓ | a number ❓ |
| 7–134 | 128 bytes of program | — |

The phone is built around a **65C02-family processor**. Its program comes in
16 KB banks that each load through the 0x8000–0xBFFF window: bank A runs fixed
at 0xC000–0xFFFF (it holds the reset and interrupt vectors) and banks B–E are
switched into 0x8000–0xBFFF. Its I/O is a custom chip in zero page. Reading the
phone's side of the protocol from these images is under way.

## A poll, step by step

ExpressNet's modem-status screen names the steps; the commands are matched to
them through the error code each step records (✅ where the match is direct).

1. Reset the modem, dial, wait for `CONNECT` (errors 4–7). ✅
2. Receive the phone's status frame: its check byte, phone number and phone
   type are checked (errors 8–12). ✅ (layout above)
3. Send the security code — `Q` (errors 13–14). ✅
4. Clear status flags — `S` (error 16). ✅
5. Software version checks, firmware download if one is pending (errors 17–36). ❓
6. Collect CDR counts — `D`/`X` at 0FF8 (errors 37–39). 🟡
7. Collect billing and audit CDRs — `D`/`X` reads; clear them — `T`/`K`
   (errors 40–55). 🟡
8. Clear the coin flag — `S`/`I` (error 33). ✅
9. Read and reset call accounting (errors 66–72). 🟡
10. Reset the phone's clock — `P` (error 73). ✅
11. End the call — `O`, `N`; then `+++`, `OK`, `ATH0` (error 99 if stopped). 🟡

Coin totals, the cash-box amount and the trouble flags all come from the
status frame (above), not from a memory read. ✅

## Collecting call records (CDRs) ✅

From PHBILAUD.C; addresses and sizes are ExpressNet's constants.

1. **Counts**: read 8 bytes at `0FF8` (`D 0F F8 08`; `X 0F F8 00 08` on phones
   with more memory; one phone family uses `D 02 00 08`). They are two pairs,
   each sent twice: billing *(first slot, number of records)* ×2, then audit
   *(first slot, number of records)* ×2. If a pair's two copies differ, or the
   numbers don't fit the ring (billing ≤ 52, audit ≤ 125), ExpressNet notes it
   and reads that whole ring from slot 0.
2. **Billing records**: a ring of **52 records of 45 bytes**, from `2000` on
   `CA` phones and `3000` on the others.
3. **Audit records**: a ring of **125 records of 23 bytes**, from `3A00`
   (`3E00` on `CD` phones whose model number is below 4204).
4. Records are read from *base + first slot × record size*, wrapping to the
   ring's start when the run passes the last slot, **5 billing records
   (225 bytes) or 10 audit records (230 bytes) per read**, then cleared with
   `T` (billing) / `K` (audit) and the flag masks above. The two bytes after
   `T` and `K` come from the site's rate record (CALLCOST.DAT): offset 2499
   for phones whose type starts with `D`, 4769 for the others; they are zero
   in every rate record seen. ✅ coinroute saves the records in the database
   before it sends the clear commands.

`protocol.parse_cdr_counts`, `billing_base`, `audit_base`, `ring_reads` and
`record_reads` implement this; `Link.cdr_counts()` reads the counts and
`Link.clear_flags()` sends the masks.

## In code

- `coinroute.protocol` — frames, check byte, a stream reader that resyncs on
  `'T'`, builders for the commands above, and `Link` (send, wait, check `'G'`,
  retry four times).
- `coinroute.modem` — drives the UPMS1200: reset and init, dial (results mapped
  to ExpressNet's error codes: BUSY 5, NO CARRIER 6, time-out 4), answer a
  ring, `+++` / `ATH0`; `SerialLine` for a real port; `.link()` hands the
  connected line to `protocol.Link`.
- `coinroute probe NUMBER --port DEV --code 1234` (experimental) — dials,
  reads and prints the phone's status, sends the security code, reads memory
  and hangs up, printing the frames.
- `tools/fake_upms.py --pty --simphone CODE` — the fake modem with a simulated
  phone behind it, so `coinroute probe` and the coming poller can be tried end
  to end without hardware.
- `coinroute.simphone` — a simulated phone that sends its status frame after
  `CONNECT`, answers those commands from a 64 KB memory image, refuses reads
  and writes until the right security code, and can drop or spoil replies to
  exercise retries. Its choices where the real phone is unknown are listed in
  its docstring.
- `protocol.Status` / `Link.receive_status()` — the status fields above, with
  ExpressNet's phone-number and phone-type checks.
- `coinroute.poller` — a whole poll in this order, stored like ExpressNet's
  records; `coinroute poll DB PHONE --port DEV` (or `--simulate`).
- `simphone.SimModemLine` — a modem with a simulated phone behind it, in
  process, on any platform (the poller's tests and `--simulate`).

## What a real capture must settle

One logged poll of a real phone by ExpressNet (the bytes between the PC and the
UPMS1200, both directions, with timing) would confirm everything above and
answer the remaining ❓: the status bytes not yet understood, the refusal
reply, the read addresses for coin totals and CDRs, and time-outs.
`tools/serial_tap.py` records exactly that.
