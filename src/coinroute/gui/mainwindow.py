"""Main window: menus, recent files, status bar, and the Sites / Settings / site tabs."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..db import NotARouteDatabase, RouteDB, export_csv
from ..fields import phone_display
from .detail import SiteDetail
from .importdlg import ImportDialog, ImportOutcome, ImportProgressDialog, ImportResultDialog, finish_import
from .reportsdlg import ReportsDialog
from .settingsview import SettingsView
from .sitesview import SitesView
from .style import muted_css
from .workers import Worker

MAX_RECENT = 8


def _nice_time(s: str | None) -> str:
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%d %H:%M").strftime("%d %b %Y %H:%M") if s else "—"
    except ValueError:
        return s or "—"


class Welcome(QWidget):
    """Shown while no database is open."""

    def __init__(self, win: MainWindow):
        super().__init__()
        self.win = win
        title = QLabel("<h2>coinroute</h2>")
        text = QLabel("Open a route database, or import an ExpressNet folder (C:\\XNET) to make one.")
        text.setWordWrap(True)
        open_btn = QPushButton("Open database…")
        open_btn.clicked.connect(win.choose_database)
        imp_btn = QPushButton("Import ExpressNet folder…")
        imp_btn.clicked.connect(win.choose_import)
        imp_btn.setDefault(True)
        self.recent_label = QLabel("Recent")
        self.recent_label.setStyleSheet(muted_css())
        self.recent = QListWidget()
        self.recent.setMaximumHeight(200)
        self.recent.itemActivated.connect(lambda it: win.open_database(it.data(Qt.ItemDataRole.UserRole)))

        box = QWidget()
        box.setMaximumWidth(560)
        v = QVBoxLayout(box)
        v.addWidget(title)
        v.addWidget(text)
        v.addSpacing(8)
        h = QHBoxLayout()
        h.addWidget(open_btn)
        h.addWidget(imp_btn)
        h.addStretch(1)
        v.addLayout(h)
        v.addSpacing(16)
        v.addWidget(self.recent_label)
        v.addWidget(self.recent)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(box, 3)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)

    def refresh(self, recent: list[str]) -> None:
        self.recent.clear()
        for p in recent:
            it = QListWidgetItem(f"{Path(p).name}    —    {Path(p).parent}")
            it.setData(Qt.ItemDataRole.UserRole, p)
            it.setToolTip(p)
            self.recent.addItem(it)
        self.recent.setVisible(bool(recent))
        self.recent_label.setVisible(bool(recent))


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings()
        self.db: RouteDB | None = None
        self._busy: list = []  # running dialogs/workers, kept alive
        self.setWindowTitle("coinroute")
        self.resize(1280, 780)

        self.sites = SitesView()
        self.sites.open_site.connect(self.open_site)
        self.settings_view = SettingsView()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.addTab(self.sites, "Sites")
        self.tabs.addTab(self.settings_view, "Settings")
        for i in (0, 1):
            self.tabs.tabBar().setTabButton(i, QTabBar.ButtonPosition.RightSide, None)
            self.tabs.tabBar().setTabButton(i, QTabBar.ButtonPosition.LeftSide, None)

        self.welcome = Welcome(self)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)

        self.source_label = QLabel()
        self.source_label.setStyleSheet("padding: 0 6px;")
        self.end_label = QLabel()
        self.end_label.setStyleSheet("padding: 0 6px;")
        self.statusBar().addWidget(self.source_label, 1)
        self.statusBar().addPermanentWidget(self.end_label)

        self._make_menus()
        geo = self.settings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self._show_db_state()

    # --- menus ---

    def _make_menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&File")
        self.act_open = self._action(
            f, "&Open database…", self.choose_database, QKeySequence.StandardKey.Open
        )
        self.act_import = self._action(f, "&Import ExpressNet folder…", self.choose_import, "Ctrl+I")
        self.act_export = self._action(f, "&Export CSV…", self.choose_export, "Ctrl+E")
        self.act_reports = self._action(f, "&Reports…", self.open_reports, "Ctrl+R")
        self.recent_menu = f.addMenu("Recent &files")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        f.addSeparator()
        self.act_close = self._action(f, "&Close database", self.close_database, "Ctrl+Shift+W")
        f.addSeparator()
        self._action(f, "&Quit", self.close, QKeySequence.StandardKey.Quit)

        v = mb.addMenu("&View")
        self._action(v, "&Sites", lambda: self.tabs.setCurrentIndex(0), "Ctrl+1")
        self._action(v, "S&ettings", lambda: self.tabs.setCurrentIndex(1), "Ctrl+2")
        self._action(v, "&Refresh", self.refresh, QKeySequence.StandardKey.Refresh)
        self._action(
            v,
            "Close site &tab",
            lambda: self._close_tab(self.tabs.currentIndex()),
            QKeySequence.StandardKey.Close,
        )

        h = mb.addMenu("&Help")
        self._action(h, "&About coinroute", self.about)

    def _action(self, menu, text, slot, shortcut=None) -> QAction:
        a = QAction(text, self)
        if shortcut is not None:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(lambda _=False: slot())
        menu.addAction(a)
        return a

    # --- recent files ---

    def recent_files(self) -> list[str]:
        v = self.settings.value("recent", [])
        if isinstance(v, str):
            v = [v]
        return [p for p in (v or []) if Path(p).is_file()]

    def _remember(self, path: Path) -> None:
        p = str(path.resolve())
        recent = [p] + [r for r in self.recent_files() if r != p]
        self.settings.setValue("recent", recent[:MAX_RECENT])
        self.welcome.refresh(self.recent_files())

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self.recent_files()
        for p in recent:
            a = self.recent_menu.addAction(f"{Path(p).name}  —  {Path(p).parent}")
            a.triggered.connect(lambda _=False, p=p: self.open_database(p))
        if not recent:
            self.recent_menu.addAction("(none)").setEnabled(False)
        else:
            self.recent_menu.addSeparator()
            clear = self.recent_menu.addAction("Clear list")
            clear.triggered.connect(lambda: (self.settings.setValue("recent", []), self.welcome.refresh([])))

    # --- database ---

    def choose_database(self) -> None:
        start = self.recent_files()[0] if self.recent_files() else ""
        f, _ = QFileDialog.getOpenFileName(
            self,
            "Open route database",
            str(Path(start).parent) if start else "",
            "coinroute database (*.db);;All files (*)",
        )
        if f:
            self.open_database(f)

    def open_database(self, path) -> bool:
        try:
            db = RouteDB(path)
        except (FileNotFoundError, NotARouteDatabase) as e:
            QMessageBox.warning(self, "Can't open database", str(e))
            return False
        self.close_database()
        self.db = db
        self.sites.set_db(db)
        self.settings_view.set_db(db)
        self._remember(Path(path))
        self._show_db_state()
        self.tabs.setCurrentIndex(0)
        self.sites.table.setFocus()
        return True

    def close_database(self) -> None:
        while self.tabs.count() > 2:
            self._close_tab(2)
        if self.db:
            self.sites.set_db(None)
            self.settings_view.set_db(None)
            self.db.close()
            self.db = None
        self._show_db_state()

    def refresh(self) -> None:
        if self.db:
            self.open_database(self.db.path)

    def _show_db_state(self) -> None:
        has = self.db is not None
        self.stack.setCurrentIndex(1 if has else 0)
        self.act_export.setEnabled(has)
        self.act_reports.setEnabled(has)
        self.act_close.setEnabled(has)
        if not has:
            self.setWindowTitle("coinroute")
            self.source_label.setText("No database open")
            self.end_label.setText("")
            self.welcome.refresh(self.recent_files())
            return
        meta = self.db.meta()
        self.setWindowTitle(f"{self.db.path.name} — coinroute")
        src = meta.get("source", "?")
        imported = (meta.get("imported_at") or "")[:10]
        self.source_label.setText(
            f"{self.db.path.name}  ·  data from {src}" + (f", imported {imported}" if imported else "")
        )
        self.source_label.setToolTip(str(self.db.path.resolve()))
        self.end_label.setText(f"Data ends {_nice_time(meta.get('data_end'))}")

    # --- site tabs ---

    def open_site(self, phone: str) -> None:
        if not self.db:
            return
        for i in range(2, self.tabs.count()):
            if getattr(self.tabs.widget(i), "phone", None) == phone:
                self.tabs.setCurrentIndex(i)
                return
        w = SiteDetail(self.db, phone)
        i = self.tabs.addTab(w, phone_display(phone))
        self.tabs.setTabToolTip(i, w.title)
        self.tabs.setCurrentIndex(i)

    def _close_tab(self, i: int) -> None:
        if i >= 2:
            w = self.tabs.widget(i)
            self.tabs.removeTab(i)
            w.deleteLater()

    # --- import / export ---

    def choose_import(self) -> None:
        last = self.settings.value("import/last_source", "")
        dlg = ImportDialog(self, source=last or "")
        if dlg.exec() != ImportDialog.DialogCode.Accepted:
            return
        job = dlg.job()
        self.settings.setValue("import/last_source", str(job.source))
        self.start_import(job)

    def start_import(self, job) -> ImportProgressDialog:
        prog = ImportProgressDialog(job, self)
        self._busy.append(prog)
        prog.done.connect(self._import_done)
        prog.finished.connect(lambda _=0: self._busy.remove(prog) if prog in self._busy else None)
        prog.start()
        prog.open()
        return prog

    def _import_done(self, outcome: ImportOutcome) -> None:
        if outcome.temp_path is not None and self.db and self.db.path.resolve() == outcome.job.dest.resolve():
            self.close_database()  # it is about to be replaced
        try:
            path = finish_import(outcome)
        except OSError as e:
            QMessageBox.critical(self, "Import failed", f"Could not replace {outcome.job.dest}:\n{e}")
            return
        res = ImportResultDialog(outcome, self)
        self.last_result = res
        res.finished.connect(lambda _=0: self.open_database(path))
        res.open()

    def open_reports(self, kind: str = "coin") -> ReportsDialog | None:
        if not self.db:
            return None
        dlg = ReportsDialog(self.db, self, kind)
        self.reports_dialog = dlg
        dlg.open()
        return dlg

    def choose_export(self) -> None:
        if not self.db:
            return
        d = QFileDialog.getExistingDirectory(
            self, "Export CSV files to folder", str(self.settings.value("export/last_dir", ""))
        )
        if not d:
            return
        self.settings.setValue("export/last_dir", d)
        n = self.db.calls_total()
        r = QMessageBox.question(
            self,
            "Include calls?",
            "Also export individual call records (audit_calls.csv)?"
            + (f"\n\nThat is {n:,} rows; the file will be large." if n else ""),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if r == QMessageBox.StandardButton.Cancel:
            return
        self.start_export(Path(d), r == QMessageBox.StandardButton.Yes)

    def start_export(self, outdir: Path, include_calls: bool) -> Worker:
        db_path = self.db.path
        worker = Worker(lambda progress: export_csv(db_path, outdir, include_calls, progress))
        dlg = QProgressDialog("Exporting…", "Cancel", 0, 0, self)
        dlg.setWindowTitle("Export CSV")
        dlg.setMinimumDuration(300)
        dlg.setMinimumWidth(420)
        dlg.canceled.connect(worker.cancel)
        worker.progress.connect(
            lambda t, n: dlg.setLabelText(f"Writing {t}.csv" + (f" — {n:,} rows" if n else ""))
        )

        def done(paths):
            dlg.reset()
            QMessageBox.information(self, "Export finished", f"Wrote {len(paths)} CSV files to\n{outdir}")

        def failed(msg, tb):
            dlg.reset()
            QMessageBox.critical(self, "Export failed", msg)

        worker.finished.connect(done)
        worker.failed.connect(failed)
        worker.cancelled.connect(dlg.reset)
        for sig in (worker.finished, worker.failed, worker.cancelled):
            sig.connect(lambda *_: self._busy.remove(worker) if worker in self._busy else None)
        self._busy.append(worker)
        worker.start()
        return worker

    # --- misc ---

    def about(self) -> None:
        QMessageBox.about(
            self,
            "About coinroute",
            f"<h3>coinroute {__version__}</h3>"
            "<p>A payphone route manager that reads Protel ExpressNet 3 data.</p>"
            "<p>Not affiliated with Protel, Inc.</p>",
        )

    def closeEvent(self, event) -> None:
        for b in list(self._busy):
            w = b.worker if isinstance(b, ImportProgressDialog) else b
            w.cancel()
            w.wait()
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.close_database()
        super().closeEvent(event)
