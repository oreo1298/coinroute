"""Editing sites from the desktop app (headless)."""
import shutil

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QSettings  # noqa: E402

from coinroute.db import RouteDB  # noqa: E402
from coinroute.gui.mainwindow import MainWindow  # noqa: E402

PHONE = "2105550108"


@pytest.fixture
def window(qtbot, demo_db, tmp_path):
    db = tmp_path / "route.db"
    shutil.copy(demo_db, db)
    w = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(w)
    assert w.open_database(str(db))
    return w, db


def test_edit_new_remove(window):
    w, db = window
    assert w.act_edit_site.isEnabled() and w.act_new_site.isEnabled()
    dlg = w.edit_site(PHONE)
    assert dlg.inputs["description"].text() == "AIRPORT BAGGAGE CLAIM"
    dlg.inputs["security_code"].setText("12x4")
    dlg.save()
    assert "4 digits" in dlg.error.text() and dlg.isVisible()        # stays open
    dlg.inputs["security_code"].setText("1234")
    dlg.inputs["description"].setText("AIRPORT GATE 4")
    dlg.save()
    with RouteDB(db) as r:
        s = r.site(PHONE)
    assert s["description"] == "AIRPORT GATE 4" and s["security_code"] == "1234"

    dlg = w.new_site()
    dlg.number.setText("512-555-0199")
    dlg.inputs["description"].setText("NEW SHELTER")
    dlg.inputs["rate_record"].setCurrentText("STANDARD-TX")
    dlg.save()
    assert dlg.saved_phone == "5125550199"
    assert any(r["phone_digits"] == "5125550199" for r in w.sites.model.rows)

    assert w.remove_site(PHONE, confirm=False)
    with RouteDB(db) as r:
        assert r.site(PHONE)["removed"]
