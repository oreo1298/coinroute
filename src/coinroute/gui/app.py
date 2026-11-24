"""Start the desktop app: `coinroute gui [DB]`, `coinroute-gui [DB]` or `python -m coinroute.gui`."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication

from .. import __version__
from .mainwindow import MainWindow


def make_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv[:1])
    app.setOrganizationName("coinroute")
    app.setApplicationName("coinroute")
    app.setApplicationVersion(__version__)
    return app


def run(db_path: str | None = None) -> int:
    app = make_app()
    win = MainWindow()
    win.show()
    if db_path:
        win.open_database(db_path)
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="coinroute-gui", description="coinroute desktop app")
    ap.add_argument("db", nargs="?", help="route database to open (made by `coinroute import`)")
    a = ap.parse_args(argv)
    return run(a.db)


if __name__ == "__main__":
    sys.exit(main())
