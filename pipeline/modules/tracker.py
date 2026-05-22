"""Per-frame position tracking of Kaya in the gameplay video.

Output schema (cached as JSON):
{
  "fps": 60.0,
  "width": 1920,
  "height": 1080,
  "track": [
    {"frame": 0, "x": 950.0, "y": 540.0, "w": 80, "h": 100, "ok": true},
    ...
  ]
}

Two backends:
- `sam2` (default): Meta's SAM 2 video predictor. The user clicks a bbox on Kaya
  in the seed frame, SAM2 segments the object and propagates the mask through
  the whole video. Robust to occlusion, fast moves, animation changes. SOTA.
- `csrt` (fallback): OpenCV CSRT tracker. Used only if SAM2 isn't installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import io_utils
from .. import config


# SAM2 model selection. `small` is the best quality/speed tradeoff on M2.
SAM2_CHECKPOINT = config.ROOT / "models" / "sam2.1_hiera_small.pt"
SAM2_CONFIG = "configs/sam2.1/sam2.1_hiera_s.yaml"

# Resolution at which we feed frames to SAM2. 540p is plenty for object
# localization (we just need bbox centers) and ~4x faster than 1080p.
SAM2_FRAME_HEIGHT = 540

# SAM2 internal image size. Default is 1024; we use 384 because:
# - we only need bbox centers (Kaya is ~50px wide at 540p source frames)
# - on M2 CPU, 1024 is ~1.9s/frame vs 0.2s/frame at 384
# - quality-wise, 384 is fine for our use case
SAM2_IMAGE_SIZE = 384

# Run SAM2 on every Nth frame, then linearly interpolate Kaya's position
# between samples. The 1€ filter downstream smooths anyway. stride=8 means
# ~7 samples/sec for a 60fps video, which is plenty for a single character.
SAM2_STRIDE = 8

# Skip the backward propagation pass. The seed is at ~1s into the video, so we
# only lose ~60 source frames at the very start (the intro). Backward pass
# would double the wall-clock time.
SAM2_BACKWARD = False

# Chunk SAM2 propagation. SAM2 keeps every loaded frame's tensor in
# `inference_state["images"]` even with offload, so processing 1500+ frames in
# one pass blows past the 8 GB RAM of a MacBook Air M2. We process in chunks,
# tear down the predictor between chunks (releases tensors), and re-seed using
# the last known bbox of the previous chunk.
SAM2_CHUNK_SIZE = 200


@dataclass
class TrackPoint:
    frame: int
    x: float
    y: float
    w: float
    h: float
    ok: bool


def _pick_seed_bbox(video: Path, seek_t: float = 1.0) -> tuple[int, tuple[int, int, int, int], np.ndarray]:
    """Open the video at `seek_t` seconds, let the user draw a bbox around Kaya.

    Returns (frame_index, bbox(x,y,w,h) in original-resolution pixels, frame_bgr).
    """
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    seek_frame = max(0, int(round(seek_t * fps)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, seek_frame)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit("Could not read seed frame from video.")

    print()
    print("[tracker] A window will open. Draw a rectangle around KAYA, then ENTER.")
    print()

    h, w = frame.shape[:2]
    scale = min(1.0, 1280 / w, 720 / h)
    disp = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1.0 else frame.copy()

    bbox = cv2.selectROI("Click & drag around KAYA, ENTER to confirm", disp, showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()

    if bbox == (0, 0, 0, 0):
        raise SystemExit("[tracker] No bbox selected. Aborting.")

    if scale < 1.0:
        bbox = tuple(int(round(v / scale)) for v in bbox)
    return seek_frame, bbox, frame


# --------------------------------------------------------------------------
# SAM2 backend
# --------------------------------------------------------------------------

def _extract_frames_for_sam2(video: Path, out_dir: Path, target_height: int, stride: int = 1) -> tuple[int, int, int]:
    """Extract frames of `video` as JPEG into `out_dir/00000.jpg` etc.

    With `stride > 1`, only every Nth frame of the source video is written.
    The output filenames are still sequential (00000, 00001, ...) — the
    caller is responsible for mapping back to source frame indices via stride.

    Returns (n_extracted, src_w, src_h).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video))
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    target_w = int(round(src_w * target_height / src_h))
    target_w -= target_w % 2

    existing = sorted(out_dir.glob("*.jpg"))
    if existing:
        return len(existing), src_w, src_h

    print(f"[tracker] Extracting frames at {target_w}x{target_height}, stride={stride}...")
    vf = f"scale={target_w}:{target_height}"
    if stride > 1:
        vf = f"select='not(mod(n\\,{stride}))',{vf}"

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video),
        "-vf", vf,
        "-vsync", "vfr",
        "-q:v", "3",
        "-start_number", "0",
        str(out_dir / "%05d.jpg"),
    ]
    io_utils.run(cmd)
    n = len(list(out_dir.glob("*.jpg")))
    return n, src_w, src_h


