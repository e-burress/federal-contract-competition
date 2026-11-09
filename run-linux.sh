#!/usr/bin/env bash
# Runs the quick rebuild, then keeps the window open. Nothing is installed without you typing it.
cd "$(dirname "$0")" || exit 1
echo "Federal contract competition: quick rebuild"
echo

finish() {
  echo
  read -r -p "Press Enter to close this window. " _
}

if command -v uv >/dev/null 2>&1; then
  uv run run.py quick
  finish; exit 0
fi

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done

if [ -z "$PY" ]; then
  echo "Neither uv nor Python 3.11 or newer was found. Nothing was installed."
  echo
  echo "Option 1, install uv. Type this in a terminal:"
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "Option 2, install Python 3.11 or newer with your package manager or from https://www.python.org/downloads/"
  echo
  echo "Then close this window and run this file again."
  finish; exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating a private Python environment in .venv, first time only..."
  if ! "$PY" -m venv .venv || ! .venv/bin/python -m pip install -r requirements.txt; then
    echo
    echo "Setup failed. See the messages above. On Debian or Ubuntu you may need: sudo apt install python3-venv"
    echo "Delete the .venv folder before trying again."
    finish; exit 1
  fi
fi
.venv/bin/python run.py quick
finish
