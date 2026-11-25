"""Shared fixtures. All data is synthetic (coinroute.demo / fakexnet)."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

# GUI tests run headless unless a display is available.
if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def demo_import(tmp_path_factory):
    """The default demo route (20 sites, 400 days), imported once: (db_path, ImportReport)."""
    from coinroute.demo import make_demo
    from coinroute.importer import import_xnet

    root = tmp_path_factory.mktemp("demo")
    make_demo(root)
    db = root / "demo.db"
    return db, import_xnet(root / "XNET", db)


@pytest.fixture(scope="session")
def demo_db(demo_import):
    return demo_import[0]


@pytest.fixture(scope="session")
def demo_xnet(tmp_path_factory):
    """A fresh demo XNET folder (not imported), for import tests."""
    from coinroute.demo import make_demo

    return make_demo(tmp_path_factory.mktemp("demo-src"))


@pytest.fixture
def db(demo_db):
    """The demo database opened read-only."""
    from coinroute.db import RouteDB

    d = RouteDB(demo_db)
    yield d
    d.close()


@pytest.fixture(autouse=True)
def _user_config(tmp_path, monkeypatch):
    """Settings the programs remember (e.g. the terminal version's last route) go to a temp folder."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
