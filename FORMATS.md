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
| 44 | u8 | billing records the phone held ("Bill RCnt.") | 🟡 name from ExpressNet's coin export; almost always 0 |
| 45 | u8 | audit records the phone held ("Aud RCnt.") | 🟡 |
| 46 | u8 | billing records collected ("B RCol.") | 🟡 |
| 47 | u8 | audit records collected ("A RCol.") | 🟡 |
| 48 | 9 | trouble flags ("CFlags"), table below: the phone's status bytes 1–9 stored unchanged (docs/PROTOCOL.md); byte 56 holds FC (0x01) and CT (0x02) | ✅ |
| 57 | 7 | ExpressNet's own flags for the call ("PFlags"), e.g. that a pair of CDR counts disagreed | 🟡 |
| 64 | u32 | lifetime coin total, cents | ✅ matches printed Coin Report |
| 68 | u32 | cash currently in box, cents | ✅ matches "In Box" column |
| 72 | u32 | amount taken out when the box was emptied, cents ("Amt. Col.") | ✅ non-zero only on polls with the E$ flag (299 of 299 in a real installation) |
| 76 | u32 | amount entered by hand ("Amt. Man.") | 🟡 rarely non-zero |
| 80 | 8 | always zero in real data | |

### Trouble flags ✅

Each flag is one bit. The mapping is ExpressNet's own: its trouble report tests
these bits (it keeps a table of byte/mask pairs in the order of the flag names),
and its printed trouble reports agree. RC, RJ, RR, DI and FC were checked against
84 printed report lines; E$ is set on 90 % of polls where the box went down and on
0.1 % of the others. Which flags a printed report shows is a per-report choice.

| Byte | Mask | Code | Meaning |
|---|---|---|---|
| 48 | 0x01 | E$ | Cash Emptied |
| 48 | 0x02 | ST | Status Check |
| 48 | 0x04 | PR | Phone Repair |
| 48 | 0x08 | PC | Peg Count |
| 48 | 0x10 | $V | Cash Vol Full |
| 48 | 0x20 | RE | Ram Error |
| 48 | 0x40 | RJ | Relay Jam |
| 48 | 0x80 | LR | Loop Reversed |
| 49 | 0x01 | VE | Cash Vol Err |
| 49 | 0x02 | $E | Coin Tot Err |
| 49 | 0x04 | TE | Reset Tot Err |
| 49 | 0x08 | BE | Box Amt Err |
| 49 | 0x10 | OE | Overage Err |
| 49 | 0x20 | AE | Call Acct Err |
| 49 | 0x40 | CL | Bill CDR Limit |
| 49 | 0x80 | CF | Bill CDR Full |
| 50 | 0x02 | $F | Coin Box Full |
| 50 | 0x04 | CG | Coin Gate Fail |
| 50 | 0x08 | DE | Defect Escrow |
| 50 | 0x10 | ME | Coin Mech Err |
| 50 | 0x20 | TR | Time Report |
| 50 | 0x40 | NE | Audit CDR Err |
| 50 | 0x80 | CE | Bill CDR Err |
| 51 | 0x01 | LB | Low Battery |
| 51 | 0x10 | HO | Handset OffHk |
| 51 | 0x40 | LA | Lower Alarm |
| 51 | 0x80 | HG | Handset Gone |
| 52 | 0x01 | NF | Audit CDR Full |
| 52 | 0x10 | BG | Bad Ground |
| 52 | 0x20 | CR | Clock Reset |
| 52 | 0x40 | EE | Download Error |
| 52 | 0x80 | TG | Trouble Gone |
| 53 | 0x01 | OC | Opto-Coupler |
| 53 | 0x02 | BR | Box Removed |
| 53 | 0x04 | RR | Relay Refund |
| 53 | 0x08 | RC | Relay Collect |
| 53 | 0x10 | SN | Silicon Ser # |
| 53 | 0x20 | BM | Box Missing |
| 53 | 0x40 | DI | Dial Inactive |
| 53 | 0x80 | SU | Sig. Unit Bad |
| 54 | 0x01 | CB | CTB rate bad |
| 54 | 0x02 | AC | ASIC clk fail |
| 54 | 0x80 | TB | Temp Brd fail |
| 56 | 0x01 | FC | Failed Comms |
| 56 | 0x02 | CT | Coin Tot Fix |

