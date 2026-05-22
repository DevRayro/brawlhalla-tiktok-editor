#!/usr/bin/env bash
# macOS launcher — double-click in Finder to run.
set -e
cd "$(dirname "$0")"

# First-run setup (idempotent).
if [ ! -d .venv ] || [ ! -d remotion/node_modules ]; then
  echo "==> First-run setup. This downloads Whisper + Remotion deps (~5GB total)."
  ./setup.sh
fi

# Activate venv.
# shellcheck disable=SC1091
source .venv/bin/activate

# Launch the local server (opens the browser automatically).
exec python deploy/local/server.py
