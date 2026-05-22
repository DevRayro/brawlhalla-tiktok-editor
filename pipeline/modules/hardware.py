"""Cross-platform hardware capability detection.

This module is the single source of truth for "what can this machine do".
The rest of the pipeline asks it for the right ffmpeg encoder, the right
torch device for Whisper / SAM2, the right hwaccel decoder, etc., instead
of hardcoding NVIDIA-specific paths.

Detection runs once on first call and is cached. It's cheap (a single
`ffmpeg -encoders` invocation plus a few torch imports) but doing it on
every request would add ~150ms of latency.

Supported targets, in priority order:
  1. NVIDIA GPU       → CUDA   + h264_nvenc + NVDEC
  2. Apple Silicon    → MPS    + h264_videotoolbox + videotoolbox
  3. Intel Quick Sync → CPU    + h264_qsv + qsv
  4. AMD AMF          → CPU    + h264_amf + d3d11va (Win) / vaapi (Linux)
  5. CPU only         → CPU    + libx264 + no hwaccel

faster-whisper / ctranslate2 don't support MPS yet, so on Apple Silicon
Whisper falls back to CPU int8 even though SAM2 / ffmpeg can use the GPU.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Hardware:
    os_name: str  # "windows" | "macos" | "linux"

    # Compute backends.
    has_cuda: bool = False
    has_mps: bool = False  # Apple Silicon
    cuda_name: str = ""

    # ffmpeg encoders detected via `ffmpeg -encoders`.
    has_nvenc: bool = False
    has_qsv: bool = False
    has_amf: bool = False
    has_videotoolbox: bool = False

    # ffmpeg hwaccel (decoder) backends.
    decoder_hwaccel: list[str] = field(default_factory=list)

    # Resolved video encoder.
    encoder_name: str = "libx264"  # ffmpeg -c:v <name>
    encoder_kind: str = "cpu"      # "cpu" | "nvenc" | "qsv" | "amf" | "videotoolbox"

    # Whisper / faster-whisper.
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"

    # SAM2 tuning.
    sam2_device: str = "cpu"      # "cuda" | "mps" | "cpu"
    sam2_image_size: int = 384
    sam2_stride: int = 8
    sam2_chunk_size: int = 200
    sam2_offload: bool = True

    # Friendly summary line for logs.
    summary: str = ""


_CACHED: Optional[Hardware] = None


def _ffmpeg_encoders_blob() -> str:
    """Return `ffmpeg -encoders` stdout, or empty string if ffmpeg missing."""
    if not shutil.which("ffmpeg"):
        return ""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        return (proc.stdout or "") + "\n" + (proc.stderr or "")
    except Exception:
        return ""


def _ffmpeg_hwaccels_blob() -> str:
    """Return `ffmpeg -hwaccels` stdout for decoder detection."""
    if not shutil.which("ffmpeg"):
        return ""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True, text=True, timeout=5,
        )
        return (proc.stdout or "") + "\n" + (proc.stderr or "")
    except Exception:
        return ""


def _detect_torch() -> tuple[bool, bool, str]:
    """Return (has_cuda, has_mps, cuda_name). Doesn't fail if torch isn't installed."""
    try:
        import torch  # type: ignore
        has_cuda = bool(torch.cuda.is_available())
        cuda_name = torch.cuda.get_device_name(0) if has_cuda else ""
        has_mps = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        return has_cuda, has_mps, cuda_name
    except Exception:
        return False, False, ""


def detect(force: bool = False) -> Hardware:
    """Detect hardware once and cache. Pass force=True to rerun."""
    global _CACHED
    if _CACHED is not None and not force:
        return _CACHED

    hw = Hardware(os_name=_os_name())
    encoders = _ffmpeg_encoders_blob()
    hwaccels = _ffmpeg_hwaccels_blob().lower()

    hw.has_nvenc = "h264_nvenc" in encoders
    hw.has_qsv = "h264_qsv" in encoders
    hw.has_amf = "h264_amf" in encoders
    hw.has_videotoolbox = "h264_videotoolbox" in encoders

    hw.has_cuda, hw.has_mps, hw.cuda_name = _detect_torch()

    # Pick the best video encoder available. Order matches the priority list
    # in the module docstring: NVENC > VideoToolbox > QSV > AMF > libx264.
    if hw.has_nvenc:
        hw.encoder_name = "h264_nvenc"
        hw.encoder_kind = "nvenc"
    elif hw.has_videotoolbox and hw.os_name == "macos":
        hw.encoder_name = "h264_videotoolbox"
        hw.encoder_kind = "videotoolbox"
    elif hw.has_qsv:
        hw.encoder_name = "h264_qsv"
        hw.encoder_kind = "qsv"
    elif hw.has_amf:
        hw.encoder_name = "h264_amf"
        hw.encoder_kind = "amf"
    else:
        hw.encoder_name = "libx264"
        hw.encoder_kind = "cpu"

    # Decoder hwaccel: prefer the same family as the encoder if available, but
    # fall back gracefully. We only emit the args; it's safe to pass them and
    # let ffmpeg fail back to software if the system doesn't actually support
    # them at runtime (-hwaccel is best-effort by design).
    if hw.encoder_kind == "nvenc" and "cuda" in hwaccels:
        hw.decoder_hwaccel = ["-hwaccel", "cuda"]
    elif hw.encoder_kind == "videotoolbox" and "videotoolbox" in hwaccels:
        hw.decoder_hwaccel = ["-hwaccel", "videotoolbox"]
    elif hw.encoder_kind == "qsv" and "qsv" in hwaccels:
        hw.decoder_hwaccel = ["-hwaccel", "qsv"]
    elif hw.encoder_kind == "amf":
        # AMD doesn't expose its own hwaccel name; use platform-typical decoder.
        if hw.os_name == "windows" and "d3d11va" in hwaccels:
            hw.decoder_hwaccel = ["-hwaccel", "d3d11va"]
        elif hw.os_name == "linux" and "vaapi" in hwaccels:
            hw.decoder_hwaccel = ["-hwaccel", "vaapi"]
    # else: no hwaccel, software decode.

    # Whisper. faster-whisper / ctranslate2 doesn't support MPS, so Apple
    # Silicon falls back to CPU int8 — still fast on M-series CPUs.
    if hw.has_cuda:
        hw.whisper_device = "cuda"
        hw.whisper_compute_type = "float16"
    else:
        hw.whisper_device = "cpu"
        hw.whisper_compute_type = "int8"

    # SAM2 tuning. CUDA gets the big-budget settings, MPS gets mid-tier, CPU
    # gets the conservative fallback.
    if hw.has_cuda:
        hw.sam2_device = "cuda"
        hw.sam2_image_size = 1024
        hw.sam2_stride = 4
        hw.sam2_chunk_size = 800
        hw.sam2_offload = False
    elif hw.has_mps:
        hw.sam2_device = "mps"
        hw.sam2_image_size = 512
        hw.sam2_stride = 6
        hw.sam2_chunk_size = 400
        hw.sam2_offload = True
    else:
        hw.sam2_device = "cpu"
        hw.sam2_image_size = 384
        hw.sam2_stride = 8
        hw.sam2_chunk_size = 200
        hw.sam2_offload = True

    # Build a one-line human summary.
    bits: list[str] = []
    if hw.has_cuda:
        bits.append(f"CUDA[{hw.cuda_name}]")
    elif hw.has_mps:
        bits.append("MPS[Apple Silicon]")
    else:
        bits.append("CPU only")
    bits.append(f"enc={hw.encoder_name}")
    if hw.decoder_hwaccel:
        bits.append(f"dec={hw.decoder_hwaccel[1]}")
    bits.append(f"whisper={hw.whisper_device}/{hw.whisper_compute_type}")
    bits.append(f"sam2={hw.sam2_device}")
    hw.summary = " · ".join(bits)

    _CACHED = hw
    return hw


def _os_name() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s.startswith("win"):
        return "windows"
    return "linux"


def encoder_args(hw: Optional[Hardware] = None, cq: int = 20) -> list[str]:
    """Return the ffmpeg `-c:v <enc> -preset ...` argv slice for the chosen
    encoder. `cq` is the constant-quality target (lower = higher quality);
    each encoder maps it to its own scale.
    """
    if hw is None:
        hw = detect()
    enc = hw.encoder_kind
    if enc == "nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p5",
            "-tune", "hq",
            "-rc", "vbr",
            "-cq", str(cq),
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]
    if enc == "videotoolbox":
        return [
            "-c:v", "h264_videotoolbox",
            "-q:v", str(cq + 30),  # videotoolbox uses 1-100 (higher=better)
            "-pix_fmt", "yuv420p",
        ]
    if enc == "qsv":
        return [
            "-c:v", "h264_qsv",
            "-preset", "medium",
            "-global_quality", str(cq),
            "-look_ahead", "1",
            "-pix_fmt", "nv12",
        ]
    if enc == "amf":
        return [
            "-c:v", "h264_amf",
            "-quality", "balanced",
            "-rc", "cqp",
            "-qp_i", str(cq),
            "-qp_p", str(cq + 2),
            "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(cq),
        "-pix_fmt", "yuv420p",
    ]


def hwaccel_args(hw: Optional[Hardware] = None) -> list[str]:
    """ffmpeg -hwaccel argv slice (may be empty)."""
    if hw is None:
        hw = detect()
    return list(hw.decoder_hwaccel)
