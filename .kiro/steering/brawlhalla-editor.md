---
inclusion: always
---

# Directives permanentes — Brawlhalla TikTok Auto-Editor

Ces règles sont actives sur **toutes les vidéos** générées par la pipeline.
Les consignes spécifiques à une vidéo donnée sont dans `input/notes.md`.

## Format de sortie

- **Toujours** 1080x1920 (9:16), 60fps, H.264 + AAC.
- Codec H.264 yuv420p pour compat TikTok max.
- Audio AAC 192kbps stéréo 48kHz.
- Pas de watermark.

## Caméra

- La caméra **doit suivre Kaya** en permanence (le perso joué).
- Le tracking est fait par **SAM2** (Meta) — modèle vidéo SOTA. Un seul clic
  rectangle sur Kaya à la frame seed, propagation automatique sur toute la vidéo.
- Zoom **serré** (~1.4–1.7x) en duel rapproché ou phase calme.
- Zoom **large** (~1.0–1.1x) quand l'action s'étale (plusieurs joueurs séparés, ringouts longs).
- Les transitions de zoom doivent être **lissées** (pas de saccades), idéalement en spring.
- Position lissée par filtre 1€ (one euro filter) pour éliminer le jitter sans
  ajouter de latence sur les mouvements rapides.

## Sous-titres

- Style **TikTok karaoké**: un mot apparaît à son timing exact, le mot courant
  est mis en évidence (couleur, scale).
- Police: sans-serif bold, tracking serré, ombre/stroke noir épais pour la lisibilité
  par-dessus le gameplay.
- Position: tiers inférieur, suffisamment haut pour ne pas être coupé par l'UI TikTok.
- 2 à 4 mots max visibles simultanément.
- Casse: **MAJUSCULES** par défaut (style TikTok punchy).

## Audio

- La voix du joueur reste **dominante**.
- La musique de fond joue à -18 dB par défaut.
- **Ducking automatique**: -8 dB supplémentaires quand la voix parle, transition douce.
- Pas de coupure de la musique, juste de la baisse.

## Brawlhalla — contexte

- Le joueur incarne **Kaya** (legend Brawlhalla).
- Il joue principalement en **1v1, 2v2, ou FFA** sur des stages standards.
- L'UI Brawlhalla affiche les portraits/HP en haut → ne **pas** masquer cette info quand
  on zoome (on garde un peu de marge).
- Les ringouts sont les moments les plus importants → les détecter et les souligner
  visuellement si possible.

## Performance

- La pipeline doit être **idempotente**: relancer `./run.sh` réutilise le cache (`work/`)
  et ne re-fait que les étapes invalidées.
- Toute étape coûteuse (transcription, tracking) doit être cachée par hash du fichier source.
