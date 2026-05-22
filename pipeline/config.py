"""Central config for the Brawlhalla auto-editor pipeline.

Everything path-related lives here so the rest of the code is repo-relative.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
WORK_DIR = ROOT / "work"
REMOTION_DIR = ROOT / "remotion"

# --- Output spec ---
OUT_W = 1080
OUT_H = 1920
OUT_FPS = 60

# --- Camera tuning ---
# Framing mode:
#   "wide"  = letterbox (full source visible, blurred bg fills 9:16 frame).
#             No camera tracking needed → no manual click. Default.
#   "tight" = cropped 9:16 with camera following Kaya + dynamic zoom (legacy).
#             Requires SAM2 tracking + manual seed click on Kaya.
FRAMING_MODE = "wide"

# Tight-mode tracker backend:
#   "action" = action-centroid (motion energy mask). Fast, no manual click,
#              never drifts onto static objects. Default and recommended.
#   "sam2"   = SAM 2 segmentation locked on Kaya. Best when it works, slow
#              (~25 min/video on M2), can drift onto static decor (rocks).
#              Asks for one click on Kaya at startup.
#   "csrt"   = OpenCV CSRT. Fallback only.
TIGHT_TRACKER = "action"

# Wide-mode zoom: how much we crop the SOURCE before fitting it into the
# 9:16 frame letterbox. 1.0 = full source 1920×1080 visible. 1.35 = ~15% of
# each side cropped to focus on the central play zone.
WIDE_ZOOM = 1.35

# Used only when FRAMING_MODE = "tight".
# Zoom range applied on top of the base 9:16 crop (1.0 = base crop).
ZOOM_MIN = 1.0
ZOOM_MAX = 1.30
ZOOM_DEFAULT = 1.10

# How fast zoom can change per second (in zoom units). Higher = snappier.
ZOOM_SLEW_PER_SEC = 0.8

# 1€ filter for camera position. Higher mincutoff = follows fast moves more
# tightly (less lag). Higher beta = more reactive on fast motion. We tuned
# these to keep up with Kaya during dashes/recoveries while staying smooth.
ONE_EURO_MINCUTOFF = 2.5
ONE_EURO_BETA = 0.20

# Safety margin around Kaya inside the cropped frame, as a fraction of the
# crop width. The camera stays centered on Kaya, but if the tracker says he's
# at e.g. position X, we treat him as a small box of radius X ± margin and
# bias the crop so that whole box stays inside. In practice this means we
# zoom out a bit when Kaya is near the action zone edge.
CAMERA_SAFETY_MARGIN = 0.18

# --- Subtitles ---
# Max words shown at once.
SUB_GROUP_SIZE = 3
# Don't break sentences across groups if a word is closer than this in seconds.
SUB_GROUP_MAX_GAP = 0.6

# --- Audio mix ---
MUSIC_DB_DEFAULT = -18.0
DUCK_DB = -8.0

# --- HP / Portrait overlay ---
# We sample two rectangles from the SOURCE video (the Brawlhalla HUD portraits)
# and re-paint them in the corners of the 9:16 output. Coordinates are
# expressed as fractions of source width/height. Defaults assume a standard
# 1v1 Brawlhalla HUD with portraits in the TOP-RIGHT corner (default OBS-like
# layout). Tune in input/notes.md if your HUD position differs.
HUD_LEFT_PORTRAIT = {  # the player whose portrait is on the LEFT of the HUD pair (Kaya here)
    "x": 0.8958, "y": 0.0241, "w": 0.0464, "h": 0.0824,
}
HUD_RIGHT_PORTRAIT = {  # the player whose portrait is on the RIGHT of the HUD pair (opponent)
    "x": 0.9427, "y": 0.0241, "w": 0.0464, "h": 0.0824,
}
# Final size of each portrait in the 9:16 output (in output pixels, square).
HUD_OUT_SIZE = 280
# Margin from the corners (output pixels).
HUD_OUT_MARGIN = 36
# Swap corners: source-LEFT portrait → output TOP-RIGHT, source-RIGHT → TOP-LEFT.
HUD_SWAP_CORNERS = True

# --- Whisper ---
WHISPER_MODEL = "large-v3"
WHISPER_LANG = "fr"

# --- Cache ---
def cache_path(name: str) -> Path:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    return WORK_DIR / name
