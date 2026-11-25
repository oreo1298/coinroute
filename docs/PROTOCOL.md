# The payphone protocol

What coinroute knows about how ExpressNet talks to a Protel phone once the two
modems are connected. Worked out by studying ExpressNet 3 v1.51 in a session
(nothing of Protel's is in this repo) and written down in our own words.
**Nothing here has been checked against a real phone yet** — a capture of one
real poll is the next step (see the end).

Confidence: ✅ read directly from how ExpressNet builds or checks the bytes ·
🟡 probable · ❓ unknown.

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

A reply whose first body byte is **`'G'`** means "accepted"; every command
step checks for it. ✅ What a phone sends instead when it refuses is ❓ (the
simulator assumes `'E'`). The reply check byte is assumed to follow the same
rule as commands. 🟡

## Timing and retries ✅

- Before sending, ExpressNet waits until the modem's receive buffer is empty,
  for at most about 4 BIOS clock ticks (~0.22 s), and logs "Receive buffer not
  empty" if it never empties.
- Each command is tried **4 times**. After the fourth failure the step's error
  code (the `COMM_ERRORS` list) is recorded and the poll moves on or ends.
- Reply time-outs per step: ❓.

## Phone types ✅

Each site has a phone type letter — **A**, **C** or **D** (the program speaks of
DA, DD and BC/CD phones). The same step can use a different command for each
type, e.g. clearing the coin flag:

| Type | Command sent |
|---|---|
| A | `S 01 00 00` |
| C | `I` |
| D (and others) | `S 01 00 00 00 00 00 00 00` |

The phone reports its type as a small number (1–5) in its status; ExpressNet
accepts type numbers 1, 4, 5 for A sites, 2, 3, 5 for D and 1–4 for C, and
otherwise stops with "Phone type mismatch". Where that number sits in the
status the phone sends: ❓

## Commands

| Letter | Arguments | L | Used for (from the step's error code) | |
|---|---|---|---|---|
| `D` | address high, address low, count | 5 | reading phone memory: CDR counts, audit and billing records, speed dials | ✅ layout, 🟡 meaning of bytes |
| `X` | three address bytes, count | 6 | the same reads on phones with more memory | ✅ layout |
| `S` | address high, address low, 1 or 6 data bytes | 5 / 10 | writing phone memory: clear status flags, clear coin flag, reset call accounting, clear CDR flags | ✅ layout |
| `Q` | 16-bit value, low byte first | 4 | security code (errors 13/14 follow it) | ✅ layout; ❓ how the 4-digit code becomes the value (number or BCD) |
| `P` | 13 ASCII digits | 15 | set the phone's clock | ✅ layout; 🟡 order yymmdd, weekday, hhmmss |
| `K` | two bytes | 4 | clear audit CDRs | ✅ layout |
| `T` | two bytes | 4 | clear billing CDRs | ✅ layout |
| `I` | — | 2 | clear coin flag on C type phones | ✅ |
| `L`, `N`, `O` | — | 2 | end-of-call steps | 🟡 |
| `R` | — | 2 | call-accounting step | 🟡 |
| `B` | — | 2 | speed dials | 🟡 |

Addresses seen in ExpressNet's commands (meanings 🟡, taken from the step that
uses them): an 8-byte read at 0FF8 when collecting CDR counts; a 4-byte read
at FF1F and a write of 3F at DA1F when clearing status flags; a write of 00 at
0100 to clear the coin flag; writes at 0020 to reset call accounting; a 2-byte
read at 2E06 for speed dials.

There is also a `Txd` + two bytes frame sent during connection set-up ❓.

## A poll, step by step

ExpressNet's modem-status screen names the steps; the commands are matched to
them through the error code each step records (✅ where the match is direct).

1. Reset the modem, dial, wait for `CONNECT` (errors 4–7). ✅
2. Receive the phone's status frame: its check byte, phone number and phone
   type are checked (errors 8–12). ✅ that it happens; ❓ the frame's layout.
3. Send the security code — `Q` (errors 13–14). 🟡
4. Clear status flags — `S` to DA1F (error 16). ✅
5. Software version checks, firmware download if one is pending (errors 17–36). ❓
6. Collect CDR counts — `D`/`X` at 0FF8 (errors 37–39). 🟡
7. Collect billing and audit CDRs — `D`/`X` reads; clear them — `T`/`K`
   (errors 40–55). 🟡
8. Clear the coin flag — `S`/`I` (error 33). ✅
9. Read and reset call accounting (errors 66–72). 🟡
10. Reset the phone's clock — `P` (error 73). ✅
11. End the call — `O`, `N`; then `+++`, `OK`, `ATH0` (error 99 if stopped). 🟡

Coin totals and the cash-box amount reach COIN.DAT during this sequence; which
read carries them is ❓.

## In code

- `coinroute.protocol` — frames, check byte, a stream reader that resyncs on
  `'T'`, builders for the commands above, and `Link` (send, wait, check `'G'`,
  retry four times).
- `coinroute.modem` — drives the UPMS1200: reset and init, dial (results mapped
  to ExpressNet's error codes: BUSY 5, NO CARRIER 6, time-out 4), answer a
  ring, `+++` / `ATH0`; `SerialLine` for a real port; `.link()` hands the
  connected line to `protocol.Link`.
- `coinroute probe NUMBER --port DEV --code N` (experimental) — dials, sends the
  security code, reads memory and hangs up, printing the frames.
- `tools/fake_upms.py --pty --simphone CODE` — the fake modem with a simulated
  phone behind it, so `coinroute probe` and the coming poller can be tried end
  to end without hardware.
- `coinroute.simphone` — a simulated phone that answers those commands from a
  64 KB memory image, refuses reads and writes until the right security code,
  and can drop or spoil replies to exercise retries. Its choices where the real
  phone is unknown are listed in its docstring.

## What a real capture must settle

One logged poll of a real phone by ExpressNet (the bytes between the PC and the
UPMS1200, both directions, with timing) would answer most of the ❓ above: the
status frame, the security code encoding, the refusal reply, the read addresses
for coin totals and CDRs, time-outs, and whether replies use the same check
byte. `tools/serial_tap.py` records exactly that.
