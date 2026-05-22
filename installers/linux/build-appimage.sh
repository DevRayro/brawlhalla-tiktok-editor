#!/usr/bin/env bash
# Build a portable AppImage of the Brawlhalla TikTok Editor.
#
# Strategy: the AppImage carries the repo source code + an AppRun launcher.
# On first launch, AppRun copies the repo into ~/.local/share/BrawlhallaEditor
# (so the venv and models survive across AppImage updates) and delegates to
# start.sh.
#
# Prereqs: appimagetool (https://github.com/AppImage/AppImageKit/releases),
#          rsync.
set -euo pipefail

VERSION="${VERSION:-1.0.0}"
ARCH="${ARCH:-x86_64}"
APP_DIR_NAME="BrawlhallaEditor"

cd "$(dirname "$0")/../.."

OUT_DIR="dist/linux/AppDir"
OUT_IMAGE="dist/${APP_DIR_NAME}-${VERSION}-${ARCH}.AppImage"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/usr/bin"
mkdir -p "$OUT_DIR/usr/share/applications"
mkdir -p "$OUT_DIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$OUT_DIR/repo"

# 1. AppRun script (entry point of the AppImage).
cat > "$OUT_DIR/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$(readlink -f "${0}")")"
DEST="$HOME/.local/share/BrawlhallaEditor"
mkdir -p "$DEST"

# First launch: replicate the bundled repo into ~/.local/share.
if [ ! -d "$DEST/pipeline" ]; then
  echo "First run — copying app to $DEST"
  rsync -a --delete \
    --exclude '.venv' \
    --exclude '_local_jobs' \
    --exclude 'work' \
    --exclude 'output' \
    --exclude 'remotion/node_modules' \
    "$HERE/repo/" "$DEST/"
fi

exec bash "$DEST/start.sh"
EOF
chmod +x "$OUT_DIR/AppRun"

# 2. Desktop file (required by AppImage).
cat > "$OUT_DIR/${APP_DIR_NAME}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Brawlhalla TikTok Editor
Comment=Auto-edit Brawlhalla replays into TikTok shorts
Exec=AppRun
Icon=${APP_DIR_NAME}
Categories=AudioVideo;Video;
Terminal=false
EOF
cp "$OUT_DIR/${APP_DIR_NAME}.desktop" "$OUT_DIR/usr/share/applications/"

# 3. Icon (required). Use a stock icon as a placeholder; replace with a real
#    256x256 PNG when one is available.
if [ -f installers/linux/icon.png ]; then
  cp installers/linux/icon.png "$OUT_DIR/${APP_DIR_NAME}.png"
  cp installers/linux/icon.png "$OUT_DIR/usr/share/icons/hicolor/256x256/apps/${APP_DIR_NAME}.png"
else
  # Generate a tiny placeholder so appimagetool doesn't fail. 256x256 PNG of
  # solid pink (matches the app theme).
  python3 - <<'PY'
from pathlib import Path
import struct, zlib
w = h = 256
def chunk(typ, data):
    crc = zlib.crc32(typ + data)
    return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", crc)
sig = b"\x89PNG\r\n\x1a\n"
ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # RGB, no compression filter
raw = b""
for _ in range(h):
    raw += b"\x00" + (b"\xff\x7e\xb6") * w
idat = zlib.compress(raw, 9)
png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
out = Path("dist/linux/AppDir") / "BrawlhallaEditor.png"
out.write_bytes(png)
out2 = Path("dist/linux/AppDir/usr/share/icons/hicolor/256x256/apps") / "BrawlhallaEditor.png"
out2.write_bytes(png)
PY
fi

# 4. Copy the source tree.
rsync -a \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '_local_jobs' \
  --exclude 'work' \
  --exclude 'output' \
  --exclude 'dist' \
  --exclude 'models/*.pt' \
  --exclude 'remotion/node_modules' \
  --exclude 'remotion/public/*-base.mp4' \
  --exclude 'remotion/public/*-source.mp4' \
  --exclude 'remotion/public/*-audio.m4a' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  ./ "$OUT_DIR/repo/"

# 5. Run appimagetool.
mkdir -p dist
appimagetool --no-appstream "$OUT_DIR" "$OUT_IMAGE"
chmod +x "$OUT_IMAGE"

echo "==> Done: $OUT_IMAGE"
