"""Preview HUD crop variants. Tweak the right portrait so the opponent's head
is well centered, like Kaya's on the left.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from pipeline import config


def crop(frame: np.ndarray, hud: dict[str, float]) -> np.ndarray:
    h, w = frame.shape[:2]
    x = int(hud["x"] * w)
    y = int(hud["y"] * h)
    cw = int(hud["w"] * w)
    ch = int(hud["h"] * h)
    return frame[y:y + ch, x:x + cw]


def main() -> None:
    cap = cv2.VideoCapture(str(config.INPUT_DIR / "2026-05-21 15-01-05.mp4"))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 60)
    ok, f = cap.read()
    cap.release()
    if not ok:
        raise SystemExit("Could not read frame.")

    # Lock left to the validated C4 (Kaya). Try several right candidates.
    LEFT = {"x": 0.895, "y": 0.030, "w": 0.052, "h": 0.082}

    variants = [
        ("L1_x=0.954",  LEFT, {"x": 0.954, "y": 0.030, "w": 0.052, "h": 0.082}),
        ("L2_x=0.950",  LEFT, {"x": 0.950, "y": 0.030, "w": 0.052, "h": 0.082}),
        ("L3_x=0.946",  LEFT, {"x": 0.946, "y": 0.030, "w": 0.052, "h": 0.082}),
        ("L4_x=0.942",  LEFT, {"x": 0.942, "y": 0.030, "w": 0.052, "h": 0.082}),
        ("L5_x=0.938",  LEFT, {"x": 0.938, "y": 0.030, "w": 0.052, "h": 0.082}),
    ]

    rows: list[np.ndarray] = []
    target_h = 220
    for label, left, right in variants:
        cl = crop(f, left)
        cr = crop(f, right)

        def fit(im: np.ndarray) -> np.ndarray:
            h, w = im.shape[:2]
            if h == 0 or w == 0:
                return np.zeros((target_h, target_h, 3), np.uint8)
            scale = target_h / h
            return cv2.resize(im, (int(w * scale), target_h))

        cl = fit(cl)
        cr = fit(cr)
        gap = np.full((target_h, 24, 3), 60, np.uint8)
        row = np.hstack([cl, gap, cr])
        label_strip = np.full((44, row.shape[1], 3), 30, np.uint8)
        cv2.putText(label_strip, label, (12, 30),
                    cv2.FONT_HERSHEY_DUPLEX, 0.75, (200, 220, 255), 1, cv2.LINE_AA)
        rows.append(np.vstack([label_strip, row]))

    max_w = max(r.shape[1] for r in rows)
    padded = []
    for r in rows:
        if r.shape[1] < max_w:
            pad = np.full((r.shape[0], max_w - r.shape[1], 3), 30, np.uint8)
            r = np.hstack([r, pad])
        padded.append(r)
        padded.append(np.full((12, max_w, 3), 60, np.uint8))

    grid = np.vstack(padded)
    out = config.WORK_DIR / "_hud_variants.jpg"
    cv2.imwrite(str(out), grid)
    print(f"Saved → {out}")


if __name__ == "__main__":
    main()
