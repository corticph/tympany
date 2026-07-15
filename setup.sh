#!/usr/bin/env bash
# Convenience wrapper for working from a local source checkout. Installs
# dependencies into a Poetry-managed virtualenv and seeds .env. Equivalent to
# running `poetry install` yourself.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v poetry >/dev/null 2>&1; then
  echo "Error: Poetry is not installed or not on your PATH."
  echo "This setup script is for a local source checkout."
  echo "Install it from https://python-poetry.org/docs/#installation, then re-run ./setup.sh."
  exit 1
fi

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  echo "Creating .env from .env.example..."
  cp .env.example .env
fi

echo "Installing dependencies with Poetry..."
poetry install

echo
echo "Setup complete."
if [ -f ".env" ]; then
  echo "Review .env and fill in any credentials you need."
fi
echo "Start the app from this checkout with:"
echo "  ./start.sh        (or: poetry run tympany-serve)"