## CDR/COMMERR.DAT — 130 bytes, one per failed call to a phone

Only failures are logged: a real installation (~290k records) has no code-0
("No error") rows; successful polls show up in COIN.DAT instead. ✅

| Off | Size | Field | |
|---|---|---|---|
| 0–43 | | common header | ✅ |
| 44 | 1 | result code: index into the 115-entry message list in `xnet.COMM_ERRORS` (4 = "Time-out on carrier detect", 5 = "BUSY detected", 8 = "Sync char not detected" …) | ✅ (list order confirmed by entry 100 = "100 Not presently used") |
| 45–129 | | all zero in a real installation; ExpressNet's export lists a "Port" column | ❓ |

## CDR/CALLACCT.DAT — 552 bytes, call counters per period

| Off | Size | Field | |
|---|---|---|---|
| 1, 8, 17, 28 | | group, site no, phone, timestamp (as common header) | ✅ |
| 32 | 7 | period start `yymmdd` | ✅ ExpressNet's export reads it as the start date |
| 39 | 7 | period end `yymmdd` | 🟡 |
| 46 + 6·*i* | 3 × u16 | category *i* (0–82): calls ("Peg Count"), money in cents, minutes | ✅ |
| 544 | 6 | three u16, often near 0x5555; ExpressNet's export also has "Overage" and "Rec. No." columns | ❓ |
| 550 | u16 | small numbers (record number?) | ❓ |

The 83 categories, in order (`xnet.CALL_CATEGORIES`): Local Coin; IntraState
IntraLata, IntraState InterLata, InterState IntraLata, InterState InterLata Coin;
Local Non Coin; the same four Non Coin; 211, 311 … 911; Incoming Call; Speed Dial
\*81–\*89, \*80, \*91, \*92; (1)555-XXXX; (1)NPA-555-XXXX; 10XXX; (1)800-NXX-XXXX;
PBX; Free Number; 950-XXXX; 0-; 00; 0+7; 0+10; Country Code #1–#9 and Dft; Card
Group 1–15; \*1 - \*7; ReRoute #1–15. (ExpressNet's own labels have two typos,
"IntrerLata" and a second "Local Coin" where Local Non Coin is meant.) Checked over
a real installation: 911, non-coin and incoming calls carry no money, local coin
calls average 14¢, toll-free calls carry minutes but no money.

## CDR/AUDITCDR.DAT — variable length, one poll's batch of calls

| Off | Size | Field | |
|---|---|---|---|
| 1, 8, 17, 28 | | group, site no, phone, timestamp | ✅ |
| 32 | 9 | date `yyyymmdd` | ✅ |
| 41 | 1 | number of calls *n* | ✅ (record size = 42 + 47·n in 99.6 % of records) |
| 42 + 47·k | 47 | call entry *k* | |

Call entry: `dialed[17]` ✅, `date yyyymmdd[9]` ✅, `time hhmm[5]` ✅, then 16
bytes of detail, named after the columns of ExpressNet's audit export:

