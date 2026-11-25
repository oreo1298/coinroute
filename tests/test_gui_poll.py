"""Poll now from the desktop app, against the simulated phone (headless)."""
import shutil

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QSettings  # noqa: E402

from coinroute.db import RouteDB  # noqa: E402
from coinroute.gui.mainwindow import MainWindow  # noqa: E402

PHONE = "2105550108"


def test_poll_now_saves_and_refreshes(qtbot, demo_db, tmp_path):
    db = tmp_path / "route.db"
    shutil.copy(demo_db, db)
    w = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(w)
    assert w.open_database(str(db))
    assert w.act_poll.isEnabled()
    with RouteDB(db) as r:
        before = len(r.coin_polls(PHONE))
    dlg = w.poll_site(PHONE)
    dlg.port.setCurrentText("SIM")
    with qtbot.waitSignal(dlg.polled, timeout=10000):
        dlg.go.click()
    assert "Poll complete" in dlg.result.text()
    assert "Send security code" in dlg.log.toPlainText()
    with RouteDB(db) as r:
        assert len(r.coin_polls(PHONE)) == before + 1
    qtbot.waitUntil(lambda: w.db is not None and w.db.meta()["data_end"] > "2026-05-02 23:59", timeout=5000)
    dlg.close()
