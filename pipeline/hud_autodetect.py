"""Auto-detect Brawlhalla HUD portrait positions in the source video.

Strategy: portraits are circular icons in the top band of the screen. We
scan the top 20% of the seed frame with HoughCircles, filter to the two
biggest matches in the right region, and report their bounding boxes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from pipeline import config


def main() -> None:
    cap = cv2.VideoCapture(str(config.INPUT_DIR / "2026-05-21 15-01-05.mp4"))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 60)
    ok, f = cap.read()
    cap.release()
    if not ok:
        raise SystemExit("Could not read frame.")

    h, w = f.shape[:2]
    # Look only at the TOP-RIGHT quadrant where the HUD lives.
    y0, y1 = 0, int(h * 0.20)
    x0, x1 = int(w * 0.70), w
    region = f[y0:y1, x0:x1]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)

    # Portraits are roughly 30-70 px radius at 1920x1080 in this game's HUD.
    # Loosened param2 and broader radius range to catch them.
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1, minDist=40,
        param1=100, param2=20, minRadius=25, maxRadius=80,
    )

    if circles is None:
        print("No circles detected. Falling back to manual coords.")
        return
    circles = np.round(circles[0, :]).astype(int)

    # Convert to absolute coords and pick the 2 biggest.
    abs_circles = [(cx + x0, cy + y0, r) for (cx, cy, r) in circles]
    abs_circles.sort(key=lambda c: -c[2])
    picks = abs_circles[:6]
    # Among picks, choose the 2 with the most similar radii AND closest y AND
    # nearby in x — the actual HUD pair.
    best_pair = None
    best_score = 1e18
    for i in range(len(picks)):
        for j in range(i + 1, len(picks)):
            ax, ay, ar = picks[i]
            bx, by, br = picks[j]
            if abs(ar - br) > 8:
                continue
            if abs(ay - by) > 10:
                continue
            dx = abs(ax - bx)
            if dx < ar * 1.5 or dx > ar * 4:
                continue
            score = abs(ar - br) * 10 + abs(ay - by) * 3 + abs(dx - ar * 2.4)
            if score < best_score:
                best_score = score
                best_pair = sorted(
                    [(ax, ay, ar), (bx, by, br)], key=lambda c: c[0]
                )

    if best_pair is None:
        print("No HUD pair found among detected circles. Top picks:")
        for cx, cy, r in picks:
            print(f"  ({cx}, {cy}) r={r}")
        return

    print(f"Source: {w}x{h}")
    print(f"Detected HUD pair (left, right):")
    for tag, (cx, cy, r) in zip(("LEFT", "RIGHT"), best_pair):
        print(f"  {tag}: center=({cx}, {cy}) radius={r}")

    # Draw debug overlay on a copy of the frame so we can verify.
    dbg = f.copy()
    for cx, cy, r in best_pair:
        cv2.circle(dbg, (cx, cy), r, (0, 255, 0), 3)
        cv2.circle(dbg, (cx, cy), 2, (0, 0, 255), -1)
    out_dbg = config.WORK_DIR / "_hud_detect.jpg"
    cv2.imwrite(str(out_dbg), dbg[:int(h * 0.25)])
    print(f"Debug → {out_dbg}")

    # Build crop rects: square boxes centered on each circle, side = 2.2 * r
    # (leaves a bit of breathing room around the portrait, including the
    # stocks number below the head).
    print()
    print("Suggested config.py values:")
    for tag, (cx, cy, r) in zip(("LEFT", "RIGHT"), best_pair):
        side = int(round(r * 2.4))
        x = cx - side // 2
        y = cy - side // 2
        # Print as fractions for config.py
        fx = x / w
        fy = y / h
        fw = side / w
        fh = side / h
        print(f'HUD_{tag}_PORTRAIT = {{"x": {fx:.4f}, "y": {fy:.4f}, '
              f'"w": {fw:.4f}, "h": {fh:.4f}}}')
        # Save a crop preview.
        crop = f[max(0, y):y + side, max(0, x):x + side]
        cv2.imwrite(str(config.WORK_DIR / f"_hud_auto_{tag.lower()}.jpg"), crop)


if __name__ == "__main__":
    main()
