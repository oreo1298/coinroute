# ExpressNet 3 file formats

What coinroute knows about the data files of Protel ExpressNet 3 (v1.5x, DOS,
1993–1999). Worked out from real data files, the program's own printed reports
and its error/label strings. **Confidence**: ✅ verified against real data or
reports · 🟡 probable · ❓ unknown (kept as raw bytes).

All numbers are little-endian. Strings are fixed-width, NUL-terminated,
usually space-padded. Times are local time.

## Storage: FairCom c-tree (ca. 1990)

Every `.DAT` has a matching `.IDX` index; coinroute reads only the `.DAT`.

| Header offset | Size | Meaning |
|---|---|---|
| 0x28 | u32 | flags; bit `0x01000000` = variable-length file ✅ |
| 0x2C | u16 | record length (fixed files) / minimum length (variable) ✅ |

**Fixed-length files** ✅: 128-byte header, then `reclen`-byte records. The header
uses the first `ceil(128/reclen)` slots. First byte `0xFF` = deleted record. Small
files are pre-allocated in 4 KB, so all-zero slots are unused.

**Variable-length files** ✅ (`AUDITCDR.DAT`, `BILLCDR.DAT`): blocks of
`mark(2) total_len u32 used_len u32 data[used_len]`, where `total_len` includes
the 10-byte block header. Mark `FA FA` = live, `FD FD` = free.

## Timestamps ✅

`u32` minutes since **1899-12-31 00:00**. Verified: matches the text date/time
stored beside it in all 56,394 coin records of a real installation.

Text dates appear as `yymmdd` (CDR files), `yyyymmdd` (audit CDRs) and
`mm/dd/yy` (site and system records). Years 70–99 are 19xx.

## Common CDR header — COIN, COMMERR (44 bytes) ✅

| Off | Size | Field |
|---|---|---|
| 0 | 1 | 0 = live (0xFF = deleted) |
| 1 | 7 | group number, e.g. `000100` |
| 8 | 9 | site number (route code), space padded |
| 17 | 11 | phone number, 10 digits |
| 28 | 4 | timestamp (minutes) |
| 32 | 7 | date `yymmdd` |
| 39 | 5 | time `hhmm` |

## CDR/COIN.DAT — 88 bytes, one per successful poll

| Off | Size | Field | |
|---|---|---|---|
| 0–43 | | common header | ✅ |
| 56 | 8 | status bytes (trouble flags?) | ❓ |
| 64 | u32 | lifetime coin total, cents | ✅ matches printed Coin Report |
| 68 | u32 | cash currently in box, cents | ✅ matches "In Box" column |

## CDR/COMMERR.DAT — 130 bytes, one per communication result

| Off | Size | Field | |
|---|---|---|---|
| 0–43 | | common header | ✅ |
| 44 | 1 | result code: index into the 115-entry message list in `xnet.COMM_ERRORS` (4 = "Time-out on carrier detect", 5 = "BUSY detected", 8 = "Sync char not detected" …) | ✅ (list order confirmed by entry 100 = "100 Not presently used") |

## CDR/CALLACCT.DAT — 552 bytes, call counters per period

| Off | Size | Field | |
|---|---|---|---|
| 1, 8, 17, 28 | | group, site no, phone, timestamp (as common header) | ✅ |
| 32 | 7 | period start `yymmdd` | 🟡 |
| 39 | 7 | period end `yymmdd` | 🟡 |
| 46– | | counters by call category (local coin, intra/inter LATA, speed dials, card groups, reroutes …) | ❓ |

## CDR/AUDITCDR.DAT — variable length, one poll's batch of calls

| Off | Size | Field | |
|---|---|---|---|
| 1, 8, 17, 28 | | group, site no, phone, timestamp | ✅ |
| 32 | 9 | date `yyyymmdd` | ✅ |
| 41 | 1 | number of calls *n* | ✅ (record size = 42 + 47·n in 99.6 % of records) |
| 42 + 47·k | 47 | call entry *k* | |

