#!/usr/bin/env bash
# dint – one-command launcher (uses uv)
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
PYTHON_VERSION="3.13"

# Create venv if missing
if [ ! -d "$VENV" ]; then
  echo "→ Creating virtual environment with Python $PYTHON_VERSION…"
  uv venv "$VENV" --python "$PYTHON_VERSION"
fi

# Install / update deps (project itself + all dependencies)
echo "→ Installing dependencies…"
uv sync --quiet

# Check .env
if [ ! -f .env ]; then
  echo "⚠  No .env file found. Copying .env.example → .env"
  cp .env.example .env
  echo "   Edit .env and add your API key, then re-run this script."
  echo "   (Or configure it later via the Settings panel in the UI.)"
  exit 1
fi

# Launch via the installed CLI entry point
exec "$VENV/bin/dint" "$@"
