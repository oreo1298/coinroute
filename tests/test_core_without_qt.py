"""The core must work without PySide6 installed (CLAUDE.md rule 6)."""

import subprocess
import sys
import textwrap

CORE = ["ctree", "fields", "scramble", "xnet", "importer", "report", "cli", "db", "demo"]


def run_without_qt(code: str) -> subprocess.CompletedProcess:
    # A None entry in sys.modules makes `import PySide6` raise ImportError, as if not installed.
    prelude = "import sys\nfor m in ('PySide6', 'shiboken6'):\n    sys.modules[m] = None\n"
    return subprocess.run(
        [sys.executable, "-c", prelude + textwrap.dedent(code)], capture_output=True, text=True
    )


def test_core_modules_import_without_pyside6():
    r = run_without_qt(f"""
        import importlib
        for m in {CORE!r}:
            importlib.import_module('coinroute.' + m)
        assert not any(k.startswith('PySide6') and sys.modules[k] is not None for k in sys.modules)
        print('ok')
    """)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


def test_gui_command_explains_missing_pyside6():
    r = run_without_qt("from coinroute.cli import main\nmain(['gui'])")
    assert r.returncode != 0
    assert "pip install 'coinroute[gui]'" in r.stderr


def test_core_sources_have_no_top_level_qt_imports():
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent / "src" / "coinroute"
    for m in CORE:
        tree = ast.parse((src / f"{m}.py").read_text())
        for node in tree.body:  # module level only; cli imports the GUI lazily
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not any(
                n.startswith(("PySide6", "shiboken6")) or n.startswith(".gui") or n == "gui" for n in names
            ), (m, names)
