#!/usr/bin/env bash
# Generate platform icons from a single source PNG/JPEG.
#
# Inputs:
#   icons/source.png  (anything PIL can read; will be resized as needed)
#
# Outputs:
#   installers/macos/AppIcon.icns
#   installers/windows/app.ico
#   installers/linux/app.png      (512×512)
#   deploy/frontend/icon-192.png
#   deploy/frontend/icon-512.png
#   deploy/frontend/favicon.ico
#
# Cross-platform: uses Pillow on macOS/Linux/Windows (already in our deps).
# On macOS we additionally use `iconutil` to produce a proper .icns set.
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${1:-icons/source.png}"
if [ ! -f "$SRC" ]; then
  echo "ERROR: source icon not found at $SRC" >&2
  exit 1
fi

mkdir -p icons installers/macos installers/windows installers/linux \
         deploy/frontend

# Pick a Python with Pillow available.
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && \
     "$cand" -c "import PIL" 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Pillow is required. Install with: pip install pillow" >&2
  exit 1
fi

echo "==> Generating PNG sizes from $SRC"
"$PY" - "$SRC" <<'PY'
import sys, os
from PIL import Image
src = sys.argv[1]
img = Image.open(src).convert("RGBA")

# Square crop if needed.
w, h = img.size
if w != h:
    side = min(w, h)
    img = img.crop(((w - side) // 2, (h - side) // 2,
                    (w + side) // 2, (h + side) // 2))

sizes = {
    "icons/icon-1024.png": 1024,
    "icons/icon-512.png": 512,
    "icons/icon-256.png": 256,
    "icons/icon-192.png": 192,
    "icons/icon-128.png": 128,
    "icons/icon-64.png": 64,
    "icons/icon-48.png": 48,
    "icons/icon-32.png": 32,
    "icons/icon-16.png": 16,
}
for path, side in sizes.items():
    img.resize((side, side), Image.LANCZOS).save(path, optimize=True)
    print(f"   {path}")
PY

# --- macOS .icns ---
if command -v iconutil >/dev/null 2>&1; then
  echo "==> Building macOS .icns"
  ICONSET="installers/macos/AppIcon.iconset"
  rm -rf "$ICONSET"
  mkdir -p "$ICONSET"
  cp icons/icon-16.png    "$ICONSET/icon_16x16.png"
  cp icons/icon-32.png    "$ICONSET/icon_16x16@2x.png"
  cp icons/icon-32.png    "$ICONSET/icon_32x32.png"
  cp icons/icon-64.png    "$ICONSET/icon_32x32@2x.png"
  cp icons/icon-128.png   "$ICONSET/icon_128x128.png"
  cp icons/icon-256.png   "$ICONSET/icon_128x128@2x.png"
  cp icons/icon-256.png   "$ICONSET/icon_256x256.png"
  cp icons/icon-512.png   "$ICONSET/icon_256x256@2x.png"
  cp icons/icon-512.png   "$ICONSET/icon_512x512.png"
  cp icons/icon-1024.png  "$ICONSET/icon_512x512@2x.png"
  iconutil -c icns "$ICONSET" -o installers/macos/AppIcon.icns
  rm -rf "$ICONSET"
  echo "   installers/macos/AppIcon.icns"
else
  echo "==> Skipping .icns (iconutil not available; you're probably on Linux/Win)"
fi

# --- Windows .ico (multi-size) ---
echo "==> Building Windows .ico"
"$PY" - <<'PY'
from PIL import Image
sizes = [16, 32, 48, 64, 128, 256]
images = [Image.open(f"icons/icon-{s}.png") for s in sizes]
images[0].save(
    "installers/windows/app.ico", format="ICO",
    sizes=[(s, s) for s in sizes],
    append_images=images[1:],
)
print("   installers/windows/app.ico")
PY

# --- Linux PNG ---
cp icons/icon-512.png installers/linux/icon.png
echo "==> installers/linux/icon.png"

# --- Web favicon + manifest icons ---
cp icons/icon-192.png deploy/frontend/icon-192.png
cp icons/icon-512.png deploy/frontend/icon-512.png

echo "==> Building favicon.ico"
"$PY" - <<'PY'
from PIL import Image
sizes = [16, 32, 48]
images = [Image.open(f"icons/icon-{s}.png") for s in sizes]
images[0].save(
    "deploy/frontend/favicon.ico", format="ICO",
    sizes=[(s, s) for s in sizes],
    append_images=images[1:],
)
print("   deploy/frontend/favicon.ico")
PY

echo
echo "==> Icons regenerated. Re-build installers with the standard build scripts."
