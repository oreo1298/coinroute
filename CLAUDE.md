# coinroute — working notes for Claude Code

## What this is

A modern replacement for **Protel ExpressNet 3** (DOS, 1993–1999), the program
payphone operators use to poll and program Protel smart payphones over a
dial-up modem (the Protel UPMS1200). coinroute must run on current Windows,
Linux and macOS, read an existing ExpressNet installation losslessly, and
eventually talk to the phones itself.

Owner: Andrew (GitHub `oreo1298`). Private repo. The first user is a payphone
operator moving off a Windows 98 PC; their ExpressNet folder is **not** in this
repo and must never be.

Decisions already made: **Python 3.11+**, **Qt desktop app (PySide6)**, polling
will later run as a background service. Milestones 1 (importer) and 2 (desktop
app) are done; work continues per [docs/ROADMAP.md](docs/ROADMAP.md).

## Hard rules

1. **No customer data in git, ever** — no `*.DAT`/`*.IDX`, no imported `*.db`,
   no CSV exports, and no real phone numbers, site names, addresses, company
   names or passwords from a real installation in code, tests, docs, commit
   messages or PR text. Tests use synthetic data (`tests/fakexnet.py`: 555
   numbers, made-up places). If someone uploads real data to a session, use it
   only to check decoding, then report findings in general terms.
2. **No Protel files in git** — not `XNET.EXE`, `RTM.EXE`, `DPMI16BI.OVL`,
   phone firmware (`PARAM/SOFTWARE/*`), manuals or disk images. Don't paste
   disassembly listings of Protel code into the repo; describe formats and
   behaviour in our own words (`FORMATS.md`, `docs/`).
3. The importer **only reads** an ExpressNet folder. Never write to it.
4. Every imported table keeps the **original record bytes** (`raw` columns) so
   later decoding needs no re-import.
5. When a field layout is learned or its confidence changes, update
   `FORMATS.md` (✅ verified · 🟡 probable · ❓ unknown) in the same commit.
6. The core (`ctree`, `fields`, `scramble`, `xnet`, `importer`, `report`, `cli`,
   `db`, `demo`, `protocol`, `simphone`, `modem`, `reports`) stays **standard-library only** and must not import Qt
   (`tests/test_core_without_qt.py` checks). GUI code lives in
   `src/coinroute/gui/`.

## Layout

```
src/coinroute/
  ctree.py      c-tree fixed + variable-length record readers (skips damaged stretches)
  fields.py     C strings, u16/u32, ExpressNet timestamps (u32 minutes since 1899-12-31)
  scramble.py   X3SYSPAR XOR, password encode/decode/check (with the original's quirks)
  xnet.py       record decoders for each ExpressNet file + COMM_ERRORS table
  importer.py   XNET folder -> new SQLite db (schema at top of file)
  db.py         RouteDB: every read query (summary, per-site history, keyset paging, settings, CSV)
  report.py     text form of the route summary for the CLI
  reports.py    printable Coin and Trouble reports (HTML) from RouteDB.coin_report / trouble_report
  demo.py       synthetic XNET folder (make_demo) + the record builders tests use
  protocol.py   the phone link: frames, check byte, commands, Link (send/retry) -- docs/PROTOCOL.md
  simphone.py   a simulated payphone answering protocol.py, for tests and bench work
  modem.py      Hayes/UPMS1200 driver: reset, dial, answer, hang up; SerialLine; results -> COMM_ERRORS
  cli.py        coinroute import | summary | export-csv | users | demo | report | probe | gui
  tui/          terminal version laid out like ExpressNet: canvas (curses / text), widgets
                (menu, table, form, prompt), app (screen stack, key bar), screens (the menu tree)
  gui/          PySide6 app: mainwindow, sitesview, detail (+ chart), settingsview,
                importdlg, workers (QThread jobs), models (row + paged table models), style
tests/          fakexnet.py (tiny fixed folder), conftest.py (demo route fixtures); test_*.py,
                test_gui_*.py need pytest-qt
tools/fake_upms.py   stand-in UPMS1200 modem (serial or pty) for bench tests
tools/screenshots.py renders every GUI screen from the demo route to PNG
tools/tui_shots.py   renders the terminal screens to PNG (key sequences in SHOTS; add new screens)
tools/serial_tap.py  logs the bytes between ExpressNet and the UPMS1200 (both ways); --decode shows frames
tools/re/ne.py       NE-executable helper for studying XNET.EXE (never commit the EXE)
FORMATS.md           every known file layout, with confidence marks
docs/ROADMAP.md      milestones; Milestone 2 spec + acceptance list
docs/REVERSE_ENGINEERING.md   what is known about XNET.EXE (where its tables live)
docs/PROTOCOL.md     the PC-to-phone protocol so far, with confidence marks
```

## Commands

```sh
pip install -e '.[gui]' pytest pytest-qt      # PySide6 for the GUI milestone
pytest -q                                      # all tests, synthetic data only
QT_QPA_PLATFORM=offscreen pytest -q            # GUI tests in a headless session
COINROUTE_REAL_XNET=/path/to/XNET pytest -q    # extra checks; only where real data exists
PYTHONPATH=src python -m coinroute --help
coinroute demo /tmp/d && coinroute import /tmp/d/XNET /tmp/d.db && coinroute gui /tmp/d.db
coinroute tui /tmp/d.db                        # terminal version (Windows: pip install '.[tui]')
```

If Qt fails to load in a fresh container (`libEGL.so.1: cannot open shared
object file`), install the runtime libraries:
`apt-get install -y libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3`.

### Looking at the GUI without a screen

Sessions here are headless. Run Qt with `QT_QPA_PLATFORM=offscreen`, render a
window with `widget.grab().save("/tmp/shot.png")`, and open the PNG to check
layout, clipping and colours. Do this for every new screen before calling it
done; Andrew will also run it on his CachyOS desktop and report back.
`QT_QPA_PLATFORM=offscreen python tools/screenshots.py OUTDIR [--dark] [--kde-fallback]`
renders them all from the demo route; add new screens to it. `--kde-fallback`
mimics Andrew's desktop with pip's PySide6 (no KDE theme plugin, so Qt's
fallback palette and a larger font, which `gui/theme.py` replaces with Fusion).
Size dialogs from their contents (`importdlg.fit_to_contents`), not fixed
pixels: KWin gives unsized dialogs 640x480.

## Conventions

- ruff, line length 110. Type hints on public functions.
- Small, focused commits; tests in the same commit as the code they cover.
- Don't load huge tables into memory: `audit_calls` is ~2M rows on a real
  install. Page or stream (LIMIT/keyset, Qt `fetchMore`).
- Keep SQL in a data module, not in widgets, so the CLI and GUI share it.
- Big or risky choices (new dependency, schema change, file layout guess
  presented as fact): write it down in the PR description.
