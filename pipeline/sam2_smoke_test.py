"""Quick SAM2 smoke test on a tiny slice of frames.

Verifies the SAM2 install works end-to-end before running the full pipeline.
Run with:  python pipeline/sam2_smoke_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shutil
import numpy as np

from pipeline import config
from pipeline.modules import tracker as tr
from pipeline.modules.tracker import SAM2_CHECKPOINT, SAM2_CONFIG


def main() -> None:
    # Use the already-extracted frames if present, but copy a small subset to a
    # temp dir so SAM2 only has to process e.g. 60 frames (1 second).
    src_frames = config.WORK_DIR / "frames-b1f9630f3e51cd91-540"
    if not src_frames.exists():
        raise SystemExit(f"No frames at {src_frames}; run the main pipeline at least once.")

    test_dir = config.WORK_DIR / "sam2-smoke-frames"
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True)

    # Take frames 60..120 — same range the real run starts from.
    n_test = 60
    seed_in_test = 0  # frame 60 maps to index 0 in the test slice
    src_seed = 60
    for i in range(n_test):
        src = src_frames / f"{src_seed + i:05d}.jpg"
        dst = test_dir / f"{i:05d}.jpg"
        if not src.exists():
            raise SystemExit(f"Missing {src}")
        shutil.copy2(src, dst)
    print(f"Staged {n_test} frames in {test_dir}")

    import torch
    from sam2.build_sam import build_sam2_video_predictor

    # Try MPS with the patch; fall back to CPU on any error.
    if torch.backends.mps.is_available():
        device = torch.device("mps")
        tr._patch_sam2_for_mps()
        print("Device: mps (patched)")
    else:
        device = torch.device("cpu")
        print("Device: cpu")
    print(f"Checkpoint: {SAM2_CHECKPOINT}")
    print(f"Config: {SAM2_CONFIG}")

    predictor = build_sam2_video_predictor(SAM2_CONFIG, str(SAM2_CHECKPOINT), device=device)
    state = predictor.init_state(
        video_path=str(test_dir),
        offload_video_to_cpu=True,
        offload_state_to_cpu=True,
        async_loading_frames=True,
    )

    # Use the bbox the user clicked earlier — at 540p coords.
    # The original click was bbox=(1370, 502, 132, 184) at 1920x1080.
    # 540p scale: 540/1080 = 0.5
    box_540 = np.array([1370 * 0.5, 502 * 0.5, (1370 + 132) * 0.5, (502 + 184) * 0.5], dtype=np.float32)
    print("Adding bbox (540p coords):", box_540.tolist())

    predictor.add_new_points_or_box(
        inference_state=state,
        frame_idx=seed_in_test,
        obj_id=1,
        box=box_540,
    )

    n_with_mask = 0
    import time
    t0 = time.time()
    for frame_idx, obj_ids, mask_logits in predictor.propagate_in_video(state):
        m = (mask_logits[0] > 0).cpu().numpy()
        if m.any():
            n_with_mask += 1
    elapsed = time.time() - t0
    print(f"Forward propagate done: {n_with_mask}/{n_test} frames had a mask")
    print(f"Time: {elapsed:.1f}s for {n_test} frames = {elapsed/n_test*1000:.0f}ms/frame")
    print(f"Estimated full-video (12486 frames): {elapsed/n_test*12486/60:.1f} min")
    print("OK")


if __name__ == "__main__":
    main()
