#!/usr/bin/env bash
set -euo pipefail

# Usage: bash scripts/run.sh

VENV_BIN=".venv/bin"
UVICORN_BIN="$VENV_BIN/uvicorn"
PY_BIN="$VENV_BIN/python"

HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8000}

if [ -x "$UVICORN_BIN" ]; then
  echo "Starting server with $UVICORN_BIN on http://$HOST:$PORT"
  exec "$UVICORN_BIN" app.main:app --reload --host "$HOST" --port "$PORT"
elif command -v uvicorn >/dev/null 2>&1; then
  echo "Starting server with system uvicorn on http://$HOST:$PORT"
  exec uvicorn app.main:app --reload --host "$HOST" --port "$PORT"
else
  echo "uvicorn not found. Installing requirements into .venv..."
  if [ -x "$PY_BIN" ]; then
    "$PY_BIN" -m pip install -r requirements.txt
    exec "$UVICORN_BIN" app.main:app --reload --host "$HOST" --port "$PORT"
  else
    echo "Python venv not found. Run: bash scripts/setup.sh"
    exit 1
  fi
fi
