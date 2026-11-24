# coinroute

A modern replacement for **Protel ExpressNet 3**, the DOS program that manages
Protel smart payphones (7000/8000-series boards). Goal: run on Windows, Linux
and macOS, read an existing ExpressNet installation, and eventually poll and
program the phones itself through the Protel UPMS1200 modem.

> Working name. Not affiliated with Protel, Inc.

## Status

| Milestone | State |
|---|---|
| 1. Import an ExpressNet folder (C:\XNET) into SQLite | **working** — sites, coin polls, comm errors, call accounting, audit calls, settings, logins |
| 2. Qt desktop app: browse sites, polls, errors, reports | next |
| 3. Decode the remaining fields (status flags, rates, options, call details) | in progress — see [FORMATS.md](FORMATS.md) |
| 4. Payphone protocol + simulated payphone for bench tests | planned |
| 5. Polling service (nightly polls, incoming calls) via UPMS1200 | planned |
| 6. Programming: rates, options, firmware downloads | planned |

## Use

Python 3.11+, no extra packages for the importer.

```sh
pip install -e .                      # or: PYTHONPATH=src python -m coinroute ...
coinroute import /path/to/XNET route.db
coinroute summary route.db            # which phones answer, cash in box, failures
coinroute export-csv route.db out/    # every table as CSV (add --calls for call records)
coinroute users /path/to/XNET         # ExpressNet logins and passwords
coinroute sample /path/to/XNET s.zip  # small (~2 MB) development sample: recent + spread history,
                                      # dialed numbers masked, no logins/billing/program/indexes
```

The XNET folder is only read. Every imported row keeps the original record
bytes, so a later version can decode more fields from the same database.

## Docs

- [FORMATS.md](FORMATS.md) — ExpressNet file layouts
- [docs/ROADMAP.md](docs/ROADMAP.md) — milestones and the next one in detail
- [docs/REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md) — what is known about XNET.EXE and the protocol
- [CLAUDE.md](CLAUDE.md) — rules and commands for working on this repo

## Tools

- `tools/fake_upms.py` — a stand-in for the Protel UPMS1200 modem, to test
  ExpressNet (in a VM) or coinroute without the real modem. See its `--help`.
- `tools/re/ne.py` — helper for studying XNET.EXE (bring your own copy).

## Tests

```sh
pip install pytest
pytest
COINROUTE_REAL_XNET=/path/to/XNET pytest     # extra checks against a real installation
```

Tests use synthetic data only. **Never commit customer data**; `.gitignore`
blocks `*.DAT`, `*.IDX` and `*.db`.
