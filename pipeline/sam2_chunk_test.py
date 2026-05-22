"""Validate the chunked SAM2 implementation on a fraction of the video.

Re-uses the bbox the user clicked previously (hardcoded from the run logs)
so we don't need a UI.
"""
from __future__ import annotations

import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.modules import io_utils, tracker as tr


def main() -> None:
    inputs = io_utils.discover_inputs(config.INPUT_DIR)
    meta = io_utils.video_meta(inputs.video)

    # Fake a small total to limit how many chunks run.
    fake_n_frames = 50 * 60  # 50 seconds @ 60fps = 3000 source frames = ~375 sampled @ stride=8
    fake_meta = {**meta, "n_frames": fake_n_frames}

    # Bbox from the previous run logs.
    bbox = (1370, 502, 132, 184)
    seed_frame = 60

    print(f"Testing chunked SAM2 on {fake_n_frames} source frames "
          f"(stride={tr.SAM2_STRIDE}, chunk_size={tr.SAM2_CHUNK_SIZE})")
    print(f"Seed bbox at frame {seed_frame}: {bbox}")
    print()

    t0 = time.time()
    pts = tr._sam2_track(
        inputs.video, seed_frame, bbox,
        meta["width"], meta["height"], fake_n_frames, config.WORK_DIR,
    )
    dt = time.time() - t0

    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    n_ok = sum(1 for p in pts if p.ok)
    print()
    print(f"Done: {n_ok}/{len(pts)} frames sampled OK ({100*n_ok/len(pts):.1f}%)")
    print(f"Wall time: {dt:.1f}s ({dt/fake_n_frames*1000:.1f}ms/source-frame)")
    print(f"Peak RSS: {rss_mb:.0f} MB")


if __name__ == "__main__":
    main()
