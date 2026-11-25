"""The settings view. Headless (pytest-qt); synthetic demo data only."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from coinroute.gui.settingsview import MASK, SettingsView  # noqa: E402


def test_settings_view_hides_passwords_until_asked(qtbot, db):
    s = SettingsView()
    qtbot.addWidget(s)
    s.set_db(db)
    assert s.sys_fields["company"].text() == "Demo Payphone Co."
    assert s.sys_fields["data_end"].text().startswith("2026-05-02")
    m = s.users_model
    assert [m.index(i, 0).data() for i in range(m.rowCount())] == ["PROTEL", "OFFICE", "TECH"]
    assert m.index(0, 1).data() == MASK
    s.show_pw.setChecked(True)
    assert m.index(0, 1).data() == "PROTELX3"
    s.show_pw.setChecked(False)
    assert m.index(0, 1).data() == MASK
    assert s.ports_model.rowCount() == 2 and s.modems_model.rowCount() == 2
    assert s.polls_model.rowCount() == 3 and s.groups_model.rowCount() == 4
    assert s.rates_model.rowCount() == 3 and s.options_model.rowCount() == 2
    s.set_db(None)
    assert s.users_model.rowCount() == 0


def test_security_codes_hidden_until_asked(qtbot, db):
    s = SettingsView()
    qtbot.addWidget(s)
    s.set_db(db)
    m = s.options_model
    col = next(i for i, c in enumerate(m.columns) if c.key == "code_access")
    row = next(i for i, r in enumerate(m.rows) if r["name"] == "STANDARD")
    assert m.index(row, col).data() == "••••"
    s.show_codes.setChecked(True)
    assert m.index(row, col).data() == "4821"
    s.set_db(db)  # reopening hides them again
    assert m.index(row, col).data() == "••••"
