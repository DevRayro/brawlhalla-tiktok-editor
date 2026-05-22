# Brawlhalla TikTok Auto-Editor

Pipeline auto pour transformer un replay Brawlhalla en short TikTok 9:16:
sous-titres karaoké, caméra dynamique, musique de fond duckée, HUD HP en
overlay coins.

Tout tourne en local sur ta machine. Le serveur web local te fait une UI
identique à un site web (drag & drop, barre de progression, téléchargement),
mais tout reste sur ton PC.

## Prérequis (à installer une fois)

- **Python 3.11+**: [python.org](https://www.python.org/downloads/) — coche
  "Add Python to PATH" pendant l'install.
- **Node.js 20+**: [nodejs.org](https://nodejs.org/)
- **ffmpeg**:
  - macOS: `brew install ffmpeg`
  - Windows: [gyan.dev builds](https://www.gyan.dev/ffmpeg/builds/) (ajouter au PATH)
  - Linux: `sudo apt install ffmpeg`
- **NVIDIA GPU** (optionnel, recommandé): rien à installer manuellement, le
  setup détecte ta carte et installe CUDA tout seul → Whisper x50 plus rapide.

## Démarrage

**macOS**: double-clique sur `start.command`
**Windows**: double-clique sur `start.bat`
**Linux/WSL**: `./start.sh`

Le premier lancement télécharge ~5 Go (modèle Whisper, Remotion Chrome). Les
suivants ouvrent le site directement dans ton navigateur sur
`http://127.0.0.1:8765`.

## Utilisation

1. Glisse-dépose ta vidéo (.mp4)
2. Optionnellement, glisse une musique (.mp3, .wav)
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
quasi-instantanée si une GPU NVIDIA est dispo.

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
│   └── audiomix.py        ffmpeg sidechain ducking
├── lexicon/
│   ├── brawlhalla.txt     Termes Brawlhalla pour biaiser Whisper
│   └── replacements.json  Corrections post-transcription
└── run.py                 Orchestrateur CLI
remotion/                Composition Remotion (React → MP4)
deploy/
├── local/server.py        Serveur web local
└── frontend/              UI (HTML / CSS / JS)
```

## Modes de cadrage

- **wide**: vidéo source 1920×1080 entièrement visible, fond flouté en haut/bas.
  Aucun perso ne sort jamais du cadre. Recommandé pour Brawlhalla.
- **tight**: crop 9:16 qui suit le centroïde d'action avec zoom dynamique.
  Plus punchy mais quand les fighters sont écartés on en perd un.

Réglages détaillés via `input/notes.md` (mode CLI) ou directement dans l'UI.
