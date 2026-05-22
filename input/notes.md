---
# Notes par vidéo (édite ce fichier avant chaque run)

# Titre court à afficher en hook au début (vide = pas de hook)
title: ""

# Style général: chill | hype | clutch
# - chill   = cadence subs lente, zoom modéré
# - hype    = subs rapides, zoom plus serré, plus d'effets
# - clutch  = très serré sur Kaya, zoom-out brutal sur ringouts
style: hype

# Cadrage: wide | tight
# - wide   = vidéo source 1920x1080 entièrement visible, fond flouté en haut/bas
#            (TikTok letterbox). Aucun tracking, action complète toujours visible.
# - tight  = caméra qui suit Kaya avec zoom dynamique (ancien mode, demande un
#            clic manuel sur Kaya au démarrage et un peu de chance avec le tracking).
framing: tight

# WIDE-MODE: zoom sur la zone de jeu (1.0 = full 1920×1080 visible, plus on
# augmente plus on rogne les côtés morts du stage). 1.35 = ~15% chaque côté.
wide_zoom: 1.35

# TIGHT-MODE backend de tracking: action | sam2 | csrt
# - action = centroïde de mouvement, rapide, pas de clic, ne dérive pas. Recommandé.
# - sam2   = Segmentation Meta SAM 2 verrouillée sur Kaya. Plus précis quand
#            ça marche, demande un clic au démarrage, peut dériver sur du décor
#            statique. ~25 min/vidéo en plus.
# - csrt   = OpenCV CSRT, fallback uniquement.
tight_tracker: action

# Volume musique en dB (par défaut -18, augmente vers -12 si tu veux la sentir plus)
music_db: -18

# Highlights (timestamps en secondes du replay original)
# Liste des moments clés à zoomer fort. Format MM:SS ou secondes.
highlights: []
# Exemple:
# highlights:
#   - "00:42"
#   - "01:15"
#   - 95.5

# Couper le début/fin (en secondes du replay original)
trim_start: 0
trim_end: 0   # 0 = pas de coupe à la fin

# Termes additionnels à donner à Whisper pour cette vidéo (noms d'adversaires,
# nicknames, mots particuliers que tu utilises). En plus du lexique permanent
# dans pipeline/lexicon/brawlhalla.txt.
extra_terms: []
# Exemple:
# extra_terms:
#   - "Bistouflex"
#   - "Mon adversaire"
#   - "edgeguardé"
---

# Notes libres pour cette vidéo

(Écris ici n'importe quelle consigne spécifique pour cette vidéo.
Par ex: "le combat principal commence à 0:30, avant c'est juste de l'attente",
"je veux finir sur le ringout final", etc.)
