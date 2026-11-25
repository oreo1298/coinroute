# Roadmap

| # | Milestone | State |
|---|---|---|
| 1 | Import an ExpressNet folder into SQLite (+ CLI, summary, CSV) | ✅ done |
| 2 | Qt desktop app over the imported data | ✅ done |
| 3 | Decode the remaining fields | **in progress** — trouble flags, call details, call accounting, security codes done |
| 4 | Payphone protocol + simulated payphone | planned |
| 5 | Polling service over the UPMS1200 | planned |
| 6 | Programming: rates, options, firmware downloads | planned |

---

## Milestone 2 — Qt desktop app (PySide6) ✅

Done. How it turned out, where it differs from the plan below:

- `src/coinroute/demo.py` + `coinroute demo`; the record builders moved there
  from `tests/fakexnet.py`.
- `src/coinroute/db.py` (`RouteDB`) holds all SQL; opened read-only; keyset
  paging for comm errors and calls (a real route's busiest phone has ~105k
  calls; pages come back in milliseconds).
- The coin chart is drawn with QPainter, not QtCharts: two panels on one time
  axis (cash in box, lifetime total) rather than two y-scales; lines break
  across gaps of more than 3 days; crosshair readout. No PySide6-Addons
  dependency.
- Site detail opens as a closable tab in the main window.
- Extras: status chips that filter, "Show removed sites" (phones whose site
  record was deleted but which once answered), `coinroute summary --removed`,
  `tools/screenshots.py`, dark-palette colours.
- Status thresholds (`db.OK_DAYS = 2`, `db.AMBER_DAYS = 30`) are measured from
  the end of the data, as in Milestone 1's summary.

The original plan:

Goal: an operator can open their imported route and see, without SQL, which
phones are working, what is in each cash box, and what went wrong.

### 2.0 Demo data first
Real data can't be in the repo, so build a richer synthetic installation to
develop and screenshot against:
- Extend `tests/fakexnet.py` (or add `src/coinroute/demo.py`, importable without
  pytest) with `make_demo(root, sites=20, days=400, seed=1)`: a few groups,
  phones that answer, phones that died at different dates (carrier time-outs,
  BUSY), cash boxes filling and being emptied, a few hundred calls per phone,
  one damaged stretch in AUDITCDR, deleted site records.
- CLI: `coinroute demo OUTDIR` writes `OUTDIR/XNET`.

### 2.1 App shell
- `coinroute gui [DB]` subcommand (imports Qt lazily) and a `coinroute-gui`
  gui-script entry point.
- Main window: File → Open database…, Import ExpressNet folder…, Export CSV…,
  Recent files (QSettings), Quit. Status bar shows data source and "data ends"
  date from `meta`.
- **Import** runs `import_xnet` in a worker thread (QThread or
  QThreadPool) with a progress dialog fed by the `progress` callback; the UI
  must not freeze. Afterwards show counts, missing files and warnings (e.g.
  skipped damaged bytes) in a dialog, then open the new database.

### 2.2 Sites (main view)
- Table of all sites from `report.route_summary()`: Group, Phone, Description,
  City/State, Status, Last good poll, In box, Coins (window), Failed calls
  (window), Main failure, Firmware.
- Status colouring: OK green; silent ≤ 30 days amber; longer red; never polled grey.
- Search box (phone/description/city), group filter, sortable columns,
  window-days spinner (default 30) that recomputes the summary.
- Totals row/strip: sites answering, total cash in boxes.

### 2.3 Site detail (double-click a site, or a side panel)
Tabs:
- **Info** — every decoded site field (rate record, options record, install
  date, location, firmware current/target).
- **Coin polls** — table, plus a chart of cash-in-box and lifetime coin total
  over time (QtCharts ships with PySide6; fall back to QPainter if missing).
  Mark days where the box was emptied (cash drops).
- **Comm errors** — table and counts by message.
- **Calls** — audit calls for this phone with a date-range filter. Lazy paging;
  never load all rows.
- **Call accounting** — the periods (counters are still raw; show hex for now).

### 2.4 Settings view
System record (company, licence, install date), ports, modems, poll lists,
groups, users (password hidden behind a "show" toggle).

### 2.5 Data module
`src/coinroute/db.py`: read-only query helpers used by both CLI and GUI
(sites summary, per-site polls/errors/calls with paging, settings). Move the SQL
out of `report.py` into it.

### Acceptance (all headless)
- [x] `coinroute demo /tmp/d && coinroute import /tmp/d/XNET /tmp/d.db` works.
- [x] `QT_QPA_PLATFORM=offscreen pytest -q` passes, including pytest-qt tests for:
      the sites model (rows, colours, filtering), the import worker end to end on
      demo data, the detail tabs populating, paging on the calls tab.
