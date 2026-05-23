"""GPU-accelerated visual base composite.

Builds a 1080x1920 video that contains everything except subtitles and title:
  - blurred / darkened background (full source, scaled to fill 9:16)
  - cropped foreground band (source center, optionally zoomed)
  - two HUD portrait crops in the corners
  - dynamic per-frame crop in TIGHT mode

Encoded with NVENC. Decoding goes through NVDEC when available.

Replacing this work in Remotion's headless Chromium gives a ~5-10x speedup
because Chromium has to decode the source video 4 times per output frame
(background, foreground, HUD-left, HUD-right). ffmpeg decodes once and uses
filter splits.

Result: a `.mp4` written to `cache_dir/base-<hash>.mp4`. The pipeline then
hands this to Remotion as a single `baseVideo` prop; Remotion only renders
subtitles + title on top. This keeps the Remotion stylability while getting
rid of the per-frame video raster work.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import io_utils
from . import hardware
from .. import config


def _hwaccel_args() -> list[str]:
    """Decoder hwaccel — picked from hardware detection (CUDA, VideoToolbox,
    QSV, AMF, or none)."""
    return hardware.hwaccel_args()


def _video_codec_args() -> list[str]:
    """Pick the best available H.264 encoder for this machine. Falls back to
    libx264 on CPU-only systems."""
    return hardware.encoder_args(cq=20)


def _build_filter_wide(
    src_w: int, src_h: int, out_w: int, out_h: int,
    wide_zoom: float, hud: dict[str, Any] | None,
) -> tuple[str, str]:
    """Static filter graph for WIDE framing.

    Geometry is constant for the whole video (no camera tracking), so we can
    express it as a single filter_complex string with no sendcmd needed.
    """
    has_hud = bool(hud and hud.get("show", True))
    n_splits = 4 if has_hud else 2
    parts: list[str] = []

    if has_hud:
        parts.append("[0:v]split=4[bg_in][fg_in][hud_a_in][hud_b_in]")
    else:
        parts.append("[0:v]split=2[bg_in][fg_in]")

    # ----- Background (blurred fill) -----
    # Scale source to fully cover 1080x1920, then center-crop, blur, darken.
    parts.append(
        f"[bg_in]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h},gblur=sigma=60,eq=brightness=-0.45:saturation=1.1[bg]"
    )

    # ----- Foreground band (center crop scaled to fill width) -----
    z = max(1.0, wide_zoom)
    parts.append(
        f"[fg_in]crop=iw/{z}:ih:iw*(1-1/{z})/2:0,scale={out_w}:-2[fg]"
    )

    if not has_hud:
        # Composite bg + fg directly into [v].
        parts.append("[bg][fg]overlay=0:(H-h)/2[v]")
        return ";".join(parts), "v"

    # With HUD: bg + fg first, then two HUD crops.
    parts.append("[bg][fg]overlay=0:(H-h)/2[c1]")
    last = "c1"
    left = hud["left"]
    right = hud["right"]
    out_size = int(hud.get("outSize", 280))
    margin = int(hud.get("outMargin", 36))
    swap = bool(hud.get("swapCorners", True))
    lx = int(left["x"] * src_w); ly = int(left["y"] * src_h)
    lw = int(left["w"] * src_w); lh = int(left["h"] * src_h)
    rx = int(right["x"] * src_w); ry = int(right["y"] * src_h)
    rw = int(right["w"] * src_w); rh = int(right["h"] * src_h)
    parts.append(f"[hud_a_in]crop={lw}:{lh}:{lx}:{ly},scale={out_size}:{out_size}[hud_a]")
    parts.append(f"[hud_b_in]crop={rw}:{rh}:{rx}:{ry},scale={out_size}:{out_size}[hud_b]")
    left_corner_x = (out_w - out_size - margin) if swap else margin
    right_corner_x = margin if swap else (out_w - out_size - margin)
    parts.append(f"[{last}][hud_a]overlay={left_corner_x}:{margin}[c2]")
    parts.append(f"[c2][hud_b]overlay={right_corner_x}:{margin}[v]")
    return ";".join(parts), "v"


def _build_filter_tight(
    src_w: int, src_h: int, out_w: int, out_h: int,
    base_crop_w: float, hud: dict[str, Any] | None,
) -> tuple[str, str]:
    """Static filter graph for TIGHT framing.

    The dynamic per-frame crop (cx, cy, zoom) is driven by a `sendcmd` file
    written separately. The crop filter uses `eval=frame` so its parameters
    can be updated each frame.
    """
    has_hud = bool(hud and hud.get("show", True))
    parts: list[str] = []

    # Initial crop placeholder values; sendcmd overwrites them every frame.
    init_w = int(base_crop_w)
    init_h = int(base_crop_w * out_h / out_w)
    # Note: we don't use `eval=frame` on crop. sendcmd sets the named filter
    # options at runtime via the AVFilter's process_command callback, which
    # crop supports for x, y, w, h directly.
    crop_chain = (
        f"sendcmd=f={{cmdfile}},"
        f"crop@cam=w={init_w}:h={init_h}:x=0:y=0,"
        f"scale={out_w}:{out_h}"
    )

    if not has_hud:
        # Single visual layer — straight chain, no split needed.
        parts.append(f"[0:v]{crop_chain}[v]")
        return ";".join(parts), "v"

    # With HUD: split source into 3 streams (fg + two HUD crops).
    parts.append("[0:v]split=3[fg_in][hud_a_in][hud_b_in]")
    parts.append(f"[fg_in]{crop_chain}[fg]")
    last = "fg"

    left = hud["left"]
    right = hud["right"]
    out_size = int(hud.get("outSize", 280))
    margin = int(hud.get("outMargin", 36))
    swap = bool(hud.get("swapCorners", True))
    lx = int(left["x"] * src_w); ly = int(left["y"] * src_h)
    lw = int(left["w"] * src_w); lh = int(left["h"] * src_h)
    rx = int(right["x"] * src_w); ry = int(right["y"] * src_h)
    rw = int(right["w"] * src_w); rh = int(right["h"] * src_h)
    parts.append(f"[hud_a_in]crop={lw}:{lh}:{lx}:{ly},scale={out_size}:{out_size}[hud_a]")
    parts.append(f"[hud_b_in]crop={rw}:{rh}:{rx}:{ry},scale={out_size}:{out_size}[hud_b]")
    left_corner_x = (out_w - out_size - margin) if swap else margin
    right_corner_x = margin if swap else (out_w - out_size - margin)
    parts.append(f"[{last}][hud_a]overlay={left_corner_x}:{margin}[c2]")
    parts.append(f"[c2][hud_b]overlay={right_corner_x}:{margin}[v]")
    return ";".join(parts), "v"


def _write_sendcmd(camera_plan: dict[str, Any], out_path: Path) -> None:
    """Write a sendcmd file driving the `crop@cam` filter per source frame.

    Format (one block per frame):
        <t_start>-<t_end> crop@cam x <x>, crop@cam y <y>, crop@cam w <w>, crop@cam h <h>;
    """
    fps = float(camera_plan["fps"])
    src_w = float(camera_plan["width"])
    src_h = float(camera_plan["height"])
    base_w = float(camera_plan["base_crop_w"])
    base_h = float(camera_plan["base_crop_h"])
    frames = camera_plan["frames"]

    lines: list[str] = []
    for i, f in enumerate(frames):
        t0 = i / fps
        t1 = (i + 1) / fps
        z = max(1e-6, float(f["zoom"]))
        cw = base_w / z
        ch = base_h / z
        cx = float(f["cx"])
        cy = float(f["cy"])
        # Top-left of the crop window, clamped to source bounds.
        x = max(0.0, min(src_w - cw, cx - cw / 2))
        y = max(0.0, min(src_h - ch, cy - ch / 2))
        lines.append(
            f"{t0:.6f}-{t1:.6f} crop@cam x {x:.3f}, "
            f"crop@cam y {y:.3f}, crop@cam w {cw:.3f}, crop@cam h {ch:.3f};"
        )
    out_path.write_text("\n".join(lines), encoding="utf-8")


def composite(
    video: Path,
    camera_plan: dict[str, Any],
    work_dir: Path,
    framing: str = "wide",
    wide_zoom: float = config.WIDE_ZOOM,
    hud: dict[str, Any] | None = None,
    out_w: int = config.OUT_W,
    out_h: int = config.OUT_H,
    out_fps: int = config.OUT_FPS,
) -> Path:
    """Build the visual base composite and return its path.

    Cached on the source video hash + a small hash of the relevant params.
    """
    src_w = int(camera_plan["width"])
    src_h = int(camera_plan["height"])

    # Cache key: video bytes + framing-relevant params.
    extra = f"{framing}|wz={wide_zoom:.3f}|out={out_w}x{out_h}@{out_fps}"
    if hud:
        extra += f"|hud={int(hud.get('show', True))}"
        extra += f"|swap={int(hud.get('swapCorners', True))}"
    key = io_utils.cache_key(video) + "-" + io_utils.cache_key_str(extra)
    out_path = work_dir / f"base-{key}.mp4"
    if out_path.exists():
        print(f"[gpu_composite] Cache hit: {out_path.name}")
        return out_path

    if framing == "tight":
        cmdfile = work_dir / f"crop-cmd-{key}.txt"
        _write_sendcmd(camera_plan, cmdfile)
        flt_template, last = _build_filter_tight(
            src_w, src_h, out_w, out_h,
            base_crop_w=float(camera_plan["base_crop_w"]),
            hud=hud,
        )
        # ffmpeg's filter graph parser treats `:` and `,` as separators inside
        # filter arguments. The cleanest way to dodge Windows drive-letter
        # colons (and any other path quirks) is to run ffmpeg with cwd set to
        # the work_dir and pass a relative filename.
        flt = flt_template.replace("{cmdfile}", cmdfile.name)
        cwd = str(work_dir)
    else:
        flt, last = _build_filter_wide(src_w, src_h, out_w, out_h, wide_zoom, hud)
        cwd = None

    cmd: list[str] = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-stats",
        *_hwaccel_args(),
        "-i", str(video),
        "-filter_complex", flt,
        "-map", f"[{last}]",
        "-r", str(out_fps),
        "-an",
        *_video_codec_args(),
        "-movflags", "+faststart",
        str(out_path),
    ]

    print(f"[gpu_composite] {framing.upper()} mode → {out_path.name}")
    print(f"[gpu_composite] $ ffmpeg ... -filter_complex \"{flt[:120]}{'...' if len(flt) > 120 else ''}\"")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        # Detect hardware-related failures (driver missing, nvcuda.dll, AMF
        # surface mismatch, QSV surface alloc, etc.) and retry once with the
        # software libx264 + no decode hwaccel. This is the safety net for
        # users whose ffmpeg binary lists hardware encoders that aren't
        # actually usable on their machine (typical with the standard Windows
        # ffmpeg builds on AMD/Intel hardware).
        err = (proc.stderr or "").lower()
        hw_markers = (
            "nvcuda", "cannot load cuda", "could not dynamically load cuda",
            "no device available", "device creation failed",
            "h264_amf", "amf encoder", "qsv encoder", "videotoolbox encoder",
            "operation not permitted",
        )
        if any(m in err for m in hw_markers):
            print(f"[gpu_composite] Hardware path failed; retrying with CPU encoder")
            print(f"  reason: {proc.stderr.strip().splitlines()[0] if proc.stderr.strip() else 'unknown'}")
            sw_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
                "-i", str(video),                       # no -hwaccel
                "-filter_complex", flt,
                "-map", f"[{last}]",
                "-r", str(out_fps),
                "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(out_path),
            ]
            proc = subprocess.run(sw_cmd, capture_output=True, text=True, cwd=cwd)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"gpu_composite (software fallback) failed (exit {proc.returncode}):\n"
                    f"  cmd: {' '.join(sw_cmd)}\n"
                    f"  stderr:\n{proc.stderr}"
                )
            print(f"[gpu_composite] CPU fallback succeeded → {out_path.name}")
            return out_path
        # Non-hardware error: surface as before.
        raise RuntimeError(
            f"gpu_composite failed (exit {proc.returncode}):\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stderr:\n{proc.stderr}"
        )
    print(f"[gpu_composite] Done → {out_path.name}")
    return out_path
