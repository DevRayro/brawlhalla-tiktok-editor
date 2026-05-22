#!/usr/bin/env bash
# Linux / WSL launcher — `bash start.sh` or chmod +x then ./start.sh
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ] || [ ! -d remotion/node_modules ]; then
  echo "==> First-run setup. This downloads Whisper + Remotion deps (~5GB)."
  bash setup.sh
fi

# shellcheck disable=SC1091
source .venv/bin/activate
exec python deploy/local/server.py