| Off in entry | Size | Field | |
|---|---|---|---|
| 31 | u32 | duration, seconds | ✅ ExpressNet's audit report prints it as "%lu sec" |
| 35 | u32 | amount charged, cents ("Amt. Chg.") | ✅ values 0 / 50 / 100 / 200 … |
| 39 | u32 | amount collected, cents ("Amt. Col.") | ✅ |
| 43 | u8 | cost band | 🟡 |
| 44 | u8 | always 0 | |
| 45 | u8 | route type ("Route") | 🟡 mostly 3; 0 for 911 / free calls |
| 46 | u8 | always 0 | |

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
| 1276 | 2 | u16, copied into ExpressNet's call set-up with the code below; same value on every site seen | ❓ |
| 1278 | 2 | **security code** sent with `Q`: packed BCD in reading order (`1234` = bytes 12 34). Equals the site's options-record access code on every real site checked | ✅ |
| rest | | flags, poll status, contact fields … | ❓ |

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
| LOCAL/PORTS.DAT | 313 | port no u8 @1, modem name [21] @3, description [41] @24, init [41] @65, answer [41] @106, three dial strings [21] @147/168/189, reset [21] @210, line number [14] @232 ✅; PBX code for local calls [12] @246 and for long distance [12] @258 🟡 (order of ExpressNet's port editor; empty in the files seen, and two old records keep leftovers of an earlier layout after the NUL); polling speed (bps for outgoing calls, e.g. "1200") [5] @270 🟡; a second speed [5] @275, "9600" in every port seen, probably the PC-to-modem speed ❓; parity, data bits, stop bits as three characters @308 (e.g. `N82`) 🟡; u8 @311 and @312 (1 in every port seen; the editor's incoming/outgoing/Protel-modem switches are candidates) ❓ |
| PARAM/MODEMS.DAT | 230 | name [21] @1, description [41] @22, init [41] @63, answer [41] @104, dial ×3 [21] @145/166/187, reset [21] @208 ✅ |
| LOCAL/GROUPS.DAT | 22 | group number [7] @1 ✅; poll list: byte 8 = 1 and byte 11 = the list's number when the group is in a poll list, else zeros 🟡 (in a real installation the groups marked for list 2 are exactly the groups of that list's failed-call file `POLL0002.BAD`); byte 21 is `0x20` or `*` ❓ |
| LOCAL/POLLIST.DAT | 231 | name [13] @1, description [61] @14, start time `hh:mm` @79 ✅; list number u32 @75 🟡; seven u16 day flags @91, Sunday first 🟡 (order of ExpressNet's poll-list editor; nightly lists have all seven set); a timestamp (u32 minutes) @109 and more u16 flags from 113 ❓ |
| PARAM/CALLCOST.DAT | 36781 | name [13] @1 ✅; a security code in packed BCD @4762 (equals a site's code in 35 of 42 real records; ExpressNet can send it with `Q` instead of the site's) ✅; the two bytes ExpressNet sends with the clear-records commands `T`/`K`: @2499 for phones whose type starts with D, @4769 for the others (read there by ExpressNet ✅; zero in every record seen); rate tables ❓ |
| PARAM/MISCOPT.DAT | 420 | options record, see below |
| SITES/OPTNCNFG, OPTNHIST, OPTNDESC | | ❓ |
| LOCAL/POLLnnnn.BAD | 27 per entry, no header | phones a run of poll list *nnnn* could not reach, one entry each: u8 @0 (0), phone [11] @1, group [7] @12, u16 position in the run @19, u16 @21 (0), u16 poll-list number @23 (matches *nnnn*), u16 @25 (6 in every entry seen: perhaps the error code, 6 = no carrier) 🟡. Plain file, not c-tree. Not imported. |
| LOCAL/TRACE/TRACEn.TXT | text | ExpressNet's own log: an hourly time stamp and internal error lines with source file and line. No line traffic. |
| LOCAL/REPCTL, RPTQUE, PRINTERS, EDMANPOL | | ❓ (reports, printers, manual polls) |
| PARAM/SOFTWARE/DD*.* | | phone firmware as ready-made `C` download frames, one per CR LF line after a text header `name size blocks`; frame layout in docs/PROTOCOL.md ✅ |

## PARAM/MISCOPT.DAT — 420 bytes, phone options ("options record")

Each site names one options record (SITES.DAT @112). ExpressNet's options
editor (EDITMISC.C) holds the phone's numbers, call-in windows, report schedule,
coin settings and the security codes the phone asks for.

| Off | Size | Field | |
|---|---|---|---|
| 1 | 13 | name | ✅ |
| 14 | 61 | description | ✅ |
| 89 | 5 | security access code, 4 digits + NUL | 🟡 order follows the editor's fields |
| 94 | 5 | coin collection code (\*#1) | 🟡 |
| 99 | 5 | coin box amount code (\*#4, \*#5) | 🟡 |
| 104 | 5 | CDR retrieval code | 🟡 |
| 109 | 5 | Credit Express code | 🟡 |
| 116, 131 | 11 each | phone numbers, nearly always blank | ❓ |
| 319 | 11 | an 11-digit number, the same in every record of a real installation | ❓ probably one of the editor's numbers (reporting, validation, reroute, PBX) |
| rest | | call-in windows, report schedule, cash box volume, coin settings, display messages … | ❓ |

The codes are present in 50 of 52 records of a real installation and differ
between the five fields in 46, so each field holds its own code.

## Known damage patterns

Real installations ran for decades on FAT disks. One real `AUDITCDR.DAT`
contained ~19.7 MB of unrelated 32-bit Windows program code (cross-linked
clusters). The variable-length reader skips such stretches and resumes at the
next valid block; the importer reports how much was skipped and where.
