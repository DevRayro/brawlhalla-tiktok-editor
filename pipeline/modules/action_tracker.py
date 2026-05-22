"""Tracker backend based on the action-centroid heuristic.

Idea: instead of identifying Kaya specifically (SAM2-style), we detect WHERE
the action is happening on screen by computing the per-frame motion centroid
(weighted by inter-frame difference) and use that as the camera target.

Pros over SAM2:
- No manual seed click.
- Cannot drift onto a static rock (rocks don't move → no motion → no centroid contribution).
- Robust to character animation, occlusion, weapon attacks.
- Computed in ~30s for a 3-min video on a MacBook Air M2.
- No GPU / no model download.

Cons:
- Follows "the action" globally, not Kaya specifically. In team modes, the
  centroid will average multiple ongoing fights. In 1v1 / FFA where there's
  one main fight at a time, this is exactly what we want.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import io_utils
from . import camera as cam_mod


def track(video: Path, video_meta: dict[str, Any], cache_dir: Path,
          force: bool = False) -> dict[str, Any]:
    """Compute action-centroid track and persist in the same schema as SAM2."""
    key = io_utils.cache_key(video)
    cache_path = cache_dir / f"track-action-{key}.json"
    if cache_path.exists() and not force:
        print(f"[tracker:action] Cache hit: {cache_path.name}")
        return io_utils.read_json(cache_path)

    n_frames = video_meta["n_frames"]
    src_w = video_meta["width"]
    src_h = video_meta["height"]
    fps = video_meta["fps"]

    print(f"[tracker:action] Computing action centroid over {n_frames} frames...")
    field = cam_mod._action_field(video, n_frames, fps)

    cx = field["cx"]
    cy = field["cy"]
    spread = field["spread"]
    intensity = field["intensity"]

    # Mark a frame as ok=True when we actually have motion (intensity above a
    # small threshold). When everything is still, the centroid is meaningless;
    # we still emit the value but flag it not-ok so the planner can hold the
    # last good position.
    ok_thresh = 0.05
    pts = []
    last_good_x = src_w / 2
    last_good_y = src_h / 2
    for fi in range(n_frames):
        is_ok = bool(intensity[fi] > ok_thresh)
        if is_ok:
            last_good_x = float(cx[fi])
            last_good_y = float(cy[fi])
            x, y = last_good_x, last_good_y
        else:
            x, y = last_good_x, last_good_y
        pts.append({
            "frame": fi,
            "x": x,
            "y": y,
            "w": float(spread[fi] * 2),
            "h": float(spread[fi] * 2),
            "ok": is_ok,
        })

    n_ok = sum(1 for p in pts if p["ok"])
    print(f"[tracker:action] {n_ok}/{n_frames} frames had detectable action ({100*n_ok/n_frames:.1f}%)")

    result = {
        "fps": fps,
        "width": src_w,
        "height": src_h,
        "seed_frame": 0,
        "backend": "action",
        "track": pts,
    }
    io_utils.write_json(cache_path, result)
    print(f"[tracker:action] → {cache_path.name}")
    return result
