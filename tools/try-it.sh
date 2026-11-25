#!/usr/bin/env bash
# Build and try coinroute on a desktop (Linux/macOS), from a git checkout.
#
#   bash tools/try-it.sh            update, install into .venv, run the tests, open the demo route
#   bash tools/try-it.sh --no-gui   ... but don't open the app at the end
#   bash tools/try-it.sh --shots    ... and also save screenshots of every screen as your desktop draws them
#   bash tools/try-it.sh --system-qt   use the distribution's PySide6 (sudo pacman -S pyside6): on KDE
#                                      that one loads Breeze; pip's PySide6 can't, so the app uses Fusion
#
# Everything goes into .venv inside the checkout (ignored by git) and a demo route in
# ~/.cache/coinroute-demo. Nothing is installed system-wide and no real data is touched.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mStopped: %s\033[0m\n' "$*" >&2; exit 1; }

# Update first, before reading the options: a newer version of this script may know options
# the old copy doesn't. If the pull changed the script, start the new one with the same options.
if [ -z "${COINROUTE_TRY_IT_UPDATED:-}" ]; then
  say "Updating $(git branch --show-current 2>/dev/null || echo checkout)"
  before=$(git hash-object tools/try-it.sh 2>/dev/null)
  git pull --ff-only || echo "(could not pull; using the copy you have)"
  if [ "$(git hash-object tools/try-it.sh 2>/dev/null)" != "$before" ]; then
    echo "try-it.sh itself was updated; restarting it"
    COINROUTE_TRY_IT_UPDATED=1 exec bash tools/try-it.sh "$@"
  fi
fi
git log --oneline -1

gui=1
shots=0
system_qt=0
for arg in "$@"; do
  case "$arg" in
    --no-gui) gui=0 ;;
    --shots) shots=1 ;;
    --system-qt) system_qt=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try: bash tools/try-it.sh --help)" >&2; exit 2 ;;
  esac
done

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || die "$PY not found. On Arch/CachyOS: sudo pacman -S python"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "coinroute needs Python 3.11 or newer"

venv_uses_system() { grep -qiE 'include-system-site-packages *= *true' .venv/pyvenv.cfg 2>/dev/null; }

say "Installing into .venv"
if [ "$system_qt" = 1 ]; then
  "$PY" -c 'import PySide6' 2>/dev/null || die "no system PySide6. Install it with: sudo pacman -S pyside6"
  if [ -d .venv ] && ! venv_uses_system; then
    echo "Recreating .venv to use the system PySide6"
    rm -rf .venv
  fi
  if [ ! -x .venv/bin/python ]; then
    "$PY" -m venv --system-site-packages .venv || die "could not create .venv"
  fi
fi
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv || die "could not create .venv"
fi
.venv/bin/python -m pip install -q --upgrade pip
if venv_uses_system && "$PY" -c 'import PySide6' 2>/dev/null; then
  .venv/bin/python -m pip install -q -e . pytest pytest-qt || die "pip install failed"
elif ! .venv/bin/python -m pip install -q -e '.[gui]' pytest pytest-qt; then
  # pip had no PySide6 for this Python: fall back to the distribution's package if it is there
  if "$PY" -c 'import PySide6' 2>/dev/null; then
    echo "pip could not install PySide6; using the system one instead"
    rm -rf .venv
    "$PY" -m venv --system-site-packages .venv || die "could not create .venv"
    .venv/bin/python -m pip install -q -e . pytest pytest-qt || die "pip install failed"
  else
    die "pip could not install PySide6. Install it with 'sudo pacman -S pyside6', then run this again"
  fi
fi
.venv/bin/python -c 'import PySide6, os; print("PySide6", PySide6.__version__, "from", os.path.dirname(PySide6.__file__))' \
  || die "PySide6 does not load"

say "Running the tests (headless)"
tests_ok=1
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q || tests_ok=0

say "Building the demo route"
demo="${XDG_CACHE_HOME:-$HOME/.cache}/coinroute-demo"
rm -rf "$demo"
mkdir -p "$demo"
.venv/bin/coinroute demo "$demo" >/dev/null || die "coinroute demo failed"
.venv/bin/coinroute import "$demo/XNET" "$demo/demo.db" 2>/dev/null | tail -3 || die "import failed"
.venv/bin/coinroute summary "$demo/demo.db" | head -2

if [ "$shots" = 1 ]; then
  say "Saving screenshots (windows will flash briefly)"
  out="$HOME/coinroute-shots"
  rm -rf "$out"
  .venv/bin/python tools/screenshots.py "$out" 2>/dev/null && echo "Saved in $out"
fi

say "Summary"
if [ "$tests_ok" = 1 ]; then echo "Tests: passed"; else echo "Tests: SOME FAILED (see above)"; fi
echo "Demo database: $demo/demo.db"
echo "Your own data: .venv/bin/coinroute gui, then File > Import ExpressNet folder..."
echo "Run again any time to test the newest version: bash tools/try-it.sh"

if [ "$gui" = 1 ]; then
  say "Opening the app on the demo route (close the window to finish)"
  .venv/bin/coinroute gui "$demo/demo.db"
fi
[ "$tests_ok" = 1 ]
