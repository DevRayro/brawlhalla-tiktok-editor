#!/usr/bin/env bash
# Run the full pipeline: tracking + transcription + composition + render.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "ERROR: .venv not found. Run ./setup.sh first." >&2
  exit 1
fi

# Load nvm and pin Node 20 for Remotion.
if [ -z "${NVM_DIR:-}" ]; then
  export NVM_DIR="$HOME/.nvm"
fi
if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  nvm use 20 >/dev/null 2>&1 || true
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python pipeline/run.py "$@"
