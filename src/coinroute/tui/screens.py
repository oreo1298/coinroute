"""coinroute's terminal screens, arranged like ExpressNet's menus.

Main menu: 1 Edit Site Record · 2 Call Costing Records · 3 Options & Registers · 4 Reports ·
5 Modems · 6 System Utilities · 7 File Utilities · 0 Exit. The submenus keep ExpressNet's
numbering; entries coinroute can't do yet say so instead of disappearing, so the layout stays
familiar. Descriptions and help are coinroute's own words.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from ..db import RouteDB
from ..fields import phone_display
from ..reports import coin_report_text, default_period, trouble_report_text
from ..xnet import SECURITY_CODES, TROUBLE_FLAGS, framing_text
from .widgets import POP, QUIT, Col, Form, Item, Menu, Message, Prompt, Screen, Section, Table, TextView

FMT = "%Y-%m-%d %H:%M"
NOT_YET = "coinroute can't do this yet. It is planned; see docs/ROADMAP.md."


def money(c) -> str:
    return "" if c is None else f"{c / 100:,.2f}"


def when(s: str | None) -> str:
    return _dt.datetime.strptime(s, FMT).strftime("%m/%d/%y %H:%M") if s else ""


def masked(v: str | None, show: bool) -> str:
    return (v or "") if show else ("••••" if v else "")


class Route:
    """Holds the open database (or none) and builds every screen from it."""

    def __init__(self, db_path: str | None = None):
        self.db: RouteDB | None = None
        self.path: str | None = None
        self.show_codes = False
        if db_path:
            self.open(db_path)

    # --- database -------------------------------------------------------------------------------

    def open(self, path: str) -> str | None:
        try:
            db = RouteDB(path)
        except Exception as e:  # noqa: BLE001 - shown to the user
            return f"Can't open {path}: {e}"
        if self.db:
            self.db.close()
        self.db, self.path = db, path
        remember_route(path)
        return None

    def heading(self) -> str:
        if not self.db:
            return "coinroute - no route open"
        company = (self.db.system() or {}).get("company") or ""
        end = self.db.meta().get("data_end")
        return f"coinroute - {company} - {Path(self.path).name}" + (f" - data to {when(end)[:8]}" if end else "")

    def need_db(self):
        if not self.db:
            return Message("No route open", "Open a route database first: File Utilities > Open Route "
                           "Database, or import an ExpressNet folder (File Utilities > Import).")
        return None

    # --- main menu ------------------------------------------------------------------------------

    def main_menu(self) -> Menu:
        def guarded(make):
            return lambda: self.need_db() or make()

        return Menu("MAIN MENU", [
            Item("Edit Site Record", "Look up a payphone site: location, codes, firmware, history",
                 guarded(self.site_picker)),
            Item("Call Costing Records Menu", "Rate records (call costing) and which sites use them",
                 guarded(self.costing_menu)),
            Item("Edit Options & Registers", "Options records: description and security codes",
                 guarded(self.options_list)),
            Item("Reports Menu", "Coin and trouble reports, coin collections", guarded(self.reports_menu)),
            Item("Modems Menu", "Ports, polling lists, manual polling and communication errors",
                 guarded(self.modems_menu)),
            Item("System Utilities Menu", "System parameters, ports and logins", guarded(self.system_menu)),
            Item("File Utilities Menu", "Open a route, import an ExpressNet folder, export CSV",
                 self.file_menu),
            Item("Exit", "Leave coinroute", lambda: QUIT),
        ], help="Press the item's number, or move with the arrow keys and press ENTER.\n"
                "ESC always goes back one screen. F1 shows help where there is some.")

    # --- 1. sites -------------------------------------------------------------------------------

    def site_picker(self, then=None, title="SELECT SITE") -> Table:
        s = self.db.site_summary(30)
        cols = [
            Col("Phone", "phone", 12),
            Col("Description", "description", 28),
            Col("Group", "group_no", 6),
            Col("Status", "status", 16),
            Col("In box", "cash_box_cents", 8, right=True, fmt=money),
        ]
        return Table(title, cols, s["sites"], on_enter=then or (lambda r: self.site_record(r["phone_digits"])),
                     search=True, note="")

    def site_record(self, phone: str) -> Form:
        s = self.db.site(phone)
        if s is None:
            return Message("Site", f"No site {phone_display(phone)}.")
        show = self.show_codes
        sections = [
            Section("Site", [
                ("Phone number", phone_display(phone)),
                ("Description", s.get("description") or "(site record deleted)"),
                ("Group", s.get("group_no")), ("Site number", s.get("site_no")),
                ("Installed", s.get("install_date")), ("Phone speed", s.get("speed")),
            ]),
            Section("Location", [
                ("Location", s.get("location_name")), ("Address", s.get("address")),
                ("City", s.get("city")), ("State", s.get("state")), ("ZIP", s.get("zip")),
            ]),
            Section("Records and codes", [
                ("Call costing record", (s.get("rate_record") or "") +
                 ("" if s.get("rate_record_found", True) else "  (not found)")),
                ("Options record", (s.get("options_record") or "") +
                 ("" if s.get("options_record_found", True) else "  (not found)")),
                ("Security code", masked(s.get("security_code"), show)),
                ("Firmware in phone", s.get("firmware_current")),
                ("Firmware to download", s.get("firmware_target")),
            ]),
            Section("History", [
                ("Good polls", f"{s['coin_polls']}  ({when(s['first_poll'])} to {when(s['last_poll'])})"
                 if s["coin_polls"] else "none"),
                ("Failed calls", s["comm_errors"]), ("Calls recorded", s["calls"]),
                ("Call accounting periods", s["call_accounting_periods"]),
            ]),
        ]

        def on_key(k):
            if k == "F3":
                return self.coin_history(phone)
            if k == "F4":
                return self.site_failures(phone)
            if k == "F5":
                self.show_codes = not self.show_codes
                return ("replace", self.site_record(phone))
            return None

        f = Form(f"SITE RECORD  {phone_display(phone)}", sections,
                 extra_keys=(("F3", "Coin polls"), ("F4", "Failures"), ("F5", "Show/hide codes")),
                 on_key=on_key)
        return f

    def coin_history(self, phone: str) -> Table:
        rows = list(reversed(self.db.coin_polls(phone)))
        codes = {c for c, *_ in TROUBLE_FLAGS}
        cols = [
            Col("Polled", lambda r: when(r["at"]), 14),
            Col("Coins", "coins_cents", 8, right=True, fmt=money),
            Col("In box", "cash_box_cents", 8, right=True, fmt=money),
            Col("Collected", "collected_cents", 9, right=True, fmt=money),
            Col("Lifetime", "coin_total_cents", 10, right=True, fmt=money),
            Col("Flags", lambda r: " ".join(c for c in r.get("flags", []) if c in codes), 20),
        ]
        return Table(f"COIN POLLS  {phone_display(phone)}", cols, rows)

    def site_failures(self, phone: str) -> Table:
        cols = [Col("When", lambda r: when(r["at"]), 14), Col("Code", "code", 4, right=True),
                Col("Failure", "message", 44)]
        rows = self.db.comm_errors_page(phone, limit=200)
        return Table(f"FAILED CALLS  {phone_display(phone)}", cols, rows,
                     more=lambda last: self.db.comm_errors_page(phone, after=last["key"], limit=200))

    # --- 2. call costing ------------------------------------------------------------------------

    def costing_menu(self) -> Menu:
        later = lambda: "The call costing tables inside rate records are not decoded yet; " + NOT_YET
        return Menu("CALL COSTING RECORDS MENU", [
            Item("Edit Speed Dials", "Speed dial numbers", later),
            Item("Edit Cost Bands", "Prices per cost band", later),
            Item("Edit NPA/NXX & Country Codes", "Area code and exchange tables", later),
            Item("Edit Surcharge, Discount & Holiday Tables", "Surcharges, discounts, holidays", later),
            Item("Edit Routes", "Call routing", later),
            Item("Edit Restricted Phone Numbers", "Numbers the phones refuse", later),
            Item("List Costing Records", "Every rate record and how many sites use it", self.rate_list),
            Item("Return", "Back to the main menu"),
        ])

    def rate_list(self) -> Table:
        cols = [Col("Record", "name", 14), Col("Sites using it", "sites", 14, right=True)]
        return Table("CALL COSTING RECORDS", cols, self.db.rate_records())

    # --- 3. options -----------------------------------------------------------------------------

    def options_list(self) -> Table:
        rows = self.db.options_records()
        show = self.show_codes
        cols = [Col("Record", "name", 13), Col("Description", "description", 26),
                Col("Sites", "sites", 5, right=True)]
        short = {"access": "Access", "coin_collection": "Collect", "coin_box_amount": "Box amt"}
        cols += [Col(short[key], lambda r, k=key: masked(r.get(f"code_{k}"), show), 7)
                 for key, _, _ in SECURITY_CODES[:3]]
        return Table("OPTIONS & REGISTERS", cols, rows, on_enter=self.options_record,
                     note="ENTER: all five codes", on_key=self._toggle(self.options_list),
                     extra_keys=(("F5", "Show/hide codes"),))

    def _toggle(self, rebuild):
        """F5: show or hide codes and passwords, then redraw the screen."""
        def on_key(k):
            if k == "F5":
                self.show_codes = not self.show_codes
                return ("replace", rebuild())
            return None
        return on_key

    def options_record(self, r: dict) -> Form:
        show = self.show_codes

        def on_key(k):
            if k == "F5":
                self.show_codes = not self.show_codes
                return ("replace", self.options_record(r))
            return None

        return Form(f"OPTIONS RECORD  {r['name']}", [
            Section("Record", [("Name", r["name"]), ("Description", r.get("description")),
                               ("Sites using it", r.get("sites"))]),
            Section("Security codes", [(label, masked(r.get(f"code_{key}"), show))
                                       for key, label, _ in SECURITY_CODES]),
        ], extra_keys=(("F5", "Show/hide codes"),), on_key=on_key)

    # --- 4. reports -----------------------------------------------------------------------------

    def reports_menu(self) -> Menu:
        return Menu("REPORTS MENU", [
            Item("One-Time-Only Reports", "Run a Coin or Trouble report for a period", self.one_time_menu),
            Item("Create/Edit Report Definitions", "Saved report settings", lambda: NOT_YET),
            Item("Print Reports", "Send reports to a printer (for now: save, then print the file)",
                 lambda: "Run a report, press F2 to save it as a text file, then print the file."),
            Item("Cross-Reference and Fault Reports", "Sites by group and by fault", self.group_report),
            Item("Edit Printer Configurations", "Printers", lambda: NOT_YET),
            Item("Coin Collections", "Coin boxes emptied, with the money taken out", self.collections),
            Item("Return", "Back to the main menu"),
        ])

    def one_time_menu(self) -> Menu:
        return Menu("ONE-TIME-ONLY REPORTS", [
            Item("Coin Report", "Coins per site for a period, with collections and cash in box",
                 lambda: self.period_prompt("coin")),
            Item("Trouble Report", "Trouble flags and phones that did not answer",
                 lambda: self.period_prompt("trouble")),
            Item("Return", "Back to the reports menu"),
        ])

    def period_prompt(self, kind: str) -> Prompt:
        a, b = default_period(self.db.meta().get("data_end"), 7 if kind == "coin" else 1)
        fields = [("From (YYYY-MM-DD HH:MM)", a), ("To (YYYY-MM-DD HH:MM)", b)]
        if kind == "trouble":
            fields.append(("Only these flags (blank = all)", ""))

        def done(v):
            try:
                start, end = sorted(_dt.datetime.strptime(x.strip(), FMT).strftime(FMT) for x in v[:2])
            except ValueError:
                return "Dates must look like 2026-05-02 01:00."
            company = (self.db.system() or {}).get("company")
            if kind == "coin":
                text = coin_report_text(self.db.coin_report(start, end), company)
            else:
                codes = [c.strip().upper() for c in v[2].replace(",", " ").split() if c.strip()]
                text = trouble_report_text(self.db.trouble_report(start, end, codes or None), company)
            return TextView(f"{kind.upper()} REPORT", text, save=lambda name: self.save_text(name, text))

        return Prompt(f"{kind.upper()} REPORT", fields, done)

    @staticmethod
    def save_text(name: str, text: str) -> str:
        if not name.strip():
            return "No file name given."
        p = Path(name.strip()).expanduser()
        try:
            p.write_text(text, encoding="utf-8")
        except OSError as e:
            return f"Can't save: {e}"
        return f"Saved to {p.resolve()}"

    def collections(self) -> Table:
        a, b = default_period(self.db.meta().get("data_end"), 90)
        rows = self.db.collections(a, b)
        cols = [Col("Emptied", lambda r: when(r["at"]), 14), Col("Phone", "phone_display", 12),
                Col("Description", "description", 26), Col("Collected", "collected_cents", 9, right=True,
                                                            fmt=money)]
        total = sum(r["collected_cents"] or 0 for r in rows)
        return Table("COIN COLLECTIONS (last 90 days of data)", cols, rows,
                     on_enter=lambda r: self.site_record(r["phone"]), note=f"Total {money(total)}")

    def group_report(self) -> Table:
        cols = [Col("Group", "group_no", 8), Col("Sites", "sites", 6, right=True),
                Col("Poll list", "poll_list", 12),
                Col("In group file", lambda r: "yes" if r["defined"] else "no", 13)]
        return Table("GROUPS", cols, self.db.group_table())

    # --- 5. modems ------------------------------------------------------------------------------

    def modems_menu(self) -> Menu:
        return Menu("MODEMS MENU", [
            Item("View Modem Status", "Each port and what it is doing", self.modem_status),
            Item("Auto Polling", "Polling lists and failed polls", self.auto_menu),
            Item("Manual Polling", "Call one phone now", self.manual_poll),
            Item("Modem Setup", "Modem profiles", self.modem_setup),
            Item("View Communication Errors", "Every failed call, newest first", self.failures),
            Item("Return", "Back to the main menu"),
        ])

    def modem_status(self) -> Table:
        cols = [Col("Port", "port_no", 4, right=True), Col("Modem", "modem_name", 10),
                Col("Line", "line_number", 14), Col("Speed", "polling_speed", 5, right=True),
                Col("Framing", "framing", 7), Col("Status", lambda r: "Idle", 20)]
        return Table("MODEM STATUS", cols, self.db.ports(),
                     on_enter=lambda r: f"Port {r['port_no']}: {framing_text(r.get('framing', ''))}, "
                                        f"polls at {r.get('polling_speed')} bps.")

    def auto_menu(self) -> Menu:
        return Menu("AUTO POLLING", [
            Item("Edit Polling List", "Polling lists, their start times and days", self.poll_lists),
            Item("Stop/Restart Auto Poll", "Start or stop the scheduled polling", self.autopoll_screen),
            Item("View/Repoll Polling Failures", "Recent failed calls", self.failures),
            Item("Group/Polling List Report", "Groups and their sites", self.group_report),
            Item("Return", "Back to the modems menu"),
        ])

    def poll_lists(self) -> Table:
        cols = [Col("No", "number", 3, right=True), Col("List", "name", 11), Col("Starts", "start_time", 6),
                Col("Days", "days_text", 22), Col("Groups", lambda r: " ".join(r["groups"]), 14),
                Col("Phones", lambda r: len(r["phones"]), 6, right=True)]
        return Table("POLLING LISTS", cols, self.db.poll_plan(),
                     on_enter=lambda r: r["description"] or r["name"])

    def autopoll_screen(self):
        if getattr(self, "auto", None) is None:
            self.auto = AutoPollScreen(self)
        return self.auto

    def manual_poll(self) -> Table:
        return self.site_picker(then=lambda r: self.poll_prompt(r["phone_digits"]),
                                title="MANUAL POLL - SELECT SITE")

    def poll_prompt(self, phone: str):
        from ..poller import PollStore, site_for_poll

        site = site_for_poll(self.db, phone)
        if site is None:
            return "This site has no site record, so it can't be polled."
        with PollStore(self.path) as st:
            port, baud = st.setting("port", "SIM"), st.setting("baud", "9600")

        def done(v):
            port, baud = v[0].strip() or "SIM", v[1].strip() or "9600"
            if not baud.isdigit():
                return "The speed must be a number, e.g. 9600."
            with PollStore(self.path) as st:
                st.set_setting("port", port)
                st.set_setting("baud", baud)
            return PollScreen(self, site, port, int(baud))

        return Prompt(f"MANUAL POLL  {phone_display(phone)}",
                      [("Modem port (SIM = simulated phone)", port), ("PC-to-modem speed", baud)], done,
                      note="e.g. /dev/ttyUSB0 or COM1. SIM tries the poll on a simulated phone.")

    def modem_setup(self) -> Table:
        cols = [Col("Modem", "name", 10), Col("Description", "description", 36), Col("Init", "init", 28)]
        return Table("MODEM SETUP", cols, self.db.modems())

    def failures(self) -> Table:
        cols = [Col("When", lambda r: when(r["at"]), 14), Col("Phone", "phone_display", 12),
                Col("Description", "description", 20), Col("Failure", "message", 26)]
        return Table("COMMUNICATION ERRORS", cols, self.db.failures_page(limit=200),
                     on_enter=lambda r: self.site_record(r["phone"]),
                     more=lambda last: self.db.failures_page(after=last["key"], limit=200))

    # --- 6. system utilities --------------------------------------------------------------------

    def system_menu(self) -> Menu:
        return Menu("SYSTEM UTILITIES MENU", [
            Item("Local System Configuration", "Communication ports", self.ports),
            Item("System Parameters", "Company, licence, install date", self.system_params),
            Item("Password Maintenance", "ExpressNet logins", self.users),
            Item("Return", "Back to the main menu"),
        ])

    def ports(self) -> Table:
        cols = [Col("Port", "port_no", 4, right=True), Col("Modem", "modem_name", 10), Col("Line", "line_number", 14),
                Col("Speed", "polling_speed", 5, right=True), Col("PC speed", "port_speed", 8, right=True),
                Col("Framing", "framing", 7), Col("PBX local", "pbx_local", 10)]
        return Table("LOCAL SYSTEM CONFIGURATION", cols, self.db.ports())

    def system_params(self) -> Form:
        s = self.db.system() or {}
        m = self.db.meta()
        return Form("SYSTEM PARAMETERS", [
            Section("System", [("Company", s.get("company")), ("Licence number", s.get("license_no")),
                               ("Installed", s.get("install_date"))]),
            Section("This route database", [("File", self.path), ("Imported from", m.get("source")),
                                            ("Imported", m.get("imported_at")),
                                            ("Data ends", when(m.get("data_end")))]),
        ])

    def users(self) -> Table:
        show = self.show_codes
        cols = [Col("Login", "user", 12), Col("Description", "description", 30),
                Col("Password", lambda r: masked(r["password"], show), 12)]
        return Table("PASSWORD MAINTENANCE", cols, self.db.users(), on_key=self._toggle(self.users),
                     extra_keys=(("F5", "Show/hide passwords"),))

    # --- 7. file utilities ----------------------------------------------------------------------

    def file_menu(self) -> Menu:
        rebuild = lambda: ("coinroute keeps its own database and never changes the ExpressNet "
                           "files, so there is nothing to rebuild: import the folder again instead.")
        return Menu("FILE UTILITIES MENU", [
            Item("Open Route Database", "Open a database made by an import", self.open_prompt),
            Item("Purge/Rebuild", "Not needed in coinroute", rebuild),
            Item("Import ExpressNet Data", "Read an ExpressNet folder into a new database", self.import_prompt),
            Item("Export Data", "Write every table to CSV files", lambda: self.need_db() or self.export_prompt()),
            Item("Return", "Back to the main menu"),
        ])

    def open_prompt(self) -> Prompt:
        def done(v):
            err = self.open(v[0].strip())
            return err or f"Opened {Path(self.path).name}."

        return Prompt("OPEN ROUTE DATABASE", [("Database file", self.path or "")], done)

    def import_prompt(self) -> Prompt:
        def done(v):
            from ..importer import import_xnet

            src, dst = v[0].strip(), v[1].strip()
            if not src or not dst:
                return "Give both the ExpressNet folder and a new database file."
            try:
                rep = import_xnet(src, dst)
            except Exception as e:  # noqa: BLE001 - shown to the user
                return f"Import failed: {e}"
            err = self.open(dst)
            return err or f"Imported {sum(rep.counts.values()):,} records into {dst} and opened it."

        return Prompt("IMPORT EXPRESSNET DATA", [("ExpressNet folder (XNET)", ""), ("New database file", "route.db")],
                      done, note="The ExpressNet folder is only read, never changed.")

    def export_prompt(self) -> Prompt:
        def done(v):
            from ..db import export_csv

            out = v[0].strip()
            if not out:
                return "Give a folder for the CSV files."
            try:
                files = export_csv(self.path, out)
            except Exception as e:  # noqa: BLE001
                return f"Export failed: {e}"
            return f"Wrote {len(files)} CSV files to {Path(out).resolve()}."

        return Prompt("EXPORT DATA", [("Folder for the CSV files", "export")], done)


class PollScreen(Screen):
    """One poll, run in the background; the steps appear as they happen."""

    def __init__(self, route: Route, site: dict, port: str, baud: int, start: bool = True):
        self.route, self.site, self.port, self.baud = route, site, port, baud
        self.title = f"POLLING  {phone_display(site['phone'])}  {site.get('description') or ''}".rstrip()
        self.lines: list[str] = []
        self.result: str | None = None
        self.done = False
        self.keys = (("", "Polling…"),)
        self.thread = None
        if start:
            import threading

            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self) -> None:
        from ..modem import UPMS1200, Modem, open_line
        from ..poller import Poller, PollStore, simulated_phone
        from ..simphone import SimModemLine

        line = None
        try:
            if self.port.upper() == "SIM":
                line = SimModemLine(simulated_phone(self.route.path, self.site))
                self.lines.append("(simulated phone: nothing is dialled)")
            else:
                line = open_line(self.port, self.baud)
            out = Poller(Modem(line, UPMS1200), progress=self.lines.append).poll(self.site)
            with PollStore(self.route.path) as st:
                st.record(self.site, out)
            st_ = out.status
            if out.ok and st_:
                self.result = (f"Poll complete. Box {st_.percent_full or 0}% full; flags: "
                               f"{' '.join(_flag_names(st_.flags)) or 'none'}. Saved.")
            else:
                self.result = f"Poll failed: {out.message} (code {out.code}). Saved."
        except Exception as e:  # noqa: BLE001 - shown on the screen
            self.result = f"Poll stopped: {e}"
        finally:
            if line is not None and hasattr(line, "close"):
                line.close()
            self.done = True
            self.keys = (("ESC", "Back"),)

    def draw(self, c, top: int, height: int) -> None:
        _, cols = c.size()
        w = min(cols - 2, 70)
        x = (cols - w) // 2
        c.fill(top, x, height, w, "normal")
        c.box(top, x, height, w, title=self.title[: w - 6])
        shown = self.lines[-(height - 5):]
        for i, ln in enumerate(shown):
            style = "bad" if ln.startswith("Failed") else "normal"
            c.put(top + 2 + i, x + 3, ln[: w - 6], style)
        if self.result:
            c.put(top + height - 2, x + 3, self.result[: w - 6], "good" if "complete" in self.result else "warn")

    def status(self) -> str:
        return "Done - ESC to go back" if self.done else f"Calling on {self.port}…"

    def key(self, k: str):
        if k == "ESC" and self.done:
            return POP
        return None


class AutoPollScreen(Screen):
    """Start and stop scheduled polling while the program is open (it keeps running in the
    background when you leave this screen)."""

    def __init__(self, route: Route):
        self.route = route
        self.title = "AUTO POLLING"
        self.lines: list[str] = []
        self.thread = None
        self.stop = None
        self.port = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    @property
    def keys(self):
        return (("ESC", "Back"), ("F2", "Stop" if self.running else "Start"), ("F3", "Run due lists now"))

    def start(self, port: str, baud: int) -> None:
        import threading

        from ..autopoll import AutoPoller
        from ..modem import open_line
        from ..poller import simulated_phone, site_for_poll
        from ..simphone import SimModemLine

        def line():
            if port.upper() != "SIM":
                return open_line(port, baud)
            with RouteDB(self.route.path) as db:
                return SimModemLine(simulated_phone(self.route.path, site_for_poll(db, ap.current)))

        stamp = lambda m: self.lines.append(f"{_dt.datetime.now():%H:%M}  {m}")
        ap = AutoPoller(self.route.path, line, progress=stamp)
        self.port = port
        self.stop = threading.Event()
        self.thread = threading.Thread(target=ap.run_forever, args=(self.stop,), daemon=True)
        self.thread.start()

    def draw(self, c, top: int, height: int) -> None:
        _, cols = c.size()
        c.fill(top, 0, height, cols, "normal")
        c.box(top, 0, height, cols, title=self.title)
        state = f"RUNNING on {self.port}" if self.running else "STOPPED"
        c.put(top + 1, 3, f"Auto polling: {state}", "good" if self.running else "title")
        plan = self.route.db.poll_plan() if self.route.db else []
        for i, pl in enumerate(plan[:4]):
            c.put(top + 2 + i, 5, f"{pl['name']:<12} {pl['start_time']:>5}  {pl['days_text']:<26} "
                  f"{len(pl['phones'])} phones"[: cols - 8], "normal")
        y = top + 3 + min(4, len(plan))
        for i, ln in enumerate(self.lines[-(height - (y - top) - 2):]):
            c.put(y + i, 3, ln[: cols - 6], "bad" if "fail" in ln.lower() else "normal")

    def status(self) -> str:
        return "Lists run on their days from their start time; F2 starts or stops"

    def key(self, k: str):
        if k == "F2":
            if self.running:
                self.stop.set()
                self.lines.append("stopping after the current call…")
                return None
            from ..poller import PollStore

            with PollStore(self.route.path) as st:
                port, baud = st.setting("port", "SIM"), st.setting("baud", "9600")

            def done(v):
                self.start(v[0].strip() or "SIM", int(v[1]) if v[1].strip().isdigit() else 9600)

            return Prompt("START AUTO POLLING", [("Modem port (SIM = simulated phones)", port),
                                                 ("PC-to-modem speed", baud)], done)
        if k == "F3" and self.running:
            self.lines.append("checking for due lists…")
            self.stop.set()
            self.thread.join(1)
            self.start(self.port, 9600)
            return None
        return super().key(k)


def _flag_names(flags: bytes) -> list[str]:
    from ..xnet import trouble_flags

    return trouble_flags(bytes(48) + bytes(flags))


def _config_file() -> Path:
    import os

    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "coinroute" / "tui.json"


def remember_route(path: str) -> None:
    """Note the route last opened, so `coinroute tui` with no file opens it again."""
    import json

    try:
        f = _config_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"last_route": str(Path(path).resolve())}), encoding="utf-8")
    except OSError:
        pass


def last_route() -> str | None:
    import json

    try:
        p = json.loads(_config_file().read_text(encoding="utf-8")).get("last_route")
    except (OSError, ValueError):
        return None
    return p if p and Path(p).is_file() else None


def make_app(db_path: str | None = None, remember: bool = False):
    from .app import App

    route = Route(db_path or (last_route() if remember else None))
    app = App(route.main_menu(), heading=route.heading())
    app.route = route
    orig = app.draw

    def draw(c):  # keep the heading current (opening or importing a route changes it)
        app.heading = route.heading()
        orig(c)

    app.draw = draw
    return app


def run(db_path: str | None = None) -> int:
    make_app(db_path, remember=True).run()
    return 0
