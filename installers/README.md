# Packaging — building installers

The Brawlhalla TikTok Editor weighs ~5 GB once fully installed (Whisper
large-v3 = 3 GB, torch+CUDA = 1.5 GB, Remotion node_modules + Chromium = 500 MB,
SAM2 = 180 MB). Bundling all of that in one installer would produce a
download nobody wants. Instead, the installers ship the **app code only
(~5 MB)** and trigger a "first-run setup" the first time the user launches
it. The setup window shows download progress; subsequent launches start
instantly.

## Layout

```
installers/
├── windows/
│   ├── installer.iss          Inno Setup script — produces the .exe
│   └── first-run.ps1          PowerShell first-run helper (called by start.bat)
├── macos/
│   ├── build-dmg.sh           Build the .app + .dmg
│   ├── BrawlhallaEditor.app/  App bundle template (Info.plist + launchers)
│   └── Brewfile               Optional: brew bundle for the prerequisites
├── linux/
│   └── build-appimage.sh      Build the AppImage
└── shared/
    ├── first-run-gui.py       Tiny tkinter window shown during first-run setup
    └── requirements-runtime.txt  Bare imports for the GUI itself (tkinter is stdlib)
```

## CI

`.github/workflows/release.yml` runs on `git push --tags v*` and builds the
three artifacts in parallel. They land as a GitHub Release.

## What the user does

### Windows
1. Download `BrawlhallaEditor-Setup.exe`
2. Double-click → Inno Setup wizard installs to `%LocalAppData%\BrawlhallaEditor`.
3. Launches `BrawlhallaEditor.exe` (a thin shortcut to `start.bat`).
4. **First run only**: a small GUI window pops up: "Téléchargement des
   modèles…" with a progress bar. Takes ~10-15 minutes on a 100 Mbps line.
5. Browser opens on `http://127.0.0.1:8765`, ready to use.

### macOS
1. Download `BrawlhallaEditor.dmg`
2. Drag `BrawlhallaEditor.app` into `/Applications`.
3. First launch: Cmd+clic on the app, "Open" (Apple Gatekeeper warning, since
   we don't sign). Same first-run setup as Windows.
4. Browser opens, ready to use.

### Linux
1. Download `BrawlhallaEditor-x86_64.AppImage`
2. `chmod +x BrawlhallaEditor-x86_64.AppImage && ./BrawlhallaEditor-x86_64.AppImage`
3. Same first-run setup, same browser open.

## Building locally

### Windows (requires Inno Setup 6)
```
iscc installers\windows\installer.iss
```

### macOS
```
./installers/macos/build-dmg.sh
```

### Linux (requires `appimagetool`)
```
./installers/linux/build-appimage.sh
```

## Code-signing (deferred)

None of the artifacts are signed. This means:
- **Windows**: SmartScreen will say "Windows protected your PC". Click
  "More info → Run anyway".
- **macOS**: Gatekeeper will say "cannot be opened". Cmd+click the app and
  pick "Open" from the menu, then confirm.
- **Linux**: no signing concept, runs as-is.

To sign properly:
- Windows: $200-500/yr EV cert from a cert authority, configure
  `signtool.exe` in the Inno Setup script.
- macOS: $99/yr Apple Developer Program, then `codesign` + `notarytool`.

Both procedures are documented in their respective build scripts as
optional comments.
