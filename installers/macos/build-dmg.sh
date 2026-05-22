#!/usr/bin/env bash
# Build the BrawlhallaEditor.app bundle and wrap it in a DMG.
#
# Prereqs: macOS only, hdiutil (built-in), rsync (built-in).
# Output:  dist/BrawlhallaEditor-<version>.dmg
set -euo pipefail

VERSION="${VERSION:-1.0.0}"
APP_NAME="BrawlhallaEditor"

cd "$(dirname "$0")/../.."  # repo root

OUT_DIR="dist/macos"
APP_PATH="$OUT_DIR/${APP_NAME}.app"
DMG_PATH="dist/${APP_NAME}-${VERSION}.dmg"

rm -rf "$OUT_DIR"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources/repo"

# 1. Copy the static parts of the bundle.
cp installers/macos/Info.plist "$APP_PATH/Contents/Info.plist"
cp installers/macos/launcher   "$APP_PATH/Contents/MacOS/launcher"
chmod +x "$APP_PATH/Contents/MacOS/launcher"

# 2. Optional icon. If you ship one at installers/macos/AppIcon.icns, copy it.
if [ -f installers/macos/AppIcon.icns ]; then
  cp installers/macos/AppIcon.icns "$APP_PATH/Contents/Resources/AppIcon.icns"
fi

# 3. Copy the repo source (lightweight; modules and node_modules are downloaded
#    at first launch).
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
  ./ "$APP_PATH/Contents/Resources/repo/"

# 4. Build the DMG.
echo "==> Building DMG at $DMG_PATH"
mkdir -p dist
rm -f "$DMG_PATH"

# Two-step: temp DMG → final compressed DMG. Lets us add a custom background
# and Applications symlink in the future without touching this script.
TMP_DMG="$OUT_DIR/${APP_NAME}-tmp.dmg"
hdiutil create -srcfolder "$APP_PATH" -volname "$APP_NAME" \
  -fs HFS+ -fsargs "-c c=64,a=16,e=16" -format UDRW -size 100m \
  "$TMP_DMG" >/dev/null

# Mount, add an Applications symlink, unmount.
DEVICE=$(hdiutil attach -readwrite -noverify -noautoopen "$TMP_DMG" \
  | grep -E '^/dev/' | head -n1 | awk '{print $1}')
sleep 2
ln -sf /Applications "/Volumes/$APP_NAME/Applications" || true
hdiutil detach "$DEVICE" >/dev/null

hdiutil convert "$TMP_DMG" -format UDZO -imagekey zlib-level=9 \
  -o "$DMG_PATH" >/dev/null
rm -f "$TMP_DMG"

echo "==> Done: $DMG_PATH"

# --- Optional: code-signing ----------------------------------------------
# To sign + notarize, you need a paid Apple Developer ID. Run something like:
#   codesign --deep --force --options runtime \
#     --sign "Developer ID Application: Your Name (TEAMID)" \
#     "$APP_PATH"
#   xcrun notarytool submit "$DMG_PATH" --apple-id <appleid> \
#     --team-id <TEAMID> --password <app-specific-password> --wait
#   xcrun stapler staple "$DMG_PATH"
