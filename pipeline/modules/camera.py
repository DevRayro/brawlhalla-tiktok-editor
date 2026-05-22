"""Compute a per-frame camera plan: (cx, cy, zoom) in source-pixel coordinates.

Output schema:
{
  "fps": 60.0,
  "width": 1920, "height": 1080,
  "out_w": 1080, "out_h": 1920,
  "frames": [
    {"cx": 950.0, "cy": 540.0, "zoom": 1.4},
    ...
  ]
}

Math summary
------------
The output is a 9:16 crop of the source 16:9 video. The base crop window has
height = source_height and width = source_height * 9/16. Inside that, we apply
a digital zoom: a higher `zoom` means a smaller window centered on (cx, cy).

The composition layer (Remotion) takes care of the actual cropping; we just
emit clean numbers per frame.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d

from . import io_utils
from .. import config


# ---------- 1€ filter -----------------------------------------------------

class OneEuroFilter:
    """Low-lag low-pass filter for noisy signals (Casiez et al., 2012).

    Tracks a 1D signal and adapts cutoff to the speed of the signal:
    smooth when slow, responsive when fast.
    """
    def __init__(self, freq: float, mincutoff: float = 1.0, beta: float = 0.0, dcutoff: float = 1.0):
        self.freq = freq
        self.mincutoff = mincutoff
        self.beta = beta
        self.dcutoff = dcutoff
        self._x_prev: float | None = None
        self._dx_prev = 0.0

    @staticmethod
    def _alpha(cutoff: float, freq: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        te = 1.0 / freq
        return 1.0 / (1.0 + tau / te)

    def __call__(self, x: float) -> float:
        if self._x_prev is None:
            self._x_prev = x
            return x
        dx = (x - self._x_prev) * self.freq
        a_d = self._alpha(self.dcutoff, self.freq)
        dx_hat = a_d * dx + (1 - a_d) * self._dx_prev
        cutoff = self.mincutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, self.freq)
        x_hat = a * x + (1 - a) * self._x_prev
        self._x_prev = x_hat
        self._dx_prev = dx_hat
        return x_hat


# ---------- Action intensity ---------------------------------------------

def _action_field(video: Path, n_frames: int, fps: float, sample_every: int = 4) -> dict[str, np.ndarray]:
    """Per-frame action intensity AND per-frame motion centroid.

    Returns:
        {
          "intensity": (n_frames,) float in [0, 1],
          "cx":        (n_frames,) source-pixel x of motion centroid,
          "cy":        (n_frames,) source-pixel y of motion centroid,
          "spread":    (n_frames,) std of motion in source pixels (action zone size)
        }

    Computes everything in a single video read pass. The Brawlhalla UI bands
    (top portraits, bottom score) are masked out so they don't dominate the
    centroid when characters are still.
    """
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    target_h = 240
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    target_w = int(src_w * target_h / src_h)

    # UI mask: blank out the top 14% (portraits + names) and bottom 8% (score)
    # so they don't influence the motion centroid.
    ui_top = int(target_h * 0.14)
    ui_bot = int(target_h * 0.92)

    # Build a column / row index grid once for centroid math.
    yy, xx = np.mgrid[0:target_h, 0:target_w].astype(np.float32)

    sampled_idx: list[int] = []
    intens: list[float] = []
    cxs: list[float] = []
    cys: list[float] = []
    sprs: list[float] = []
    prev_gray: np.ndarray | None = None

    fi = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if fi % sample_every == 0:
            small = cv2.resize(frame, (target_w, target_h))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if prev_gray is not None:
                diff = cv2.absdiff(gray, prev_gray).astype(np.float32)
                # Mask out UI bands.
                diff[:ui_top, :] = 0
                diff[ui_bot:, :] = 0
                total = float(diff.sum())
                if total > 1e-3:
                    cx_small = float((diff * xx).sum() / total)
                    cy_small = float((diff * yy).sum() / total)
                    # Spread: use median absolute deviation rather than stddev,
                    # and only over pixels that actually moved (>10% of peak),
                    # to avoid background scrolling / particles inflating it.
                    threshold = float(diff.max()) * 0.10
                    moving_mask = diff > threshold
                    if moving_mask.any():
                        moving_x = xx[moving_mask]
                        moving_y = yy[moving_mask]
                        # Use 80th percentile distance from centroid as spread.
                        d = np.sqrt((moving_x - cx_small) ** 2 + (moving_y - cy_small) ** 2)
                        spread_small = float(np.percentile(d, 80))
                    else:
                        spread_small = 0.0
                else:
                    cx_small = target_w / 2
                    cy_small = target_h / 2
                    spread_small = 0.0
                # Scale up to source-pixel coords.
                scale = src_w / target_w
                cxs.append(cx_small * scale)
                cys.append(cy_small * scale)
                sprs.append(spread_small * scale)
                intens.append(total / (target_w * target_h))
                sampled_idx.append(fi)
            prev_gray = gray
        fi += 1
    cap.release()

    if not intens:
        z = np.zeros(n_frames)
        return {
            "intensity": z,
            "cx": np.full(n_frames, src_w / 2),
            "cy": np.full(n_frames, src_h / 2),
            "spread": z,
        }

    # Per-frame interpolation.
    fs = np.arange(n_frames, dtype=np.float64)
    sampled_idx_arr = np.array(sampled_idx, dtype=np.float64)
    intensity = np.interp(fs, sampled_idx_arr, intens)
    cx_full = np.interp(fs, sampled_idx_arr, cxs)
    cy_full = np.interp(fs, sampled_idx_arr, cys)
    spread_full = np.interp(fs, sampled_idx_arr, sprs)

    # Smooth.
    sigma = max(1.0, fps * 0.5)
    intensity = gaussian_filter1d(intensity, sigma=sigma)
    cx_full = gaussian_filter1d(cx_full, sigma=sigma)
    cy_full = gaussian_filter1d(cy_full, sigma=sigma)
    spread_full = gaussian_filter1d(spread_full, sigma=sigma)

    # Cap spread at a reasonable fraction of source width. Raw spread tends to
    # over-estimate because particle effects, attacks, and background scrolling
    # contribute. For a typical Brawlhalla 1v1, the real fighter-to-fighter
    # distance rarely exceeds 35% of the screen width.
    spread_full = np.minimum(spread_full, src_w * 0.14)

    # Normalize intensity.
    lo, hi = np.percentile(intensity, 5), np.percentile(intensity, 95)
    if hi - lo > 1e-6:
        intensity = np.clip((intensity - lo) / (hi - lo), 0, 1)
    else:
        intensity = np.zeros_like(intensity)

    return {
        "intensity": intensity,
        "cx": cx_full,
        "cy": cy_full,
        "spread": spread_full,
    }


def _action_intensity(video: Path, n_frames: int, fps: float, sample_every: int = 4) -> np.ndarray:
    """Backward-compat wrapper: returns just the intensity."""
    return _action_field(video, n_frames, fps, sample_every)["intensity"]


# ---------- Crop math -----------------------------------------------------

def _base_crop_size(src_w: int, src_h: int, out_w: int, out_h: int) -> tuple[float, float]:
    """At zoom=1.0, the crop window (in source pixels) that maps to the 9:16 output."""
    # 9:16 window inscribed in the source: full height, narrow width.
    crop_w = src_h * out_w / out_h
    crop_h = float(src_h)
    if crop_w > src_w:
        # Source narrower than 9:16: full width, crop height.
        crop_w = float(src_w)
        crop_h = src_w * out_h / out_w
    return crop_w, crop_h


def _clamp_center(cx: float, cy: float, crop_w: float, crop_h: float, src_w: int, src_h: int) -> tuple[float, float]:
    """Keep the crop window fully inside the source frame."""
    half_w, half_h = crop_w / 2, crop_h / 2
    cx = min(max(cx, half_w), src_w - half_w)
    cy = min(max(cy, half_h), src_h - half_h)
    return cx, cy


# ---------- Plan ----------------------------------------------------------

def plan(track_data: dict[str, Any], video: Path, cache_dir: Path,
         out_w: int = config.OUT_W, out_h: int = config.OUT_H,
         highlights_sec: list[float] | None = None) -> dict[str, Any]:
    """Build the per-frame camera plan."""
    key = io_utils.cache_key(video)
    cache_path = cache_dir / f"camera-{key}.json"
    if cache_path.exists():
        print(f"[camera] Cache hit: {cache_path.name}")
        return io_utils.read_json(cache_path)

    fps = track_data["fps"]
    src_w = track_data["width"]
    src_h = track_data["height"]
    track = track_data["track"]
    n = len(track)
    if n == 0:
        raise SystemExit("[camera] Empty track data.")

    # 1) raw position arrays
    xs = np.array([p["x"] for p in track], dtype=np.float64)
    ys = np.array([p["y"] for p in track], dtype=np.float64)

    # Replace failed-track frames with last good value.
    last_good_x, last_good_y = xs[0], ys[0]
    for i, p in enumerate(track):
        if p["ok"]:
            last_good_x, last_good_y = xs[i], ys[i]
        else:
            xs[i], ys[i] = last_good_x, last_good_y

    # 2) Compute action field (intensity + motion centroid + spread).
    print("[camera] Computing action field...")
    field = _action_field(video, n, fps)
    action = field["intensity"]
    acx = field["cx"]
    acy = field["cy"]

    backend_str = track_data.get("backend", "sam2")  # legacy default

    # 3) DRIFT DETECTION + BLEND.
    #    SAM2 sometimes locks onto static decor (a rock, a platform). Heuristic:
    #    if Kaya's tracked position is far from where the action is happening
    #    AND Kaya isn't moving much, the tracker is stuck.
    #
    #    Skipped when the track itself IS the action centroid (action backend).
    if backend_str == "action":
        print("[camera] Action-centroid backend → drift detection skipped.")
        target_x = xs.copy()
        target_y = ys.copy()
    else:
        print("[camera] Detecting tracker drift vs. action centroid...")
        win = max(int(fps * 0.8), 2)  # ~0.8s window
        # Velocity of the tracked position in pixels/sec.
        vx = np.gradient(xs) * fps
        vy = np.gradient(ys) * fps
        speed = np.sqrt(vx ** 2 + vy ** 2)
        speed_smooth = gaussian_filter1d(speed, sigma=win)

        # Distance between tracked Kaya and action centroid.
        dist_to_action = np.sqrt((xs - acx) ** 2 + (ys - acy) ** 2)
        dist_to_action_smooth = gaussian_filter1d(dist_to_action, sigma=win)

        # Thresholds.
        STATIC_THR = src_w * 0.015 * fps    # ~30 px/sec at 1920p — Kaya hardly moves
        FAR_THR = src_w * 0.20              # >20% of source width away from action

        # blend weight in [0, 1]: 0 = trust SAM2, 1 = trust action centroid.
        static_score = np.clip((STATIC_THR - speed_smooth) / STATIC_THR, 0, 1)  # 1 when static
        far_score = np.clip((dist_to_action_smooth - FAR_THR * 0.5) / FAR_THR, 0, 1)  # 0..1 as distance grows
        drift_w = static_score * far_score
        # Smooth the weight so blend transitions take a few frames.
        drift_w = gaussian_filter1d(drift_w, sigma=fps * 0.5)
        drift_w = np.clip(drift_w, 0, 1)

        n_drift = int((drift_w > 0.5).sum())
        if n_drift > 0:
            print(f"[camera] Drift detected on {n_drift}/{n} frames ({100*n_drift/n:.1f}%): "
                  f"blending toward action centroid.")

        target_x = (1 - drift_w) * xs + drift_w * acx
        target_y = (1 - drift_w) * ys + drift_w * acy

    # 4) smooth with 1€ filter.
    fx = OneEuroFilter(freq=fps, mincutoff=config.ONE_EURO_MINCUTOFF, beta=config.ONE_EURO_BETA)
    fy = OneEuroFilter(freq=fps, mincutoff=config.ONE_EURO_MINCUTOFF, beta=config.ONE_EURO_BETA)
    sx = np.array([fx(v) for v in target_x])
    sy = np.array([fy(v) for v in target_y])

    # Map action [0,1] → zoom: high action = wider (zoom_min), low action = tighter (zoom_max).
    base_zoom = config.ZOOM_MAX - action * (config.ZOOM_MAX - config.ZOOM_MIN)

    # Highlights: force tighter zoom for ~2s windows around each highlight.
    if highlights_sec:
        for t in highlights_sec:
            f0 = int(max(0, (t - 1.0) * fps))
            f1 = int(min(n, (t + 1.5) * fps))
            base_zoom[f0:f1] = config.ZOOM_MAX

    # 4) clamp zoom slew rate (no abrupt jumps).
    max_step = config.ZOOM_SLEW_PER_SEC / fps
    zoom = np.copy(base_zoom)
    for i in range(1, n):
        d = zoom[i] - zoom[i - 1]
        if d > max_step:
            zoom[i] = zoom[i - 1] + max_step
        elif d < -max_step:
            zoom[i] = zoom[i - 1] - max_step

    # 5) Compute the crop window per frame.
    #    SAFETY-MARGIN PASS: after clamping the crop center to the source bounds,
    #    Kaya may end up near the edge of the visible window if he's near the
    #    source border. We then zoom OUT until Kaya sits well inside the crop
    #    (with a margin of CAMERA_SAFETY_MARGIN * crop_w on each side).
    base_w, base_h = _base_crop_size(src_w, src_h, out_w, out_h)
    margin = config.CAMERA_SAFETY_MARGIN
    frames: list[dict[str, float]] = []
    for i in range(n):
        z = float(zoom[i])
        kx, ky = float(sx[i]), float(sy[i])

        # Iteratively reduce zoom until Kaya is inside the crop with margin.
        # We cap the loop at a few iterations; each step lowers zoom by ~10%.
        for _ in range(8):
            cw = base_w / z
            ch = base_h / z
            cx, cy = _clamp_center(kx, ky, cw, ch, src_w, src_h)
            # Kaya's offset from crop center, normalized by half-crop:
            dx = abs(kx - cx) / (cw / 2)
            dy = abs(ky - cy) / (ch / 2)
            # 1.0 means Kaya is exactly at the edge of the crop. We want him
            # within (1 - margin) so there's empty space between him and the
            # crop border.
            if dx <= 1.0 - margin and dy <= 1.0 - margin:
                break
            # Need to widen the field of view → reduce zoom.
            z = max(config.ZOOM_MIN, z * 0.9)

        cw = base_w / z
        ch = base_h / z
        cx, cy = _clamp_center(kx, ky, cw, ch, src_w, src_h)
        frames.append({"cx": cx, "cy": cy, "zoom": z})

    # 6) Re-smooth the per-frame zoom one more time so the safety-margin
    #    adjustments don't create staircase steps. Apply the same slew limit.
    zoom_arr = np.array([f["zoom"] for f in frames])
    for i in range(1, n):
        d = zoom_arr[i] - zoom_arr[i - 1]
        if d > max_step:
            zoom_arr[i] = zoom_arr[i - 1] + max_step
        elif d < -max_step:
            zoom_arr[i] = zoom_arr[i - 1] - max_step
    # Recompute centers with the smoothed zoom.
    for i in range(n):
        z = float(zoom_arr[i])
        cw = base_w / z
        ch = base_h / z
        cx, cy = _clamp_center(float(sx[i]), float(sy[i]), cw, ch, src_w, src_h)
        frames[i] = {"cx": cx, "cy": cy, "zoom": z}

    plan = {
        "fps": fps,
        "width": src_w,
        "height": src_h,
        "out_w": out_w,
        "out_h": out_h,
        "base_crop_w": base_w,
        "base_crop_h": base_h,
        "frames": frames,
    }
    io_utils.write_json(cache_path, plan)
    print(f"[camera] Plan ready ({n} frames) → {cache_path.name}")
    return plan