- [x] Screenshots of the main window, a site detail (each tab) and the import
      result dialog have been rendered to PNG and checked by eye; attach them to
      the PR description. (`docs/screenshots/`, made by `tools/screenshots.py`.)
- [x] Core modules still import without PySide6 installed
      (`tests/test_core_without_qt.py`).
- [x] README updated with how to start the GUI.

Later (not required for M2): PyInstaller build for Windows; printable reports
(Coin Report and Trouble Report layouts like ExpressNet's, as HTML/PDF).

---

## Milestone 3 — decode the remaining fields

Needs real data to verify, so this is done on Andrew's machine
(`COINROUTE_REAL_XNET`) or with data uploaded to a session (never committed).

**Done** (see FORMATS.md; method in REVERSE_ENGINEERING.md "Tables found so
far"): the 45 trouble flags (COIN.DAT bytes 48–56), amount taken out and record
counts in COIN.DAT, the 16 bytes of audit call detail, the 83 call-accounting
categories, and the five security codes in options records (MISCOPT.DAT). All
are decoded on read from the raw bytes, so old databases need no re-import.

**Still open:** the rest of COIN "PFlags" (57–63), the CALLACCT tail (544–551),
the comm-error port, the options record's phone numbers / call-in windows /
report schedule, poll-list weekday flags, port speeds and framing, rate tables
(CALLCOST), and the site record's binary areas. The leads below were written
before the work above; the trouble-flag lead is answered.

Original notes:

- **Trouble flags.** ExpressNet's Trouble Report prints per-poll codes: AE Call
  Acct Err, E$ Cash Emptied, $V Cash Vol Full, $F Coin Box Full, CG Coin Gate
  Fail, ME Coin Mech Err, DI Dial Inactive, EE Download Error, HG Handset Gone,
  HO Handset OffHk, DE Defect Escrow, FC Failed Comms, LB Low Battery, PC Peg
  Count, PR Phone Repair, RE Ram Error, RC Relay Collect, RJ Relay Jam, RR Relay
  Refund, ST Status Check, TR Time Report, TG Trouble Gone, SU Sig. Unit Bad.
  The 8 "status bytes" in COIN.DAT (offset 56) stayed constant for a phone
  whose printed flags changed day to day, so the daily flags are stored
  somewhere else — candidates: the audit call detail bytes, CALLACCT, the
  site record's binary areas, or the report files (LOCAL/REPCTL, RPTQUE).
  Printed reports in LOCAL/SPOOL are the reference to match against.
- **Audit call detail** — 16 bytes per call (duration? amount charged? coins?
  call type?). Compare against call-accounting totals and rate records.
- **Call accounting counters** (CALLACCT.DAT from offset 46). Category names
  used by ExpressNet's own export: Local Coin; IntraState/InterState ×
  IntraLata/InterLata, Coin and Non Coin; Incoming Call; Speed Dial *80–*89,
  *91, *92; (1)555-XXXX; (1)NPA-555-XXXX; 10XXX; (1)800-NXX-XXXX; PBX; Free
  Number; 950-XXXX; Country Code #1–#9 and Dft; Card Group 1–15; ReRoute #1–15.
- **Rate records** (PARAM/CALLCOST.DAT, 36,781-byte records), **options**
  (PARAM/MISCOPT.DAT, 420), SITES/OPTNCNFG/OPTNHIST/OPTNDESC, CDR/BILLCDR.DAT
  (variable length).
- **Site record** binary areas — notably the security code the phone expects
  (needed for Milestone 5), polling flags, last-status fields.

## Milestone 4 — protocol and a simulated payphone

From static analysis of XNET.EXE (see docs/REVERSE_ENGINEERING.md) plus
captures from `tools/fake_upms.py` logs. Deliverables: `docs/PROTOCOL.md`, and
`tools/fake_payphone.py` (or a mode of fake_upms) that answers like a Protel
7000-series board after CONNECT, so polling code can be tested end to end
without hardware. Confirm against a real phone before trusting it.

## Milestone 5 — polling service

pyserial; the UPMS1200 profile (init `ATM1L0E0Q0V1X3S10=255S0=0S9=2S7=100&D2`,
answer `ATA`, dial `ATDT`, reset `ATZ`, hang-up `+++` / `ATH0`); the poll-list
schedule (outgoing nightly, incoming window); incoming calls (RING → ATA);
writes results into the same database tables. Background service with the GUI
as a client. Must be testable against fake_upms + fake payphone.

## Milestone 6 — programming
Download rates/options to phones; firmware downloads (the DD*.* files are
Protel's — users supply their own).
