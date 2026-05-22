#!/usr/bin/env bash
# One-time setup for the Brawlhalla TikTok auto-editor pipeline.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Checking system dependencies"
for bin in ffmpeg ffprobe python3; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "ERROR: $bin not found. Install it first." >&2
    exit 1
  fi
done

# Try to load nvm so we can pin Node 20 even if the user's default is older.
if [ -z "${NVM_DIR:-}" ]; then
  export NVM_DIR="$HOME/.nvm"
fi
if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
fi
if command -v nvm >/dev/null 2>&1; then
  nvm use 20 >/dev/null 2>&1 || nvm install 20
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: node/npm not found." >&2
  exit 1
fi

NODE_MAJOR=$(node -p "process.versions.node.split('.')[0]")
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "ERROR: Node 18+ required (found $(node --version)). Install Node 20 (e.g. 'nvm install 20')." >&2
  exit 1
fi
echo "    Using Node $(node --version)"

echo "==> Creating Python venv at .venv"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing Python dependencies"
pip install --upgrade pip wheel >/dev/null
pip install -r pipeline/requirements.txt

# Install CUDA-enabled torch for NVIDIA users (Linux/WSL). Detected via
# `nvidia-smi`. faster-whisper picks up CUDA automatically when torch.cuda
# is available, giving a ~50× speedup on Whisper.
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    echo "==> NVIDIA GPU detected, installing CUDA build of torch"
    pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cu121
    # cuDNN libs needed by faster-whisper / ctranslate2 on GPU.
    pip install nvidia-cudnn-cu12==9.* || true
fi

echo "==> Downloading SAM2 checkpoint (~180MB) [optional, used by tight_tracker=sam2]"
mkdir -p models
if [ ! -f models/sam2.1_hiera_small.pt ]; then
  curl -L --fail -o models/sam2.1_hiera_small.pt \
    https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt || \
    echo "    Download failed (skipping; SAM2 backend is optional)."
else
  echo "    Already present."
fi

echo "==> Installing Remotion dependencies"
cd remotion
if [ ! -d node_modules ]; then
  npm install
else
  npm install --no-audit --no-fund
fi
cd ..

echo "==> Setup complete. Run ./run.sh to process a video."
