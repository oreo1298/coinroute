"""The import worker and dialogs. Headless (pytest-qt); synthetic demo data only."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QTimer  # noqa: E402

from coinroute.gui.importdlg import (  # noqa: E402
    ImportDialog,
    ImportJob,
    ImportProgressDialog,
    ImportResultDialog,
)


def test_import_worker_end_to_end_keeps_ui_responsive(qtbot, demo_xnet, tmp_path):
    job = ImportJob(demo_xnet, tmp_path / "route.db")
    dlg = ImportProgressDialog(job)
    qtbot.addWidget(dlg)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    steps = []
    dlg.worker.progress.connect(lambda s, n: steps.append(s))
    with qtbot.waitSignal(dlg.done, timeout=60000) as sig:
        dlg.start()
        dlg.open()
    timer.stop()
    outcome = sig.args[0]
    assert outcome.report.counts["SITES/SITES.DAT"] == 20
    assert outcome.report.counts["audit calls"] > 4000
    assert len(outcome.report.warnings) == 2
    assert "CDR/AUDITCDR.DAT" in steps and steps[-1] == "building indexes"
    assert len(ticks) >= 3  # the event loop kept running during the import
    assert (tmp_path / "route.db").exists() and outcome.temp_path is None

    res = ImportResultDialog(outcome)
    qtbot.addWidget(res)
    assert res.counts.rowCount() == len(outcome.report.counts)
    assert res.warnings.count() == 2


def test_import_cancel_leaves_nothing(qtbot, demo_xnet, tmp_path):
    job = ImportJob(demo_xnet, tmp_path / "route.db")
    dlg = ImportProgressDialog(job)
    qtbot.addWidget(dlg)
    dlg.worker.cancel()  # cancelled before the first progress call
    with qtbot.waitSignal(dlg.worker.cancelled, timeout=30000):
        dlg.start()
    dlg.worker.wait()
    assert not (tmp_path / "route.db").exists()
    assert dlg.outcome is None


def test_import_dialog_validates_source(qtbot, demo_xnet, tmp_path):
    dlg = ImportDialog(source=str(tmp_path / "nothing"))
    qtbot.addWidget(dlg)
    assert not dlg.import_btn.isEnabled()
    dlg.source.setText(str(demo_xnet.parent))  # the folder that contains XNET is fine
    assert dlg.import_btn.isEnabled()
    assert "13 of 13" in dlg.source_note.text()
    assert dlg.dest.text().endswith(".db")
    dlg.dest.setText(str(tmp_path / "x.db"))
    assert dlg.job().source == demo_xnet
