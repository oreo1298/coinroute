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
will later run as a background service. Milestone 1 (importer) is done; work
continues per [docs/ROADMAP.md](docs/ROADMAP.md).

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
6. The core (`ctree`, `fields`, `scramble`, `xnet`, `importer`, `report`, `cli`)
   stays **standard-library only** and must not import Qt. GUI code lives in
   `src/coinroute/gui/`.

## Layout

```
src/coinroute/
  ctree.py      c-tree fixed + variable-length record readers (skips damaged stretches)
  fields.py     C strings, u16/u32, ExpressNet timestamps (u32 minutes since 1899-12-31)
  scramble.py   X3SYSPAR XOR, password encode/decode/check (with the original's quirks)
  xnet.py       record decoders for each ExpressNet file + COMM_ERRORS table
  importer.py   XNET folder -> new SQLite db (schema at top of file)
  report.py     route health summary
  cli.py        coinroute import | summary | export-csv | users
tests/          fakexnet.py builds synthetic XNET folders; test_*.py
tools/fake_upms.py   stand-in UPMS1200 modem (serial or pty) for bench tests
tools/re/ne.py       NE-executable helper for studying XNET.EXE (never commit the EXE)
FORMATS.md           every known file layout, with confidence marks
docs/ROADMAP.md      milestones; Milestone 2 spec + acceptance list
docs/REVERSE_ENGINEERING.md   what is known about XNET.EXE and the payphone protocol
```

## Commands

```sh
pip install -e '.[gui]' pytest pytest-qt      # PySide6 for the GUI milestone
pytest -q                                      # all tests, synthetic data only
QT_QPA_PLATFORM=offscreen pytest -q            # GUI tests in a headless session
COINROUTE_REAL_XNET=/path/to/XNET pytest -q    # extra checks; only where real data exists
PYTHONPATH=src python -m coinroute --help
```

### Looking at the GUI without a screen

Sessions here are headless. Run Qt with `QT_QPA_PLATFORM=offscreen`, render a
window with `widget.grab().save("/tmp/shot.png")`, and open the PNG to check
layout, clipping and colours. Do this for every new screen before calling it
done; Andrew will also run it on his CachyOS desktop and report back.

## Conventions

- ruff, line length 110. Type hints on public functions.
- Small, focused commits; tests in the same commit as the code they cover.
- Don't load huge tables into memory: `audit_calls` is ~2M rows on a real
  install. Page or stream (LIMIT/keyset, Qt `fetchMore`).
- Keep SQL in a data module, not in widgets, so the CLI and GUI share it.
- Big or risky choices (new dependency, schema change, file layout guess
  presented as fact): write it down in the PR description.
