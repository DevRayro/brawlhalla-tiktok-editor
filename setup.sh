#!/usr/bin/env bash
# Cross-platform setup for the Brawlhalla TikTok auto-editor.
# Detects the OS and GPU, installs the matching PyTorch wheel + SAM2.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Checking system dependencies"
for bin in ffmpeg ffprobe; do
  command -v "$bin" >/dev/null 2>&1 || {
    echo "ERROR: $bin not found. Install it first." >&2
    exit 1
  }
done

# Pick the Python interpreter. Prefer 3.11/3.12 (best compat with pinned deps).
PYEXE=""
for cand in python3.11 python3.12 python3.10 python3; do
  command -v "$cand" >/dev/null 2>&1 && PYEXE="$cand" && break
done
[ -z "$PYEXE" ] && { echo "ERROR: python3 not found" >&2; exit 1; }
echo "    Using $PYEXE ($($PYEXE --version))"

# Try to load nvm so we can pin Node 20 even if the user's default is older.
[ -z "${NVM_DIR:-}" ] && export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
command -v nvm >/dev/null 2>&1 && (nvm use 20 >/dev/null 2>&1 || nvm install 20)

command -v node >/dev/null 2>&1 || { echo "ERROR: node/npm not found." >&2; exit 1; }
NODE_MAJOR=$(node -p "process.versions.node.split('.')[0]")
[ "$NODE_MAJOR" -lt 18 ] && { echo "ERROR: Node 18+ required (found $(node --version))." >&2; exit 1; }
echo "    Using Node $(node --version)"

echo "==> Creating Python venv at .venv"
[ -d .venv ] || $PYEXE -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing base Python dependencies"
pip install --upgrade pip wheel >/dev/null
pip install -r pipeline/requirements.txt

# ----- Torch installation tuned to the local hardware -----
OS_NAME=$(uname -s)
TORCH_INDEX=""
case "$OS_NAME" in
  Darwin)
    echo "==> macOS detected, installing default torch (MPS-capable on Apple Silicon)"
    pip install --upgrade torch torchvision
    ;;
  Linux|*)
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
      GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1)
      echo "    GPU: $GPU_NAME"
      # Blackwell (RTX 50xx) requires CUDA 12.8 wheels; everything older runs
      # on cu121.
      if printf '%s' "$GPU_NAME" | grep -Eqi 'RTX 50|Blackwell'; then
        TORCH_INDEX=https://download.pytorch.org/whl/cu128
        echo "==> Blackwell detected, installing CUDA 12.8 build of torch"
      else
        TORCH_INDEX=https://download.pytorch.org/whl/cu121
        echo "==> NVIDIA detected, installing CUDA 12.1 build of torch"
      fi
      pip install --upgrade torch torchvision --index-url "$TORCH_INDEX"
      pip install nvidia-cudnn-cu12==9.* || true
    else
      echo "==> No NVIDIA GPU detected, installing CPU build of torch"
      pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cpu
    fi
    ;;
esac

echo "==> Installing SAM 2 (no-deps)"
pip install --no-deps "SAM-2 @ git+https://github.com/facebookresearch/sam2.git" || \
  echo "    SAM-2 install failed (non-fatal, tracker will fall back to action_tracker)"

echo "==> Downloading SAM2 checkpoint (~180MB)"
mkdir -p models
if [ ! -f models/sam2.1_hiera_small.pt ]; then
  curl -L --fail -o models/sam2.1_hiera_small.pt \
    https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt || \
    echo "    Download failed (non-fatal, SAM2 backend will fall back)."
fi

echo "==> Installing Remotion dependencies"
cd remotion
if [ ! -d node_modules ]; then
  npm install
else
  npm install --no-audit --no-fund
fi
cd ..

echo "==> Setup complete. Run ./start.sh (or start.command on macOS) to launch."
