"""GPU contention guard.

When several pipeline jobs run in parallel (EDITOR_MAX_CONCURRENT > 1), the
GPU-heavy stages — Whisper transcription and SAM2 tracking — must NOT overlap,
or they fight over VRAM and either OOM or thrash. CPU-bound stages (audio mix,
Remotion rasterization) are free to overlap.

This module exposes a process-wide semaphore that those heavy stages acquire
via `gpu_section()`. With the default of 1 permit, only one job touches the
GPU at a time; the others wait at the section boundary. Tune with the
EDITOR_GPU_SLOTS environment variable (rarely needed).
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

try:
    _SLOTS = max(1, int(os.environ.get("EDITOR_GPU_SLOTS", "1")))
except ValueError:
    _SLOTS = 1

_GPU_SEMAPHORE = threading.BoundedSemaphore(_SLOTS)


@contextmanager
def gpu_section(label: str = ""):
    """Serialize GPU-heavy work. Blocks until a GPU slot is free."""
    _GPU_SEMAPHORE.acquire()
    try:
        yield
    finally:
        _GPU_SEMAPHORE.release()
