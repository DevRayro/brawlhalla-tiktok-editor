#!/usr/bin/env bash
# Generate platform icons from a single source image.
#
# Inputs:
#   icons/source.png   (any reasonable size; PNG/JPEG/WEBP all OK)
#
# Outputs:
#   installers/macos/AppIcon.icns
#   installers/windows/app.ico
#   installers/linux/icon.png      (512×512)
#   deploy/frontend/icon-192.png
#   deploy/frontend/icon-512.png
#   deploy/frontend/favicon.ico
#
# We never upscale beyond the source's native size (upscaling produces blurry
# icons in OS icon caches). If the source is smaller than 1024×1024, we cap
# at the source size and skip the macOS retina @2x slots that would force an
# upscale.
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
from PIL import Image, ImageFilter, ImageDraw
src_path = sys.argv[1]
img = Image.open(src_path).convert("RGBA")

# Square crop (centered) if needed.
w, h = img.size
if w != h:
    side = min(w, h)
    img = img.crop(((w - side) // 2, (h - side) // 2,
                    (w + side) // 2, (h + side) // 2))
side = img.size[0]
print(f"   source: {side}×{side}")

# We never upscale beyond the source size; macOS retina slots will fall back
# to a slightly smaller @2x png if needed. This avoids the all-blurry icon.
NATIVE_MAX = side
def fit(target: int) -> Image.Image:
    """Resize down with Lanczos. If the requested size > native, return native."""
    final = min(target, NATIVE_MAX)
    if final == side:
        return img.copy()
    return img.resize((final, final), Image.LANCZOS)

# A subtle unsharp mask boosts perceived sharpness on the small (16/32/48px)
# variants without making the larger ones look crunchy.
def sharpen_small(im: Image.Image) -> Image.Image:
    if im.size[0] >= 192:
        return im
    return im.filter(ImageFilter.UnsharpMask(radius=0.6, percent=120, threshold=2))

sizes = [1024, 512, 256, 192, 128, 64, 48, 32, 16]
for s in sizes:
    out = fit(s)
    out = sharpen_small(out)
    out_path = f"icons/icon-{s}.png"
    out.save(out_path, optimize=True)
    print(f"   {out_path}  ({out.size[0]}×{out.size[1]})")
PY

# --- macOS .icns ---
if command -v iconutil >/dev/null 2>&1; then
  echo "==> Building macOS .icns"
  ICONSET="installers/macos/AppIcon.iconset"
  rm -rf "$ICONSET"
  mkdir -p "$ICONSET"
  # Each slot uses the matching size; we never re-upscale, so retina @2x
  # slots get whatever was actually generated (might equal the non-@2x size
  # if the source was too small).
  cp icons/icon-16.png   "$ICONSET/icon_16x16.png"
  cp icons/icon-32.png   "$ICONSET/icon_16x16@2x.png"
  cp icons/icon-32.png   "$ICONSET/icon_32x32.png"
  cp icons/icon-64.png   "$ICONSET/icon_32x32@2x.png"
  cp icons/icon-128.png  "$ICONSET/icon_128x128.png"
  cp icons/icon-256.png  "$ICONSET/icon_128x128@2x.png"
  cp icons/icon-256.png  "$ICONSET/icon_256x256.png"
  cp icons/icon-512.png  "$ICONSET/icon_256x256@2x.png"
  cp icons/icon-512.png  "$ICONSET/icon_512x512.png"
  cp icons/icon-1024.png "$ICONSET/icon_512x512@2x.png"
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
sizes = [256, 128, 64, 48, 32, 16]
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
sizes = [48, 32, 16]
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
