"""The main window: opening databases, site tabs, import and export.

Headless (pytest-qt); synthetic demo data only.
"""

import pytest

pytest.importorskip("PySide6.QtWidgets")

import shutil  # noqa: E402
import sqlite3  # noqa: E402

from PySide6.QtCore import QModelIndex, QSettings  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from coinroute.gui.importdlg import TEMP_SUFFIX, ImportJob  # noqa: E402
from coinroute.gui.mainwindow import MainWindow  # noqa: E402
from coinroute.gui.models import RowRole  # noqa: E402


@pytest.fixture
def window(qtbot, tmp_path):
    w = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(w)
    return w


def test_double_click_opens_site_tab(qtbot, window, demo_db):
    assert window.open_database(demo_db)
    assert "Data ends 02 May 2026" in window.end_label.text()
    assert "demo.db" in window.source_label.text()
    v = window.sites
    idx = v.proxy.index(3, 1)
    v.table.doubleClicked.emit(idx)
    assert window.tabs.count() == 3
    assert window.tabs.currentWidget().phone == idx.data(RowRole)["phone_digits"]
    v.table.doubleClicked.emit(idx)  # same site: no second tab
    assert window.tabs.count() == 3
    window._close_tab(2)
    assert window.tabs.count() == 2
    assert window.recent_files() == [str(demo_db.resolve())]


def test_open_rejects_non_database(qtbot, window, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a))
    bad = tmp_path / "notes.db"
    bad.write_text("hello")
    assert not window.open_database(bad)
    assert shown and window.db is None and window.stack.currentIndex() == 0


def test_main_window_import_replaces_existing_file(qtbot, window, demo_xnet, tmp_path):
    dest = tmp_path / "route.db"
    dest.write_text("old file")
    prog = window.start_import(ImportJob(demo_xnet, dest))
    qtbot.waitUntil(lambda: getattr(window, "last_result", None) is not None, timeout=60000)
    assert prog.outcome.temp_path == dest.with_name(dest.name + TEMP_SUFFIX)
    window.last_result.accept()  # "Open route"
    qtbot.waitUntil(lambda: window.db is not None, timeout=5000)
    assert window.db.path == dest
    assert not dest.with_name(dest.name + TEMP_SUFFIX).exists()
    assert len(window.sites.model.rows) == 20


def test_database_without_calls(qtbot, window, demo_xnet, tmp_path):
    from coinroute.importer import import_xnet

    import_xnet(demo_xnet, tmp_path / "nocalls.db", include_calls=False)
    assert window.open_database(tmp_path / "nocalls.db")
    window.open_site(window.sites.model.rows[0]["phone_digits"])
    d = window.tabs.currentWidget()
    assert d.calls.model.rowCount() == 0 and not d.calls.model.canFetchMore(QModelIndex())


def test_database_copy_is_not_modified(qtbot, window, demo_db, tmp_path):
    copy = tmp_path / "copy.db"
    shutil.copy(demo_db, copy)
    before = copy.read_bytes()
    window.open_database(copy)
    window.open_site(window.sites.model.rows[0]["phone_digits"])
    window.close_database()
    assert copy.read_bytes() == before
    assert sqlite3.connect(copy).execute("SELECT count(*) FROM sites").fetchone()[0] == 20


def test_export_csv_runs_on_a_worker(qtbot, window, demo_db, tmp_path, monkeypatch):
    done = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: done.append(a))
    window.open_database(demo_db)
    w = window.start_export(tmp_path / "csv", include_calls=True)
    qtbot.waitUntil(lambda: bool(done), timeout=30000)
    w.wait()
    assert (tmp_path / "csv/sites.csv").exists() and (tmp_path / "csv/audit_calls.csv").exists()
    assert w not in window._busy
