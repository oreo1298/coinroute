"""Render every coinroute screen to PNG from the synthetic demo route (no real data).

    QT_QPA_PLATFORM=offscreen python tools/screenshots.py OUTDIR [--dark]

Writes main window, each site-detail tab, settings pages and the import dialogs.
Used to check layouts in headless sessions and for PR descriptions.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, QSettings, QStandardPaths, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPalette
from PySide6.QtWidgets import QApplication

from coinroute.demo import make_demo
from coinroute.gui.app import make_app
from coinroute.gui.importdlg import ImportDialog, ImportJob, ImportProgressDialog, ImportResultDialog
from coinroute.gui.mainwindow import MainWindow


def dark_palette() -> QPalette:
    p = QPalette()
    for role, c in [
        (QPalette.ColorRole.Window, "#202124"),
        (QPalette.ColorRole.WindowText, "#e8eaed"),
        (QPalette.ColorRole.Base, "#1a1a19"),
        (QPalette.ColorRole.AlternateBase, "#232322"),
        (QPalette.ColorRole.Text, "#e8eaed"),
        (QPalette.ColorRole.Button, "#2b2c2f"),
        (QPalette.ColorRole.ButtonText, "#e8eaed"),
        (QPalette.ColorRole.Highlight, "#3b6fb6"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.Mid, "#4a4b4f"),
        (QPalette.ColorRole.Midlight, "#3a3b3f"),
        (QPalette.ColorRole.ToolTipBase, "#2b2c2f"),
        (QPalette.ColorRole.ToolTipText, "#e8eaed"),
        (QPalette.ColorRole.PlaceholderText, "#9aa0a6"),
    ]:
        p.setColor(role, QColor(c))
    return p


def settle(app: QApplication, n: int = 5) -> None:
    for _ in range(n):
        app.processEvents()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--dark", action="store_true", help="render with a dark palette")
    ap.add_argument("--db", help="use this database instead of a fresh demo route")
    a = ap.parse_args(argv)
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    QStandardPaths.setTestModeEnabled(True)
    app = make_app(["screenshots"])
    if a.dark:
        app.setPalette(dark_palette())
    sfx = "-dark" if a.dark else ""
    tmp = Path(tempfile.mkdtemp(prefix="coinroute-shots-"))
    if a.db:
        db = Path(a.db)
    else:
        make_demo(tmp)
        db = tmp / "demo.db"
        job = ImportJob(tmp / "XNET", db)
        prog = ImportProgressDialog(job)
        prog.resize(520, prog.sizeHint().height())
        prog.show()
        prog._progress("CDR/AUDITCDR.DAT", 45000)
        prog.elapsed.setText("0:07")
        settle(app)
        prog.grab().save(str(out / f"import-progress{sfx}.png"))
        prog.hide()
        from coinroute.gui.importdlg import run_import

        outcome = run_import(job, lambda s, n: None)
        res = ImportResultDialog(outcome)
        res.resize(640, 560)
        res.show()
        settle(app)
        res.grab().save(str(out / f"import-result{sfx}.png"))
        res.close()
        dlg = ImportDialog(source=str(tmp / "XNET"))
        dlg.show()
        settle(app)
        dlg.grab().save(str(out / f"import-dialog{sfx}.png"))
        dlg.close()

    settings = QSettings(str(tmp / "settings.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings)
    win.resize(1360, 800)
    win.show()
    settle(app)
    win.grab().save(str(out / f"welcome{sfx}.png"))
    win.open_database(db)
    settle(app)
    win.grab().save(str(out / f"main-sites{sfx}.png"))

    # a filtered view: only the silent > 30 days chip
    win.sites.chips["dead"].click()
    settle(app)
    win.grab().save(str(out / f"main-sites-filtered{sfx}.png"))
    win.sites.chips[None].click()

    # site detail: the busiest healthy phone
    rows = win.sites.model.rows
    pick = max((r for r in rows if r["status_kind"] == "ok"), key=lambda r: r["coins_in_window_cents"] or 0)
    win.open_site(pick["phone_digits"])
    detail = win.tabs.currentWidget()
    settle(app)
    for i, name in enumerate(detail.TABS):
        detail.tabs.setCurrentIndex(i)
        settle(app)
        if name == "Coin polls":
            ch = detail.coins.chart
            pos = ch.rect().center() + QPoint(ch.width() // 5, 0)
            ev = QMouseEvent(
                QMouseEvent.Type.MouseMove,
                pos,
                ch.mapToGlobal(pos),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(ch, ev)
            settle(app)
        slug = name.lower().replace(" ", "-")
        win.grab().save(str(out / f"detail-{slug}{sfx}.png"))
    # a dead phone's comm errors
    dead = next((r for r in rows if r["status_kind"] == "dead" and r["failed_calls_in_window"]), None)
    if dead:
        win.open_site(dead["phone_digits"])
        d2 = win.tabs.currentWidget()
        d2.tabs.setCurrentIndex(2)
        settle(app)
        win.grab().save(str(out / f"detail-dead-comm-errors{sfx}.png"))

    win.tabs.setCurrentIndex(1)
    sv = win.settings_view
    for i, name in enumerate(sv.PAGES):
        sv.nav.setCurrentRow(i)
        settle(app)
        slug = name.lower().replace(" & ", "-").replace(" ", "-")
        win.grab().save(str(out / f"settings-{slug}{sfx}.png"))
    win.close()
    print(f"screenshots in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