def _mask_to_bbox(mask: np.ndarray) -> tuple[float, float, float, float] | None:
    """Convert a boolean mask to (cx, cy, w, h). Returns None if mask empty."""
    if mask.ndim == 3:
        mask = mask[0]
    ys, xs = np.where(mask > 0)
    if xs.size == 0:
        return None
    x0, x1 = float(xs.min()), float(xs.max())
    y0, y1 = float(ys.min()), float(ys.max())
    return ((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0 + 1, y1 - y0 + 1)


def _sam2_track(video: Path, seek_frame: int, bbox_full: tuple[int, int, int, int],
                src_w: int, src_h: int, n_frames_total: int, cache_dir: Path) -> list[TrackPoint]:
    """Run SAM2 segmentation on subsampled frames and interpolate to all source frames.

    To stay within the memory budget of a MacBook Air M2 (8 GB), we process the
    sampled frames in CHUNKS of `SAM2_CHUNK_SIZE`. SAM2 keeps every loaded
    frame's tensor in `inference_state["images"]` even with offload, so a
    single 1500-frame propagation pass blows the RAM. Between chunks we tear
    down the predictor (releases tensors), restart, and re-seed using the last
    known bbox.
    """
    import gc
    import shutil

    import torch
    from sam2.build_sam import build_sam2_video_predictor

    if not SAM2_CHECKPOINT.exists():
        raise FileNotFoundError(
            f"SAM2 checkpoint missing: {SAM2_CHECKPOINT}\n"
            f"Re-run setup.sh or download manually:\n"
            f"  curl -L -o {SAM2_CHECKPOINT} "
            f"https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt"
        )

    # 1. Stage subsampled frames once.
    key = io_utils.cache_key(video)
    frames_dir = cache_dir / f"frames-{key}-h{SAM2_FRAME_HEIGHT}-s{SAM2_STRIDE}"
    n_sampled, _src_w, _src_h = _extract_frames_for_sam2(
        video, frames_dir, SAM2_FRAME_HEIGHT, stride=SAM2_STRIDE
    )
    print(f"[tracker] {n_sampled} sampled frames staged ({frames_dir.name})")

    # 2. SAM2 image dims and src↔sam scale factors.
    sam_w = int(round(src_w * SAM2_FRAME_HEIGHT / src_h))
    sam_w -= sam_w % 2
    sam_h = SAM2_FRAME_HEIGHT
    sx = src_w / sam_w
    sy = src_h / sam_h

    x, y, w, h = bbox_full
    seed_box_sam = np.array([x / sx, y / sy, (x + w) / sx, (y + h) / sy], dtype=np.float32)
    seed_sampled_idx = max(0, min(n_sampled - 1, round(seek_frame / SAM2_STRIDE)))

    device = torch.device("cpu")
    print(f"[tracker] SAM2 device: {device} (image_size={SAM2_IMAGE_SIZE})")

    # 3. Run SAM2 in chunks. We do forward chunks starting at the seed, and
    #    optionally backward chunks before the seed.
    sampled: dict[int, tuple[float, float, float, float] | None] = {}
    chunks_root = cache_dir / f"sam2-chunks-{key}"
    chunks_root.mkdir(parents=True, exist_ok=True)

    def run_chunk(chunk_dir: Path, seed_local_idx: int, seed_box: np.ndarray,
                  global_offset: int, reverse: bool = False) -> tuple[float, float, float, float] | None:
        """Run SAM2 on a single chunk dir. Returns the last good bbox in SAM2
        coords (for chaining to the next chunk). `global_offset` is the index
        in the global sampled-frame array of `chunk_dir/00000.jpg`.
        """
        predictor = build_sam2_video_predictor(
            SAM2_CONFIG, str(SAM2_CHECKPOINT), device=device,
            hydra_overrides_extra=[f"++model.image_size={SAM2_IMAGE_SIZE}"],
        )
        state = predictor.init_state(
            video_path=str(chunk_dir),
            offload_video_to_cpu=True,
            offload_state_to_cpu=True,
            async_loading_frames=False,
        )
        predictor.add_new_points_or_box(state, frame_idx=seed_local_idx, obj_id=1, box=seed_box)

        last_box: tuple[float, float, float, float] | None = None
        last_idx = -1
        for f_local, _obj, mask_logits in predictor.propagate_in_video(state, reverse=reverse):
            m = (mask_logits[0] > 0).cpu().numpy()
            b = _mask_to_bbox(m)
            f_global = global_offset + f_local
            sampled[f_global] = b
            if b is not None:
                last_box = b
                last_idx = f_local

        # Release everything before next chunk.
        del predictor, state
        gc.collect()
        return last_box, last_idx

    # Build the chunk plan.
    # Forward: sampled-frame indices [seed_sampled_idx .. n_sampled-1]
    fwd_indices = list(range(seed_sampled_idx, n_sampled))
    bwd_indices = list(range(seed_sampled_idx, -1, -1))  # includes seed for re-anchor

    def make_chunk_dir(global_indices: list[int], chunk_id: str) -> Path:
        d = chunks_root / chunk_id
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        for local_i, gi in enumerate(global_indices):
            src = frames_dir / f"{gi:05d}.jpg"
            dst = d / f"{local_i:05d}.jpg"
            # Hardlink to save disk space — same FS so always works.
            try:
                dst.hardlink_to(src)
            except OSError:
                shutil.copy2(src, dst)
        return d

    # Forward chunks.
    print("[tracker] SAM2 forward propagation in chunks...")
    cursor = 0
    cur_box = seed_box_sam.copy()
    cur_seed_local = 0  # in the first chunk, the seed is at local index 0
    chunk_n = 0
    while cursor < len(fwd_indices):
        end = min(len(fwd_indices), cursor + SAM2_CHUNK_SIZE)
        global_indices = fwd_indices[cursor:end]
        chunk_dir = make_chunk_dir(global_indices, f"fwd-{chunk_n:03d}")
        print(f"[tracker]   chunk {chunk_n+1} fwd: sampled[{global_indices[0]}..{global_indices[-1]}] ({len(global_indices)} frames)")
        last_box, _last_idx = run_chunk(chunk_dir, cur_seed_local, cur_box, global_offset=global_indices[0], reverse=False)
        shutil.rmtree(chunk_dir)
        if last_box is None:
            print("[tracker]   WARN: chunk produced no masks, stopping forward")
            break
        # Next chunk starts where this one ended; seed = last good box.
        cur_box = np.array([last_box[0] - last_box[2] / 2, last_box[1] - last_box[3] / 2,
                            last_box[0] + last_box[2] / 2, last_box[1] + last_box[3] / 2],
                           dtype=np.float32)
        cur_seed_local = 0
        cursor = end
        chunk_n += 1

    # Backward chunks (only if seed is not at the very start).
    if SAM2_BACKWARD and seed_sampled_idx > 0:
        print("[tracker] SAM2 backward propagation in chunks...")
        cursor = 0
        cur_box = seed_box_sam.copy()
        chunk_n = 0
        while cursor < len(bwd_indices):
            end = min(len(bwd_indices), cursor + SAM2_CHUNK_SIZE)
            global_indices = bwd_indices[cursor:end]
            chunk_dir = make_chunk_dir(list(reversed(global_indices)), f"bwd-{chunk_n:03d}")
            # In a reversed chunk, the seed (the last sampled idx of the chunk)
            # is at the END (local idx = len-1).
            local_seed = len(global_indices) - 1
            print(f"[tracker]   chunk {chunk_n+1} bwd: sampled[{global_indices[-1]}..{global_indices[0]}] ({len(global_indices)} frames)")
            # We need to re-map global offset for the reversed chunk:
            # local 0 corresponds to global_indices[-1] (the lowest index after reversal in dir).
            # Actually we wrote the dir in reversed order so dir[i] = bwd_indices[cursor + (len-1-i)].
            # For simplicity, we run forward over the dir (since SAM2 just sees JPGs) and remap:
            predictor = build_sam2_video_predictor(
                SAM2_CONFIG, str(SAM2_CHECKPOINT), device=device,
                hydra_overrides_extra=[f"++model.image_size={SAM2_IMAGE_SIZE}"],
            )
            state = predictor.init_state(
                video_path=str(chunk_dir), offload_video_to_cpu=True, offload_state_to_cpu=True,
            )
            predictor.add_new_points_or_box(state, frame_idx=local_seed, obj_id=1, box=cur_box)
            last_box = None
            for f_local, _obj, mask_logits in predictor.propagate_in_video(state, reverse=True):
                m = (mask_logits[0] > 0).cpu().numpy()
                b = _mask_to_bbox(m)
                # Map local index back to the global sampled index.
                gi = global_indices[len(global_indices) - 1 - f_local]
                if gi not in sampled:
                    sampled[gi] = b
                if b is not None:
                    last_box = b
            del predictor, state
            gc.collect()
            shutil.rmtree(chunk_dir)
            if last_box is None:
                break
            cur_box = np.array([last_box[0] - last_box[2] / 2, last_box[1] - last_box[3] / 2,
                                last_box[0] + last_box[2] / 2, last_box[1] + last_box[3] / 2],
                               dtype=np.float32)
            cursor = end
            chunk_n += 1

    # Cleanup chunk root if empty.
    try:
        chunks_root.rmdir()
    except OSError:
        pass

    # 4. Interpolate to per-source-frame.
    keys = sorted(k for k, v in sampled.items() if v is not None)
    if not keys:
        raise SystemExit("[tracker] SAM2 produced no masks. Check the seed bbox.")
    print(f"[tracker] SAM2 done: {len(keys)}/{n_sampled} sampled frames produced a mask")

    sample_frames = np.array([k * SAM2_STRIDE for k in keys], dtype=np.float64)
    cxs = np.array([sampled[k][0] for k in keys])
    cys = np.array([sampled[k][1] for k in keys])
    bws = np.array([sampled[k][2] for k in keys])
    bhs = np.array([sampled[k][3] for k in keys])

    all_frames = np.arange(n_frames_total, dtype=np.float64)
    cx_full = np.interp(all_frames, sample_frames, cxs)
    cy_full = np.interp(all_frames, sample_frames, cys)
    bw_full = np.interp(all_frames, sample_frames, bws)
    bh_full = np.interp(all_frames, sample_frames, bhs)

    sampled_set = set(int(f) for f in sample_frames)
    pts: list[TrackPoint] = []
    for fi in range(n_frames_total):
        # `ok=True` for source frames within the sampled range. Frames outside
        # the [first_sample, last_sample] interval are extrapolated and marked
        # not-ok so the camera planner can fall back to defaults.
        ok = sample_frames[0] <= fi <= sample_frames[-1]
        pts.append(TrackPoint(
            frame=fi,
            x=float(cx_full[fi]) * sx,
            y=float(cy_full[fi]) * sy,
            w=float(bw_full[fi]) * sx,
            h=float(bh_full[fi]) * sy,
            ok=ok,
        ))
    return pts


# --------------------------------------------------------------------------
# CSRT fallback
# --------------------------------------------------------------------------

def _csrt_track(video: Path, seek_frame: int, bbox: tuple[int, int, int, int], n_frames: int) -> list[TrackPoint]:
    """OpenCV CSRT tracker, forward + backward from the seed. Last-resort fallback."""
    cap = cv2.VideoCapture(str(video))

    tracker = cv2.TrackerCSRT_create()
    cap.set(cv2.CAP_PROP_POS_FRAMES, seek_frame)
    ok, frame = cap.read()
    if not ok:
        cap.release()
        raise SystemExit("Failed to read seed frame.")
    tracker.init(frame, bbox)

    forward: list[TrackPoint] = []
    last_ok_bbox = bbox
    for fi in range(seek_frame, n_frames):
        if fi != seek_frame:
            ok, frame = cap.read()
            if not ok:
                break
        ok, b = tracker.update(frame) if fi != seek_frame else (True, bbox)
        if ok:
            x, y, w, h = b
            last_ok_bbox = b
        else:
            x, y, w, h = last_ok_bbox
        forward.append(TrackPoint(fi, x + w / 2, y + h / 2, w, h, ok))

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    backward_frames: list[np.ndarray] = []
    for _ in range(seek_frame + 1):
        ok, f = cap.read()
        if not ok:
            break
        backward_frames.append(f)
    cap.release()

    backward: list[TrackPoint] = []
    if seek_frame > 0:
        bw_tracker = cv2.TrackerCSRT_create()
        bw_tracker.init(backward_frames[seek_frame], bbox)
        last_ok_bbox = bbox
        for fi in range(seek_frame - 1, -1, -1):
            ok, b = bw_tracker.update(backward_frames[fi])
            if ok:
                x, y, w, h = b
                last_ok_bbox = b
            else:
                x, y, w, h = last_ok_bbox
            backward.append(TrackPoint(fi, x + w / 2, y + h / 2, w, h, ok))
        backward.reverse()

    return backward + forward


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def track(video: Path, video_meta: dict[str, Any], cache_dir: Path, force: bool = False) -> dict[str, Any]:
    """Track Kaya across the entire video. Cached by source hash."""
    key = io_utils.cache_key(video)
    cache_path = cache_dir / f"track-{key}.json"
    if cache_path.exists() and not force:
        print(f"[tracker] Cache hit: {cache_path.name}")
        return io_utils.read_json(cache_path)

    n_frames = video_meta["n_frames"]
    src_w = video_meta["width"]
    src_h = video_meta["height"]

    seek_frame, bbox, _seed = _pick_seed_bbox(video, seek_t=1.0)
    print(f"[tracker] Seed frame={seek_frame} bbox={bbox}")

    backend = "unknown"
    try:
        import sam2  # noqa: F401
        if SAM2_CHECKPOINT.exists():
            print("[tracker] Backend: SAM2 (sam2.1_hiera_small)")
            backend = "sam2"
            pts = _sam2_track(video, seek_frame, bbox, src_w, src_h, n_frames, cache_dir)
        else:
            raise FileNotFoundError("checkpoint missing")
    except Exception as e:
        print(f"[tracker] SAM2 unavailable ({e}); falling back to CSRT.")
        backend = "csrt"
        pts = _csrt_track(video, seek_frame, bbox, n_frames)

    result = {
        "fps": video_meta["fps"],
        "width": src_w,
        "height": src_h,
        "seed_frame": seek_frame,
        "backend": backend,
        "track": [
            {"frame": p.frame, "x": p.x, "y": p.y, "w": p.w, "h": p.h, "ok": bool(p.ok)}
            for p in pts
        ],
    }
    io_utils.write_json(cache_path, result)
    n_ok = sum(1 for p in pts if p.ok)
    print(f"[tracker] Done [{backend}]: {n_ok}/{len(pts)} frames tracked OK ({100*n_ok/len(pts):.1f}%) → {cache_path.name}")
    return result
