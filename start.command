#!/usr/bin/env bash
# macOS launcher (double-clickable from Finder).
set -euo pipefail
cd "$(dirname "$0")"

# ---- Stage 1: prerequisites via Homebrew ----
need_prereqs=0
for bin in python3 node ffmpeg; do
  command -v "$bin" >/dev/null 2>&1 || { need_prereqs=1; break; }
done

if [ "$need_prereqs" = "1" ]; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installing missing prerequisites via Homebrew…"
    # Tk is bundled with python3@3.11/3.12 from Homebrew.
    brew install ffmpeg node python@3.11 || true
  else
    echo "Homebrew is required to auto-install prerequisites."
    echo "Install Homebrew first:"
    echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    echo "Then re-run start.command."
    read -n 1 -s -r -p "Press any key to exit..."
    exit 1
  fi
fi

# ---- Stage 2: first-run GUI ----
if [ ! -f .install-complete ]; then
  python3 installers/shared/first_run_gui.py
fi

# ---- Stage 3: server ----
if [ ! -d .venv ]; then
  echo "Internal error: .install-complete exists but .venv is missing." >&2
  echo "Delete .install-complete and re-run this launcher." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ ! -d remotion/node_modules ]; then
  (cd remotion && npm install --no-audit --no-fund)
fi

python3 deploy/local/server.py
