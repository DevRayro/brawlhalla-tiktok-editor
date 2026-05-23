#!/usr/bin/env bash
# Linux launcher: prereq check → first-run GUI → server.
set -euo pipefail
cd "$(dirname "$0")"

# ---- Stage 1: prerequisites ----
need_prereqs=0
for bin in python3 node ffmpeg; do
  command -v "$bin" >/dev/null 2>&1 || { need_prereqs=1; break; }
done

if [ "$need_prereqs" = "1" ]; then
  echo "Some prerequisites are missing. Linux uses your distro's package manager:"
  if command -v apt >/dev/null 2>&1; then
    echo "  sudo apt install -y ffmpeg python3 python3-venv python3-tk nodejs npm"
  elif command -v dnf >/dev/null 2>&1; then
    echo "  sudo dnf install -y ffmpeg python3 python3-tkinter nodejs npm"
  elif command -v pacman >/dev/null 2>&1; then
    echo "  sudo pacman -S ffmpeg python python-tk nodejs npm"
  else
    echo "  Install ffmpeg, python3 (with tkinter), and node 20+ via your package manager."
  fi
  echo
  echo "Then re-run ./start.sh."
  exit 1
fi

# ---- Stage 2: first-run GUI ----
if [ ! -f .install-complete ]; then
  if python3 -c 'import tkinter' >/dev/null 2>&1; then
    python3 installers/shared/first_run_gui.py
  else
    # Headless server — fall back to plain CLI install.
    echo "tkinter not available; running setup.sh in CLI mode."
    bash setup.sh
    touch .install-complete
  fi
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

# Restart loop: when the server self-updates it exits with code 75 and we
# relaunch automatically.
while true; do
  set +e
  python3 deploy/local/server.py
  rc=$?
  set -e
  if [ "$rc" = "75" ] || [ -f .restart-requested ]; then
    rm -f .restart-requested
    echo "==> Update applied — restarting…"
    sleep 1
    continue
  fi
  exit $rc
done
