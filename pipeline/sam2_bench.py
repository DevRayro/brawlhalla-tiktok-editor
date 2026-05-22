"""Benchmark SAM2 in different configs on a real-size slice.

Runs the small variant on CPU at varying image_size to find a viable config.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shutil
import numpy as np
import torch

from pipeline import config


def _stage(src_dir: Path, dst_dir: Path, n: int, src_offset: int = 60) -> None:
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    dst_dir.mkdir(parents=True)
    for i in range(n):
        src = src_dir / f"{src_offset + i:05d}.jpg"
        if not src.exists():
            raise SystemExit(f"Missing {src}")
        shutil.copy2(src, dst_dir / f"{i:05d}.jpg")


def _bench(image_size: int | None, n_frames: int = 30) -> float:
    """Run SAM2 on n_frames test frames; return seconds per frame."""
    from sam2.build_sam import build_sam2_video_predictor

    src_frames = config.WORK_DIR / "frames-b1f9630f3e51cd91-540"
    test_dir = config.WORK_DIR / "sam2-bench-frames"
    _stage(src_frames, test_dir, n_frames)

    overrides: list[str] = []
    if image_size is not None:
        overrides = [f"++model.image_size={image_size}"]

    device = torch.device("cpu")
    predictor = build_sam2_video_predictor(
        "configs/sam2.1/sam2.1_hiera_s.yaml",
        str(config.ROOT / "models" / "sam2.1_hiera_small.pt"),
        device=device,
        hydra_overrides_extra=overrides,
    )
    state = predictor.init_state(
        video_path=str(test_dir),
        offload_video_to_cpu=True,
        offload_state_to_cpu=True,
        async_loading_frames=False,
    )

    box = np.array([685.0, 251.0, 751.0, 343.0], dtype=np.float32)
    predictor.add_new_points_or_box(state, frame_idx=0, obj_id=1, box=box)

    t0 = time.time()
    for _ in predictor.propagate_in_video(state):
        pass
    return (time.time() - t0) / n_frames


def main() -> None:
    total = 12486
    print(f"Bench SAM2 on CPU (small variant, 540p source frames):\n")
    results: list[tuple[int | None, float]] = []
    for img_size in (1024, 512, 384, 256):
        try:
            sec = _bench(img_size)
            results.append((img_size, sec))
            print(f"  image_size={img_size}: {sec*1000:.0f} ms/frame")
        except Exception as e:
            print(f"  image_size={img_size}: FAILED ({type(e).__name__}: {e})")

    print("\nProjected full-video runtimes:")
    for img_size, sec in results:
        print(f"  image_size={img_size}:")
        for stride in (1, 4, 8, 16):
            n = total // stride
            secs = n * sec
            print(f"    stride={stride:2d}  → {n:5d} frames → {secs/60:5.1f} min")


if __name__ == "__main__":
    main()
