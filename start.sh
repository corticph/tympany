#!/usr/bin/env bash
# Convenience wrapper: run the web server from a local source checkout via
# Poetry.
#
# Configuration (override via environment, read by web/serve.py):
#   HOST            bind address (default 127.0.0.1; use 0.0.0.0 behind a proxy)
#   PORT            bind port    (default 8000)
#   TYMPANY_DEV=1   enable --reload for local development (off by default)
#   WEB_CONCURRENCY worker processes (default 1; multi-worker needs SECRET_KEY set)
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v poetry >/dev/null 2>&1; then
  echo "Error: Poetry is not installed. This script is for a local source checkout."
  echo "Run ./setup.sh first (or install Poetry)."
  exit 1
fi

exec poetry run tympany-serve
