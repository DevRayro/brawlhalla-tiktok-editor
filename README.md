# Brawlhalla TikTok Auto-Editor

Pipeline auto pour transformer un replay Brawlhalla en short TikTok 9:16:
sous-titres karaoké, caméra dynamique, musique de fond duckée, HUD HP en
overlay coins.

Tout tourne en local sur ta machine. Le serveur web local te fait une UI
identique à un site web (drag & drop, barre de progression, téléchargement),
mais tout reste sur ton PC.

## Installation

Trois manières, du plus simple au plus avancé:

### 1. Installeur tout-en-un (recommandé)

Va sur [la page des releases](https://github.com/DevRayro/brawlhalla-tiktok-editor/releases/latest)
et télécharge:

- **Windows**: `BrawlhallaEditor-Setup-x.y.z.exe` → double-clic, suivant suivant.
- **macOS**: `BrawlhallaEditor-x.y.z.dmg` → ouvre le DMG, glisse l'app dans
  Applications. Suis les instructions du fichier `À LIRE.txt` la 1ère fois
  (Gatekeeper).
- **Linux**: `BrawlhallaEditor-x.y.z-x86_64.AppImage` → `chmod +x` puis exécute.

L'installeur copie ~5 MB de code. Au premier lancement, l'app télécharge ~5 GB
de modèles (Whisper, Remotion Chrome, SAM2). Compte 10-20 min selon ta
connexion. Les lancements suivants sont instantanés.

Mises à jour: dès qu'une nouvelle version sort sur GitHub, un bandeau "Nouvelle
version dispo" apparaît dans l'UI. Un clic, ~10 secondes, c'est à jour. Pas de
re-téléchargement des 5 GB.

### 2. Clone Git (pour bidouiller le code)

Prérequis à installer une fois:

- **Python 3.11 ou 3.12**: [python.org](https://www.python.org/downloads/)
  (coche "Add Python to PATH" sur Windows).
- **Node.js 20+**: [nodejs.org](https://nodejs.org/)
- **ffmpeg**:
  - macOS: `brew install ffmpeg`
  - Windows: [gyan.dev builds](https://www.gyan.dev/ffmpeg/builds/) (ajouter au PATH)
  - Linux: `sudo apt install ffmpeg`

Puis:

```bash
git clone https://github.com/DevRayro/brawlhalla-tiktok-editor.git
cd brawlhalla-tiktok-editor
# macOS:    double-clic sur start.command
# Windows:  double-clic sur start.bat
# Linux:    ./start.sh
```

### 3. Mode CLI (sans UI web)

```bash
./setup.sh                      # ou setup.bat sur Windows
# pose ta vidéo dans input/, optionnel: une musique
./run.sh                        # ou run.bat
```

Le résultat sort dans `output/`. Réglages dans `input/notes.md` (voir le
template fourni).

## Utilisation (UI web)

1. Glisse-dépose ta vidéo (.mp4)
2. Glisse une musique OU colle un lien YouTube/SoundCloud (optionnel)
3. Choisis le cadrage (Wide ou Tight)
4. Clique « Lancer l'édition »
5. Récupère ta vidéo finale

## Vitesse indicative

| Hardware | Durée pour une vidéo de 3 min |
|---|---|
| MacBook Air M2 (8 CPU, no GPU) | ~50 min |
| PC gaming moderne (Ryzen 7 + RTX) | ~5-10 min |
| Workstation (16+ cœurs, RTX/H100) | ~3-5 min |

Le bottleneck est le rendu Remotion (CPU). La transcription Whisper est
quasi-instantanée si une GPU NVIDIA est dispo (CUDA auto-détecté).

L'encodage vidéo final utilise:
- **NVENC** sur GPU NVIDIA
- **VideoToolbox** sur Mac
- **AMF** sur GPU AMD (Windows/Linux)
- **QSV** sur Intel iGPU
- **libx264** en fallback CPU

(L'app teste chaque encoder au démarrage avec un mini render et choisit ce
qui marche réellement, pas juste ce qui est listé dans `ffmpeg -encoders`.)

## Architecture

```
input/                   Mode CLI: dossier source si tu utilises ./run.sh
output/                  Mode CLI: vidéo finale
pipeline/
├── modules/
│   ├── transcribe.py      Whisper large-v3, FR, word-level timestamps
│   ├── action_tracker.py  Centroïde de mouvement (rapide, pas de modèle ML)
│   ├── tracker.py         SAM2 (optionnel, plus précis mais lent)
│   ├── camera.py          Plan caméra (1€ filter + zoom dynamique)
│   ├── audiomix.py        ffmpeg sidechain ducking
│   ├── hardware.py        Détection GPU/encoder/hwaccel multi-plateforme
│   └── gpu_composite.py   Composition ffmpeg (avec fallback CPU automatique)
├── lexicon/
│   ├── brawlhalla.txt     Termes Brawlhalla pour biaiser Whisper
│   └── replacements.json  Corrections post-transcription
└── run.py                 Orchestrateur CLI
remotion/                Composition Remotion (React → MP4)
deploy/
├── local/
│   ├── server.py          Serveur FastAPI local
│   ├── updater.py         Self-update depuis GitHub Releases
│   └── audio_url.py       Téléchargement audio (yt-dlp)
└── frontend/              UI (HTML / CSS / JS)
installers/
├── windows/installer.iss  Inno Setup (.exe)
├── macos/build-dmg.sh     DMG packager
└── linux/build-appimage.sh
.github/workflows/release.yml  Build & publish des installeurs sur tag
```

## Modes de cadrage

- **wide**: vidéo source 1920×1080 entièrement visible, fond flouté en haut/bas.
  Aucun perso ne sort jamais du cadre. Recommandé pour Brawlhalla.
- **tight**: crop 9:16 qui suit le centroïde d'action avec zoom dynamique.
  Plus punchy mais quand les fighters sont écartés on en perd un.

Réglages détaillés via `input/notes.md` (mode CLI) ou directement dans l'UI.

## Publier une nouvelle version

```bash
# Sur main, à jour:
git tag vX.Y.Z
git push origin vX.Y.Z
```

Le workflow GitHub Actions builde les 3 installeurs (Windows/macOS/Linux) et
crée la GitHub Release. Compte ~10 min. Les utilisateurs déjà installés voient
le bandeau de mise à jour dans la minute qui suit la publication.
