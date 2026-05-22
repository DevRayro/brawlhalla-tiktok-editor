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

# 3b. Ad-hoc sign the .app. Without a real Developer ID this won't satisfy
#     Gatekeeper, but it gives the bundle a stable code identity and avoids
#     the "damaged app" error on download in some macOS versions. Users will
#     still need to right-click → Open the first time, OR run the unblock
#     command from the README below.
codesign --deep --force --sign - "$APP_PATH" 2>/dev/null || true

# 3c. Drop a "READ ME FIRST" file at the DMG root explaining the Gatekeeper
#     workaround in French. Without notarization the user has to either:
#       a) right-click → Open (and accept once)
#       b) clear quarantine via xattr from the Terminal
README_PATH="$OUT_DIR/À LIRE.txt"
cat > "$README_PATH" <<'EOF'
Brawlhalla TikTok Editor — installation sur macOS
===================================================

1. Glisse "BrawlhallaEditor.app" sur le dossier Applications à droite.

2. Première ouverture:
   - Ouvre le dossier Applications dans Finder
   - Clic-DROIT (ou Ctrl+clic) sur BrawlhallaEditor → "Ouvrir"
   - Dans la fenêtre d'avertissement, clique à nouveau "Ouvrir"

   (Pas un double-clic la première fois. macOS bloque les apps non signées
   par Apple ; le clic-droit te laisse confirmer manuellement.)

3. Si rien ne se passe ou que macOS refuse:
   - Ouvre le Terminal (Spotlight → "Terminal")
   - Colle cette ligne et fais Entrée :

       find /Applications/BrawlhallaEditor.app -exec xattr -c {} \;

   - Réessaie d'ouvrir l'app.

4. Premier lancement: une fenêtre s'ouvre pour télécharger les modèles
   (Whisper + Remotion, environ 5 Go, 10 à 20 minutes).
   Les lancements suivants sont instantanés.

Pourquoi cette gymnastique ? L'app n'est pas signée avec un certificat
Apple Developer ($99/an), donc macOS la traite comme un logiciel inconnu.
Le code est public sur GitHub : https://github.com/DevRayro/brawlhalla-tiktok-editor
EOF

# 4. Build the DMG.
echo "==> Building DMG at $DMG_PATH"
mkdir -p dist
rm -f "$DMG_PATH"

# Stage everything that should appear in the mounted DMG: the .app plus the
# README. The Applications symlink is added after mounting (you can't ln
# inside an unmounted DMG image).
STAGE_DIR="$OUT_DIR/dmg-stage"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
cp -R "$APP_PATH" "$STAGE_DIR/"
cp "$README_PATH" "$STAGE_DIR/"

# Two-step: temp DMG → final compressed DMG. Lets us add a custom background
# and Applications symlink in the future without touching this script.
TMP_DMG="$OUT_DIR/${APP_NAME}-tmp.dmg"
hdiutil create -srcfolder "$STAGE_DIR" -volname "$APP_NAME" \
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

# Ad-hoc sign the DMG itself for the same reasons as the .app above.
codesign --force --sign - "$DMG_PATH" 2>/dev/null || true

echo "==> Done: $DMG_PATH"

# --- Optional: code-signing ----------------------------------------------
# To sign + notarize, you need a paid Apple Developer ID. Run something like:
#   codesign --deep --force --options runtime \
#     --sign "Developer ID Application: Your Name (TEAMID)" \
#     "$APP_PATH"
#   xcrun notarytool submit "$DMG_PATH" --apple-id <appleid> \
#     --team-id <TEAMID> --password <app-specific-password> --wait
#   xcrun stapler staple "$DMG_PATH"
