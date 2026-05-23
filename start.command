#!/usr/bin/env bash
# macOS launcher (double-clickable from Finder).
# Avoids Tk because the system python's Tk binding is broken on recent macOS
# (reports "macOS 26 required") and Homebrew's python ships without _tkinter.
# Instead we run setup directly in this Terminal window.
set -euo pipefail
cd "$(dirname "$0")"

# Load nvm if it's installed so we can use a recent Node (≥18) even when the
# user's default is older. Must be sourced in the current shell, not a
# subshell, or PATH won't propagate.
if [ -z "${NVM_DIR:-}" ]; then
  export NVM_DIR="$HOME/.nvm"
fi
if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  nvm use 20 >/dev/null 2>&1 || true
fi

# Helpful banner so the user sees something when Finder opens this Terminal.
clear
cat <<'EOF'
  ╔═══════════════════════════════════════════════════╗
  ║   Brawlhalla TikTok Editor — démarrage local      ║
  ╚═══════════════════════════════════════════════════╝
EOF
echo

# ---- Stage 1: prerequisites via Homebrew ----
need_prereqs=0
for bin in python3 node ffmpeg; do
  command -v "$bin" >/dev/null 2>&1 || { need_prereqs=1; break; }
done

if [ "$need_prereqs" = "1" ]; then
  if ! command -v brew >/dev/null 2>&1; then
    cat <<'EOF'
Homebrew est requis pour installer Python / Node / ffmpeg automatiquement.

Installe-le d'abord en collant cette ligne dans le Terminal puis Entrée :

  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

Une fois fini, relance cette app.
EOF
    read -n 1 -s -r -p "Appuie sur une touche pour fermer…"
    exit 1
  fi
  echo "==> Installation des dépendances système (ffmpeg, node, python)…"
  brew install ffmpeg node python@3.11 || true
fi

# ---- Stage 2: first-run setup (venv + pip + Remotion) directly in shell ----
if [ ! -f .install-complete ]; then
  echo
  echo "==> Première installation. Ça télécharge ~5 Go (Whisper, Remotion, modèles)."
  echo "    Compte 10 à 20 minutes selon ta connexion. Tu peux laisser tourner."
  echo

  if [ -x ./setup.sh ]; then
    bash ./setup.sh
  else
    echo "ERREUR: setup.sh introuvable ou non exécutable" >&2
    read -n 1 -s -r -p "Appuie sur une touche pour fermer…"
    exit 1
  fi

  if [ -d .venv ] && [ -d remotion/node_modules ]; then
    echo "ok" > .install-complete
    echo
    echo "==> Installation terminée."
  else
    echo
    echo "ERREUR: l'installation a échoué (.venv ou remotion/node_modules manquant)." >&2
    echo "Relance cette app pour réessayer, ou ouvre les logs ci-dessus." >&2
    read -n 1 -s -r -p "Appuie sur une touche pour fermer…"
    exit 1
  fi
fi

# ---- Stage 3: server ----
if [ ! -d .venv ]; then
  echo "Erreur: .install-complete existe mais .venv n'existe pas." >&2
  echo "Supprime .install-complete dans le dossier de l'app et relance." >&2
  read -n 1 -s -r -p "Appuie sur une touche pour fermer…"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ ! -d remotion/node_modules ]; then
  echo "==> Installation des deps Remotion (manquantes)…"
  (cd remotion && npm install --no-audit --no-fund)
fi

echo
echo "==> Lancement du serveur. Ouverture du navigateur sur http://127.0.0.1:8765 …"
echo "    Ctrl+C dans cette fenêtre pour arrêter."
echo

# Restart loop: when the server self-updates, it exits with code 75 and we
# relaunch automatically (so the new code takes effect).
while true; do
  set +e
  python3 deploy/local/server.py
  rc=$?
  set -e
  if [ "$rc" = "75" ] || [ -f .restart-requested ]; then
    rm -f .restart-requested
    echo
    echo "==> Mise à jour appliquée — redémarrage du serveur…"
    sleep 1
    continue
  fi
  exit $rc
done
