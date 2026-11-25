"""Auto polling: run the poll lists on their schedule, like ExpressNet's Auto Polling.

Each poll list has a start time and weekdays (LOCAL/POLLIST.DAT); groups are assigned to lists
(LOCAL/GROUPS.DAT), and a list polls the sites of its groups one after another. A list is due
once a day, on its days, from its start time; failed phones are tried again at the end of the
run (`retries`). When each list last ran is kept in the route database's `settings` table, so
stopping and starting again doesn't repeat a run.

    stop = threading.Event()
    AutoPoller(db_path, lambda: SerialLine("/dev/ttyUSB0", 9600)).run_forever(stop)

Standard library only.
"""
from __future__ import annotations

import datetime as _dt
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from .db import RouteDB
from .modem import UPMS1200, Modem, Profile
from .poller import Poller, PollStore, site_for_poll

WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # datetime.weekday() order


def is_due(pl: dict, now: _dt.datetime, last_run: str | None) -> bool:
    """A list runs once per day, on its days, from its start time."""
    if not pl.get("phones") or WEEKDAY[now.weekday()] not in pl.get("days", []):
        return False
    try:
        h, m = (int(x) for x in (pl.get("start_time") or "").split(":")[:2])
    except ValueError:
        return False
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return now >= start and last_run != now.date().isoformat()


@dataclass
class RunReport:
    name: str
    ok: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (phone, message)


class AutoPoller:
    def __init__(self, db_path: str, open_line: Callable[[], object], *, profile: Profile = UPMS1200,
                 progress: Callable[[str], None] | None = None, retries: int = 1,
                 now: Callable[[], _dt.datetime] | None = None, poller_options: dict | None = None):
        self.db_path = str(db_path)
        self.open_line = open_line
        self.profile = profile
        self.progress = progress or (lambda s: None)
        self.retries = retries
        self.now = now or _dt.datetime.now
        self.poller_options = poller_options or {}
        self.current: str = ""

    def plan(self) -> list[dict]:
        with RouteDB(self.db_path) as db:
            return db.poll_plan()

    def due_lists(self) -> list[dict]:
        now = self.now()
        with PollStore(self.db_path) as st:
            return [pl for pl in self.plan() if is_due(pl, now, st.setting(f"autopoll_last_{pl['number']}") or None)]

    def run_list(self, pl: dict, stop: threading.Event | None = None) -> RunReport:
        rep = RunReport(pl["name"])
        todo = list(pl["phones"])
        with PollStore(self.db_path) as st:
            st.set_setting(f"autopoll_last_{pl['number']}", self.now().date().isoformat())
        for attempt in range(self.retries + 1):
            failed = []
            for phone in todo:
                if stop is not None and stop.is_set():
                    return rep
                ok, message = self.poll_one(phone, pl["name"])
                if ok:
                    rep.ok.append(phone)
                else:
                    failed.append((phone, message))
            todo = [ph for ph, _ in failed]
            rep.failed = failed
            if not todo:
                break
            if attempt < self.retries:
                self.progress(f"{pl['name']}: trying {len(todo)} failed phone(s) again")
        self.progress(f"{pl['name']}: {len(rep.ok)} polled, {len(rep.failed)} failed")
        return rep

    def poll_one(self, phone: str, list_name: str = "") -> tuple[bool, str]:
        with RouteDB(self.db_path) as db:
            site = site_for_poll(db, phone)
        if site is None:
            return False, "no site record"
        self.current = phone
        self.progress(f"{list_name}: polling {phone}")
        line = self.open_line()
        st = PollStore(self.db_path)
        try:
            opts = {"now": lambda: self.now().replace(second=0, microsecond=0), "store": st,
                    **self.poller_options}
            out = Poller(Modem(line, self.profile), **opts).poll(site)
            st.record(site, out)
        finally:
            st.close()
            if hasattr(line, "close"):
                line.close()
            self.current = ""
        self.progress(f"{list_name}: {phone} {out.message}")
        return out.ok, out.message

    def run_due(self, stop: threading.Event | None = None) -> list[RunReport]:
        return [self.run_list(pl, stop) for pl in self.due_lists() if not (stop and stop.is_set())]

    def run_forever(self, stop: threading.Event, check_every: float = 30.0) -> None:
        """Check for due lists every `check_every` seconds until `stop` is set."""
        self.progress("auto polling started")
        while not stop.is_set():
            self.run_due(stop)
            stop.wait(check_every)
        self.progress("auto polling stopped")
