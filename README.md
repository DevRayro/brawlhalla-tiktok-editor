# Brawlhalla TikTok Auto-Editor

Pipeline auto pour transformer un replay Brawlhalla en short TikTok 9:16:
sous-titres karaoké, caméra dynamique, musique de fond duckée, HUD HP en
overlay coins.

## Téléchargement (utilisateur final)

Récupère l'installer prêt à l'emploi pour ta plateforme depuis la page
[Releases](../../releases) du repo:

| OS | Fichier | Comment |
|---|---|---|
| Windows 10/11 | `BrawlhallaEditor-Setup-x.y.z.exe` | Double-clique. SmartScreen affichera un avertissement (installer non signé) → clique « Plus d'infos → Exécuter quand même ». |
| macOS 11+ (Apple Silicon ou Intel) | `BrawlhallaEditor-x.y.z.dmg` | Glisse l'app dans `/Applications`. Première ouverture: Cmd+clic sur l'app puis « Ouvrir » (Gatekeeper). |
| Linux x86_64 | `BrawlhallaEditor-x.y.z-x86_64.AppImage` | `chmod +x` puis double-clique. |

Au premier lancement, l'app installe automatiquement ses dépendances
(modèle Whisper ~3 GB, PyTorch CUDA, Remotion). Compte 10–20 minutes selon
ta connexion. Les lancements suivants démarrent en quelques secondes.

L'app détecte aussi automatiquement ta config:
- **NVIDIA GPU** → CUDA + NVENC pour Whisper, SAM2 et l'encode vidéo
- **Apple Silicon** → MPS pour SAM2, VideoToolbox pour l'encode
- **CPU seul** → libx264 + Whisper int8 (plus lent mais ça marche)

## Mode développeur (clone du repo)

Deux modes d'utilisation:

| | **Cloud (Modal)** | **Local (ton PC)** |
|---|---|---|
| Setup | rien | une fois (`setup.sh` / `setup.bat`) |
| Lancement | une URL | double-click sur `start.command` / `start.bat` |
| GPU NVIDIA utilisé | non | oui (Whisper x50 plus rapide) |
| Vitesse | ~25 min / vidéo | ~5–10 min sur PC moderne |
| Quota | $30 free Modal/mois | illimité |
| Partage | URL publique | impossible (sauf via tunnel) |

## Lancement local (recommandé pour usage personnel)

### Prérequis (à installer une fois)

- **Python 3.11+**: [python.org](https://www.python.org/downloads/)
- **Node.js 20+**: [nodejs.org](https://nodejs.org/)
- **ffmpeg**:
  - macOS: `brew install ffmpeg`
  - Windows: [gyan.dev builds](https://www.gyan.dev/ffmpeg/builds/) (ajouter au PATH)
  - Linux: `sudo apt install ffmpeg`
- **NVIDIA GPU** (optionnel, recommandé): rien à installer manuellement, le
  setup détecte ta carte et installe CUDA tout seul.

### Démarrage

**macOS**: double-clique sur `start.command`
**Windows**: double-clique sur `start.bat`
**Linux/WSL**: `./start.sh`

Le premier lancement télécharge ~5 Go (modèle Whisper, Remotion Chrome). Les
suivants ouvrent le site directement dans ton navigateur sur
`http://127.0.0.1:8765`.

### Utilisation

1. Glisse-dépose ta vidéo (.mp4)
2. Optionnellement, glisse une musique (.mp3, .wav)
3. Choisis le cadrage (Wide ou Tight)
4. Clique « Lancer l'édition »
5. Récupère ta vidéo finale

## Lancement cloud (Modal)

Si tu n'as pas de GPU costaud, le cloud reste plus rapide que ton CPU. Voir
[deploy/modal_app.py](./deploy/modal_app.py).

```bash
pip install modal
modal token new
modal deploy deploy/modal_app.py
```

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
├── modal_app.py           Backend Modal cloud
└── frontend/              UI partagée local + cloud
```

## Modes de cadrage

- **wide**: vidéo source 1920×1080 entièrement visible, fond flouté en haut/bas.
  Aucun perso ne sort jamais du cadre. Recommandé pour Brawlhalla.
- **tight**: crop 9:16 qui suit le centroïde d'action avec zoom dynamique.
  Plus punchy mais quand les fighters sont écartés on en perd un.

Réglages détaillés via `input/notes.md` (mode CLI) ou directement dans l'UI.