**CDR/AUDITCDR.OLD** ✅ has the same format: an earlier copy of the audit file
(on one real install, last written 2020). It held ~31,500 batches (~825k calls)
missing from the current file, so the importer reads both and skips batches
already seen (same phone, timestamp and call count). Batches that repeat with
different bytes are counted in a warning; the first one read is kept.

Call entry: `dialed[17]` ✅, `date yyyymmdd[9]` ✅, `time hhmm[5]` ✅, then 16
bytes of detail ❓ (probably duration, amounts, call type).

## SITES/SITES.DAT — 2456 bytes, one per payphone

| Off | Size | Field | |
|---|---|---|---|
| 1 | 11 | phone number | ✅ |
| 12 | 61 | description | ✅ |
| 86 | 13 | rate (call costing) record name → `PARAM/CALLCOST.DAT` | ✅ |
| 112 | 13 | options record name → `PARAM/MISCOPT.DAT` | ✅ |
| 383 | 7 | group number | ✅ |
| 390 | 9 | site number | ✅ |
| 896 | 5 | speed (`1200`) | 🟡 |
| 903 | 9 | install date `mm/dd/yy` | 🟡 |
| 980 | 31 | location name | ✅ |
| 1011 | 31 | street address | ✅ |
| 1042 | 21 | city | ✅ |
| 1063 | 3 | state | ✅ |
| 1066 | 11 | ZIP | ✅ |
| 1250 | 13 | firmware in phone (`DD7019-0023R`) | 🟡 |
| 1263 | 13 | firmware to download | 🟡 |
| rest | | security code, flags, poll status, contact fields … | ❓ |

## SYSTEM/X3SYSPAR.DAT — 211 bytes, XOR-scrambled ✅

Byte *j* of the record is XORed with `(0xAB + j) & 0xFF`. Then: install date
`mm/dd/yy` at 1, company name [31] at 19, licence number [11] at 50.

## SYSTEM/PASSWORD.DAT — 83 bytes, logins ✅

User [13] at 1, description [41] at 14, encoded password [8] at 0x37, key salt
at 0x4F. Key = 13 rounds of `k = ((k + rec[1+i]) & 0xFF) ^ rec[0x4F]`. The
encoder (`scramble.py`) XORs with a 19-byte table, rotates, and swaps bits 2/4 in
every third byte. Reversible, with two quirks of the original that
`check_password` reproduces: keys `0xF0–0xFF` never store the 8th character,
and the result is `strcpy`'d (a 0 byte truncates it).

## Configuration files

| File | Len | Known fields |
|---|---|---|
| LOCAL/PORTS.DAT | 313 | port no u8 @1, modem name [21] @3, description [41] @24, init [41] @65, answer [41] @106, three dial strings [21] @147/168/189, reset [21] @210, line number [14] @232 ✅; speeds/framing ("1200", "9600", "N82") around 246–311 ❓ |
| PARAM/MODEMS.DAT | 230 | name [21] @1, description [41] @22, init [41] @63, answer [41] @104, dial ×3 [21] @145/166/187, reset [21] @208 ✅ |
| LOCAL/GROUPS.DAT | 22 | group number [7] @1 ✅ |
| LOCAL/POLLIST.DAT | 231 | name [13] @1, description [61] @14, start time `hh:mm` @79 ✅; weekday flags around 87–103 ❓ |
| PARAM/CALLCOST.DAT | 36781 | name [13] @1 ✅; rate tables ❓ |
| PARAM/MISCOPT.DAT | 420 | name [13] @1 ✅; phone options ❓ |
| SITES/OPTNCNFG, OPTNHIST, OPTNDESC | | ❓ |
| LOCAL/REPCTL, RPTQUE, PRINTERS, EDMANPOL | | ❓ (reports, printers, manual polls) |
| PARAM/SOFTWARE/DD*.* | | phone firmware; text header `name size blocks\r\n`, then what look like download blocks ❓ |

## Known damage patterns

Real installations ran for decades on FAT disks. One real `AUDITCDR.DAT`
contained ~19.7 MB of unrelated 32-bit Windows program code (cross-linked
clusters). The variable-length reader skips such stretches and resumes at the
next valid block; the importer reports how much was skipped and where.
