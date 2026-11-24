"""Build small synthetic ExpressNet folders for tests. No customer data.

The record builders live in coinroute.demo (so `coinroute demo` works without
pytest); this module keeps the tiny fixed folder the unit tests count on.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from coinroute.demo import (  # noqa: F401  (re-exported for tests)
    audit,
    callacct,
    cdr_header,
    coin,
    commerr,
    deleted,
    fixed_file,
    header,
    put,
    site,
    syspar,
    user,
    var_file,
)


def build(root: Path, *, damaged_audit=False) -> Path:
    """Make a small XNET folder: 2 phones, one answering, one dead."""
    x = root / "XNET"
    for d in ("SITES", "CDR", "LOCAL", "PARAM", "SYSTEM"):
        (x / d).mkdir(parents=True, exist_ok=True)
    end = dt.datetime(2026, 5, 2, 1, 5)
    (x / "SITES/SITES.DAT").write_bytes(fixed_file(2456, [site("7145550101", "COURT LOBBY"),
                                                          site("2105550199", "POOL PHONE", group="000200",
                                                               city="SHELBYVILLE", state="TX")], deleted=2))
    coins = [coin("7145550101", end - dt.timedelta(days=d), 100000 + 25 * (40 - d), 5000 + 25 * (40 - d))
             for d in range(40, -1, -1)]
    coins.append(coin("2105550199", end - dt.timedelta(days=100), 20000, 1500, group="000200"))
    (x / "CDR/COIN.DAT").write_bytes(fixed_file(88, coins, deleted=3))
    errs = [commerr("2105550199", end - dt.timedelta(days=d), 4, group="000200") for d in range(10)]
    errs.append(commerr("2105550199", end - dt.timedelta(days=50), 5, group="000200"))
    (x / "CDR/COMMERR.DAT").write_bytes(fixed_file(130, errs))
    t = dt.datetime(2026, 4, 30, 14, 0)
    auds = [audit("7145550101", end - dt.timedelta(days=1), [("5551212", t), ("911", t)]),
            audit("7145550101", end, [("5550000", t)])]
    (x / "CDR/AUDITCDR.DAT").write_bytes(var_file(auds, garbage_after=1 if damaged_audit else None))
    (x / "SYSTEM/X3SYSPAR.DAT").write_bytes(fixed_file(211, [syspar()]))
    (x / "SYSTEM/PASSWORD.DAT").write_bytes(fixed_file(83, [user("PROTEL", "SECRET12")]))
    return x
