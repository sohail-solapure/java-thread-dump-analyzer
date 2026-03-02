#!/usr/bin/env bash
set -euo pipefail

# Project root: this script must be run from the project root directory
# Usage: bash scripts/setup.sh

PY=${PYTHON:-python3}
VENV_DIR=.venv

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment at $VENV_DIR"
  $PY -m venv "$VENV_DIR"
fi

echo "Upgrading pip"
"$VENV_DIR/bin/python" -m pip install --upgrade pip

echo "Installing requirements"
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

echo "Setup complete. To run the server:\n  bash scripts/run.sh"
