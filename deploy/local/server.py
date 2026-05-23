"""Local server — same UI as the cloud version, runs the pipeline on this
machine. Detects CUDA automatically (NVIDIA GPU = much faster Whisper).

Launch via:
    python deploy/local/server.py

…or, more user-friendly, via the double-clickable launchers in this folder:
    macOS: start.command
    Windows: start.bat
    Linux: start.sh
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from pathlib import Path

# Pipeline lives one level up.
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from deploy.local import updater  # noqa: E402


# In-process job registry.
JOBS: dict[str, dict] = {}
# Per-job seed bbox set by the web picker (only used in tight framing).
JOB_SEEDS: dict[str, dict] = {}
# threading.Event signalling the pipeline that the seed is ready.
JOB_SEED_EVENTS: dict[str, threading.Event] = {}
LOCAL_JOBS_DIR = ROOT / "_local_jobs"
LOCAL_JOBS_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = ROOT / "deploy" / "frontend"

PORT = 8765


def _set_status(job_id: str, **fields) -> None:
    cur = JOBS.get(job_id) or {}
    cur.update(fields)
    cur["updated_at"] = time.time()
    JOBS[job_id] = cur


def run_pipeline(job_id: str) -> None:
    """Same shape as the Modal worker, but synchronous and using local paths."""
    # Re-import inside the thread (avoids issues on Windows with fork-vs-spawn).
    from pipeline import config
    from pipeline.modules import audiomix, camera, io_utils, transcribe
    from pipeline.modules import action_tracker

    job_dir = LOCAL_JOBS_DIR / job_id
    in_dir = job_dir / "input"
    out_dir = job_dir / "output"
    work_dir = job_dir / "work"
    out_dir.mkdir(exist_ok=True, parents=True)
    work_dir.mkdir(exist_ok=True, parents=True)

    # Point pipeline at this job's dirs.
    config.INPUT_DIR = in_dir
    config.OUTPUT_DIR = out_dir
    config.WORK_DIR = work_dir
    config.REMOTION_DIR = ROOT / "remotion"

    # Track per-stage timing so the UI can show what took how long.
    started_at = time.time()
    stage_times: dict[str, float] = {}
    _last_stage_start = [started_at]
    _current_stage = [""]

    def _mark_stage(stage: str) -> None:
        """Record duration of the previous stage and start a new one."""
        now = time.time()
        prev = _current_stage[0]
        if prev and prev not in {"queued", "discover"}:
            stage_times[prev] = stage_times.get(prev, 0.0) + (now - _last_stage_start[0])
        _current_stage[0] = stage
        _last_stage_start[0] = now

    # Wrap _set_status so any stage transition records timing automatically.
    def _stage(job_id: str, **fields) -> None:
        new_stage = fields.get("stage")
        if new_stage and new_stage != _current_stage[0]:
            _mark_stage(new_stage)
        # Always include cumulative timing snapshot for the frontend.
        fields.setdefault("started_at", started_at)
        fields["elapsed"] = time.time() - started_at
        fields["stage_times"] = dict(stage_times)
        _set_status(job_id, **fields)

    try:
        _stage(job_id, stage="discover", progress=2,
                    message="Lecture des fichiers…")
        inputs = io_utils.discover_inputs(in_dir)
        notes = inputs.notes or {}
        meta = io_utils.video_meta(inputs.video)
        _stage(job_id, stage="discover", progress=5,
                    message=f"Source: {meta['width']}x{meta['height']} @ {meta['fps']:.0f}fps "
                            f"({meta['duration']:.0f}s)",
                    duration=meta["duration"], width=meta["width"], height=meta["height"])

        _stage(job_id, stage="transcribe", progress=10,
                    message="Transcription audio (Whisper large-v3)…")
        extra_terms = notes.get("extra_terms") or []
        if isinstance(extra_terms, str):
            extra_terms = [t.strip() for t in extra_terms.split(",") if t.strip()]
        transcript = transcribe.transcribe(inputs.video, work_dir, extra_terms=extra_terms)
        sub_groups = transcribe.group_words(
            transcript["words"],
            group_size=config.SUB_GROUP_SIZE,
            max_gap=config.SUB_GROUP_MAX_GAP,
        )
        _stage(job_id, progress=40, message=f"{len(transcript['words'])} mots transcrits")

        framing = (notes.get("framing") or config.FRAMING_MODE or "wide").lower()
        if framing == "wide":
            _stage(job_id, stage="camera", progress=45,
                        message="Mode WIDE — calcul du plan caméra")
            n = meta["n_frames"]
            cx, cy = meta["width"] / 2, meta["height"] / 2
            track_data = {
                "fps": meta["fps"], "width": meta["width"], "height": meta["height"],
                "seed_frame": 0,
                "track": [{"frame": i, "x": cx, "y": cy, "w": 100, "h": 100, "ok": True}
                          for i in range(n)],
            }
        else:
            # Tight mode: SAM2 follows Kaya specifically, seeded from a bbox
            # the user draws on the seed-frame picker in the web UI. We pause
            # the pipeline here until the user submits a seed (or 5 minutes
            # elapse without one — then we auto-seed from motion).
            seed_t = 1.0  # seconds
            seed_frame_idx = max(0, int(round(seed_t * meta["fps"])))
            _stage(job_id, stage="await_seed", progress=42,
                        message="Trace un rectangle autour de Kaya pour démarrer le tracking…",
                        await_seed=True,
                        seed_frame_idx=seed_frame_idx,
                        src_w=meta["width"], src_h=meta["height"])

            event = JOB_SEED_EVENTS.setdefault(job_id, threading.Event())
            got_seed = event.wait(timeout=300)  # 5 minutes max
            user_seed = JOB_SEEDS.get(job_id)
            _set_status(job_id, await_seed=False)

            from pipeline.modules import hardware as _hw_mod
            _hw_track = _hw_mod.detect()
            _stage(job_id, stage="track", progress=43,
                        message=f"Tracking SAM2 ({_hw_track.sam2_device.upper()})…")
            try:
                from pipeline.modules import tracker as sam2_tracker
                if got_seed and user_seed:
                    sf = int(user_seed.get("frame", seed_frame_idx))
                    bx = int(user_seed["x"])
                    by = int(user_seed["y"])
                    bw = int(user_seed["w"])
                    bh = int(user_seed["h"])
                    track_data = sam2_tracker.track(
                        inputs.video, meta, work_dir,
                        seed=(sf, (bx, by, bw, bh)),
                    )
                else:
                    # User never submitted a seed → fall back to auto-seed.
                    track_data = sam2_tracker.track(
                        inputs.video, meta, work_dir, auto_seed=True,
                    )
            except Exception as e:
                print(f"[track] SAM2 failed ({e}); falling back to action_tracker.",
                      file=sys.stderr, flush=True)
                _stage(job_id, stage="track", progress=43,
                            message=f"SAM2 KO ({type(e).__name__}) — fallback action_tracker…")
                track_data = action_tracker.track(inputs.video, meta, work_dir)
            _stage(job_id, stage="camera", progress=55,
                        message="Calcul du plan caméra…")

        cam = camera.plan(track_data, inputs.video, work_dir,
                          out_w=config.OUT_W, out_h=config.OUT_H,
                          highlights_sec=[io_utils.parse_timestamp(h)
                                          for h in (notes.get("highlights") or [])])

        _stage(job_id, stage="audio", progress=60,
                    message="Mix audio (voix + musique duckée)…")
        music_db = float(notes.get("music_db") or config.MUSIC_DB_DEFAULT)
        mixed_audio = audiomix.mix(inputs.video, inputs.music, work_dir, music_db=music_db)

        # GPU composite: bake bg + fg + HUD into a single 1080x1920 video using
        # ffmpeg with NVDEC/NVENC. This used to be done by Remotion in headless
        # Chromium, which decoded the source 4 times per output frame on CPU.
        # Now Remotion only has to overlay subtitles + title.
        from pipeline.modules import gpu_composite
        hud_cfg = {
            "left": config.HUD_LEFT_PORTRAIT,
            "right": config.HUD_RIGHT_PORTRAIT,
            "outSize": config.HUD_OUT_SIZE,
            "outMargin": config.HUD_OUT_MARGIN,
            "swapCorners": config.HUD_SWAP_CORNERS,
            "show": notes.get("show_hud", True),
        }
        wide_zoom_val = float(notes.get("wide_zoom") or config.WIDE_ZOOM)
        from pipeline.modules import hardware as _hw
        _hw_now = _hw.detect()
        composite_label = (
            "Compositing visuel (NVENC)" if _hw_now.encoder_kind == "nvenc"
            else "Compositing visuel (VideoToolbox)" if _hw_now.encoder_kind == "videotoolbox"
            else "Compositing visuel (Quick Sync)" if _hw_now.encoder_kind == "qsv"
            else "Compositing visuel (AMF)" if _hw_now.encoder_kind == "amf"
            else "Compositing visuel (CPU)"
        )
        _stage(job_id, stage="composite", progress=62,
                    message=composite_label + "…")
        base_path = gpu_composite.composite(
            inputs.video, cam, work_dir,
            framing=framing,
            wide_zoom=wide_zoom_val,
            hud=hud_cfg,
            out_w=config.OUT_W,
            out_h=config.OUT_H,
            out_fps=config.OUT_FPS,
        )

        # Stage assets in a per-job public dir to avoid the 1+ GB copy bloat
        # we'd otherwise get from sharing the global remotion/public/. Remotion
        # accepts `--public-dir` to point at any folder.
        job_public = work_dir / "remotion-public"
        if job_public.exists():
            shutil.rmtree(job_public, ignore_errors=True)
        job_public.mkdir(parents=True, exist_ok=True)

        audio_pub = job_public / f"{job_id}-audio.m4a"
        base_pub = job_public / f"{job_id}-base.mp4"
        shutil.copy2(mixed_audio, audio_pub)
        shutil.copy2(base_path, base_pub)

        render_input = {
            "videoSrc": "",  # No longer used by Remotion when baseVideo is set.
            "audioSrc": audio_pub.name,
            "baseVideo": base_pub.name,
            "fps": cam["fps"],
            "outFps": config.OUT_FPS,
            "outWidth": config.OUT_W,
            "outHeight": config.OUT_H,
            "duration": meta["duration"],
            "totalFrames": len(cam["frames"]),
            "camera": cam,
            "subtitles": sub_groups,
            "title": notes.get("title", "") or "",
            "style": (notes.get("style") or "hype").lower(),
            "framing": framing,
            "wideZoom": wide_zoom_val,
            "hud": {**hud_cfg, "show": False},  # already baked
        }
        render_input_path = work_dir / "render-input.json"
        io_utils.write_json(render_input_path, render_input)

        _stage(job_id, stage="render", progress=65,
                    message="Rendu Remotion…")
        out_path = out_dir / "output.mp4"
        import subprocess, re

        # Pick concurrency based on CPU count (rough heuristic).
        try:
            import os
            cpu_count = os.cpu_count() or 4
            concurrency = max(2, min(16, cpu_count - 2))
        except Exception:
            concurrency = 4

        # Use shell=False with explicit npx; on Windows we may need shell=True
        # for npx batch resolution.
        is_windows = sys.platform.startswith("win")
        cmd = [
            "npx", "--no-install", "remotion", "render",
            "src/index.ts", "MainComp", str(out_path),
            "--props", str(render_input_path),
            # h264 (libx264) is actually faster than NVENC for our case,
            # because the bottleneck is Chromium's frame rasterization, not
            # the H.264 encoder. NVENC would just sit idle waiting for frames.
            "--codec", "h264",
            # Per-frame screenshots: JPEG is ~3-4x faster to encode than PNG
            # and the quality loss is invisible after H.264 reencode.
            "--image-format", "jpeg",
            "--jpeg-quality", "92",
            # Force Chromium to use DirectX (ANGLE) for layer compositing.
            # On Windows this means the 5090's hardware path is used by
            # Chromium itself for the GPU compositor.
            "--gl", "angle",
            # Per-job public dir to avoid the global remotion/public/ bloat.
            "--public-dir", str(job_public),
            "--audio-codec", "aac",
            "--audio-bitrate", "192k",
            "--concurrency", str(concurrency),
            "--enforce-audio-track",
            "--log=info",
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.REMOTION_DIR),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0,
            shell=is_windows,
        )

        rendering_re = re.compile(rb"Render(?:ing|ed)?\s*(?:frames\s+)?.*?(\d+)\s*/\s*(\d+)")
        encoding_re = re.compile(rb"Encod(?:ing|ed)?\s*(?:video\s+)?.*?(\d+)\s*/\s*(\d+)")
        last_pct = 65
        last_emit = time.time()
        buf = b""
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            buf += chunk
            *parts, buf = re.split(rb"[\r\n]", buf)
            for raw_line in parts:
                if not raw_line.strip():
                    continue
                line = raw_line.decode("utf-8", errors="replace").strip()
                m = rendering_re.search(raw_line)
                if m:
                    cur, total = int(m.group(1)), int(m.group(2))
                    pct = 65 + int(20 * cur / total)
                    if pct > last_pct or time.time() - last_emit > 5:
                        _stage(job_id, progress=pct,
                                    message=f"Rendu: {cur}/{total} frames")
                        last_pct = pct
                        last_emit = time.time()
                        print(f"[render] {cur}/{total} ({pct}%)", flush=True)
                    continue
                m2 = encoding_re.search(raw_line)
                if m2:
                    cur, total = int(m2.group(1)), int(m2.group(2))
                    pct = 85 + int(13 * cur / total)
                    if pct > last_pct or time.time() - last_emit > 5:
                        _stage(job_id, progress=pct,
                                    message=f"Encoding: {cur}/{total} frames")
                        last_pct = pct
                        last_emit = time.time()
                        print(f"[encode] {cur}/{total} ({pct}%)", flush=True)
                    continue
                if line:
                    print(f"[remotion] {line}", flush=True)

        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"Remotion render failed (exit {rc})")

        # Tidy: drop the per-job public dir so it doesn't accumulate.
        try:
            shutil.rmtree(job_public, ignore_errors=True)
        except OSError:
            pass

        # Finalize timing for the last stage (render).
        _mark_stage("done")
        total_elapsed = time.time() - started_at
        _set_status(job_id, stage="done", progress=100,
                    message="Terminé !",
                    output_path=str(out_path),
                    started_at=started_at,
                    elapsed=total_elapsed,
                    total_elapsed=total_elapsed,
                    stage_times=dict(stage_times))

    except Exception as e:
        tb = traceback.format_exc()
        _mark_stage("error")
        total_elapsed = time.time() - started_at
        _set_status(job_id, stage="error",
                    message=f"Erreur: {e}\n\n{tb}",
                    started_at=started_at,
                    elapsed=total_elapsed,
                    total_elapsed=total_elapsed,
                    stage_times=dict(stage_times))
        print(tb, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Brawlhalla TikTok Editor (local)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.post("/api/upload")
async def upload(
    video: UploadFile = File(...),
    music: UploadFile = File(None),
    notes: str = Form(""),
    framing: str = Form("wide"),
    title: str = Form(""),
):
    job_id = uuid.uuid4().hex[:12]
    job_dir = LOCAL_JOBS_DIR / job_id
    in_dir = job_dir / "input"
    in_dir.mkdir(parents=True, exist_ok=True)

    video_path = in_dir / video.filename
    with video_path.open("wb") as f:
        while chunk := await video.read(1 << 20):
            f.write(chunk)

    if music and music.filename:
        music_path = in_dir / music.filename
        with music_path.open("wb") as f:
            while chunk := await music.read(1 << 20):
                f.write(chunk)

    frontmatter = (
        "---\n"
        f"framing: {framing}\n"
        f'title: "{title}"\n'
        "---\n\n"
        f"{notes}\n"
    )
    (in_dir / "notes.md").write_text(frontmatter, encoding="utf-8")

    _set_status(job_id, stage="queued", progress=0, message="En file d'attente…")
    threading.Thread(target=run_pipeline, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def status(job_id: str):
    st = JOBS.get(job_id)
    if st is None:
        raise HTTPException(404, "unknown job")
    return JSONResponse(st)


# ---------------------------------------------------------------------------
# Seed-frame picker (tight mode only).
#
# The pipeline pauses at stage="await_seed" and exposes:
#   GET  /api/seed_frame/<job_id>?t=1.0       — JPEG of the source at t seconds
#   POST /api/seed/<job_id>  body={frame,x,y,w,h}  — user-submitted seed bbox
# ---------------------------------------------------------------------------

@app.get("/api/seed_frame/{job_id}")
async def seed_frame(job_id: str, t: float = 1.0):
    job_dir = LOCAL_JOBS_DIR / job_id
    in_dir = job_dir / "input"
    if not in_dir.exists():
        raise HTTPException(404, "unknown job")
    # Locate the uploaded video.
    videos = [p for p in in_dir.iterdir()
              if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}]
    if not videos:
        raise HTTPException(404, "no video in job")
    src = videos[0]

    # Extract a JPEG via ffmpeg. We re-extract on every call (it's instant
    # for any reasonable t) so the user can scrub freely.
    out_path = job_dir / "work" / f"seed-{int(round(t * 1000))}.jpg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        import subprocess
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, t):.3f}",
            "-i", str(src),
            "-frames:v", "1",
            "-q:v", "3",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise HTTPException(500, f"ffmpeg failed: {proc.stderr[:300]}")
    return FileResponse(str(out_path), media_type="image/jpeg")


@app.post("/api/seed/{job_id}")
async def submit_seed(job_id: str, body: dict):
    """User-submitted seed bbox in source-pixel coords. Schema:
    {frame: int, x: int, y: int, w: int, h: int}
    """
    if job_id not in JOBS:
        raise HTTPException(404, "unknown job")
    try:
        seed = {
            "frame": int(body.get("frame", 0)),
            "x": int(body["x"]),
            "y": int(body["y"]),
            "w": int(body["w"]),
            "h": int(body["h"]),
        }
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, f"bad seed payload: {e}")
    if seed["w"] <= 1 or seed["h"] <= 1:
        raise HTTPException(400, "bbox too small")
    JOB_SEEDS[job_id] = seed
    JOB_SEED_EVENTS.setdefault(job_id, threading.Event()).set()
    return {"ok": True, "seed": seed}


# ---------------------------------------------------------------------------
# System monitoring (CPU / RAM / GPU / VRAM) — polled by the frontend widget.
# Only the server process tree is reported, not the whole machine.
# ---------------------------------------------------------------------------

# Cache last-known GPU info between calls (NVML init is cheap but we'd rather
# not pay it every 1.5s). None means "tried and failed", absent means "never
# tried".
_NVML_HANDLE: dict = {}


def _nvml_handle():
    """Return a (pynvml, handle) tuple or (None, None) if no NVIDIA GPU."""
    if "h" in _NVML_HANDLE:
        return _NVML_HANDLE["pynvml"], _NVML_HANDLE["h"]
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        _NVML_HANDLE["pynvml"] = pynvml
        _NVML_HANDLE["h"] = h
        return pynvml, h
    except Exception:
        # No NVIDIA driver, or pynvml not installed. Try the torch fallback
        # next call instead of forever skipping.
        _NVML_HANDLE["pynvml"] = None
        _NVML_HANDLE["h"] = None
        return None, None


def _app_pids() -> set[int]:
    """Set of PIDs belonging to this server process and all its descendants
    (ffmpeg jobs, npm, node, Chromium, etc.)."""
    import psutil, os
    try:
        me = psutil.Process(os.getpid())
        return {me.pid, *(c.pid for c in me.children(recursive=True))}
    except Exception:
        return set()


# Cache of psutil.Process instances by PID. psutil's per-process cpu_percent()
# is computed as the delta between successive calls **on the same instance**,
# so we MUST keep the instance alive across /api/sysinfo polls — otherwise
# every call sees a fresh instance and returns 0.0.
_PROC_CACHE: dict[int, "object"] = {}


def _get_proc(pid: int):
    import psutil
    p = _PROC_CACHE.get(pid)
    if p is None:
        try:
            p = psutil.Process(pid)
            # Prime the per-process CPU sampling. The first call on a fresh
            # instance always returns 0.0; subsequent calls return the
            # percent of CPU time used since this prime call.
            p.cpu_percent(interval=None)
            _PROC_CACHE[pid] = p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
    return p


def _app_cpu_ram() -> dict:
    """Sum CPU% and RSS across the entire server process tree."""
    import psutil
    cpu_count = psutil.cpu_count(logical=True) or 1
    total_ram = psutil.virtual_memory().total

    pids = _app_pids()

    # Drop cached instances whose PID is gone.
    for stale_pid in list(_PROC_CACHE):
        if stale_pid not in pids:
            _PROC_CACHE.pop(stale_pid, None)

    cpu_acc = 0.0
    rss_acc = 0
    n_procs = 0
    for pid in pids:
        p = _get_proc(pid)
        if p is None:
            continue
        try:
            cpu_acc += p.cpu_percent(interval=None)
            rss_acc += p.memory_info().rss
            n_procs += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            _PROC_CACHE.pop(pid, None)
            continue
    cpu_pct = cpu_acc / cpu_count
    return {
        "cpu_pct": min(100.0, max(0.0, cpu_pct)),
        "cpu_count": cpu_count,
        "ram_used": int(rss_acc),
        "ram_total": int(total_ram),
        "ram_pct": (rss_acc / total_ram * 100.0) if total_ram else 0.0,
        "n_procs": n_procs,
    }


def _gpu_app_stats(pids: set[int]) -> dict | None:
    """Per-process GPU stats. Returns the slice of GPU util / VRAM that
    belongs to our process tree, plus the total VRAM size for context."""
    pynvml, h = _nvml_handle()
    if pynvml is None or h is None:
        # Fallback: torch reports our process VRAM (only ours), no util.
        try:
            import torch  # type: ignore
            if torch.cuda.is_available():
                used = torch.cuda.memory_reserved(0)
                free, total = torch.cuda.mem_get_info(0)
                return {
                    "name": torch.cuda.get_device_name(0),
                    "util_pct": None,
                    "mem_used": int(used),
                    "mem_total": int(total),
                    "mem_pct": (used / total * 100.0) if total else 0.0,
                    "temp_c": None,
                    "source": "torch",
                }
        except Exception:
            pass
        return None

    try:
        name = pynvml.nvmlDeviceGetName(h)
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="replace")
        try:
            temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            temp = None
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)

        # Per-process VRAM. Try v3 (current driver) then fall back to legacy.
        def _list_procs(getter_names: list[str]) -> list:
            for fn_name in getter_names:
                fn = getattr(pynvml, fn_name, None)
                if fn is None:
                    continue
                try:
                    return list(fn(h))
                except Exception:
                    continue
            return []

        compute_procs = _list_procs([
            "nvmlDeviceGetComputeRunningProcesses_v3",
            "nvmlDeviceGetComputeRunningProcesses_v2",
            "nvmlDeviceGetComputeRunningProcesses",
        ])
        graphics_procs = _list_procs([
            "nvmlDeviceGetGraphicsRunningProcesses_v3",
            "nvmlDeviceGetGraphicsRunningProcesses_v2",
            "nvmlDeviceGetGraphicsRunningProcesses",
        ])

        seen_pids: set[int] = set()
        vram_app = 0
        for p in compute_procs + graphics_procs:
            pid = getattr(p, "pid", None)
            if pid is None or pid in seen_pids:
                continue
            if pid in pids:
                used = getattr(p, "usedGpuMemory", 0) or 0
                vram_app += int(used)
                seen_pids.add(pid)

        # Per-process SM utilization (best-effort: Blackwell + recent driver).
        util_pct: float | None = None
        try:
            samples = pynvml.nvmlDeviceGetProcessUtilization(h, 0)
            acc = 0.0
            for s in samples:
                if s.pid in pids:
                    # smUtil is "% of SMs that were busy" for that process
                    # over the sample window. Sum across our tree's pids.
                    acc += float(s.smUtil)
            util_pct = min(100.0, acc)
        except Exception:
            # On consumer cards or older drivers this call may fail. Leave
            # util_pct as None; the UI will display "n/a".
            util_pct = None

        return {
            "name": name,
            "util_pct": util_pct,
            "mem_used": int(vram_app),
            "mem_total": int(mem.total),
            "mem_pct": (vram_app / mem.total * 100.0) if mem.total else 0.0,
            "temp_c": temp,
            "source": "nvml",
        }
    except Exception:
        return None


@app.get("/api/sysinfo")
async def sysinfo():
    pids = _app_pids()
    base = _app_cpu_ram()
    base["gpu"] = _gpu_app_stats(pids)
    return JSONResponse(base)


@app.get("/api/hardware")
async def hardware_info():
    """Public-facing hardware capability summary. Exposes what the pipeline
    detected so the frontend can show "Encoded with NVENC / VideoToolbox /
    libx264" on the done card and the user knows what to expect."""
    try:
        from pipeline.modules import hardware as hw_mod
        hw = hw_mod.detect()
        return JSONResponse({
            "os": hw.os_name,
            "cuda": hw.has_cuda,
            "cuda_name": hw.cuda_name,
            "mps": hw.has_mps,
            "encoder": hw.encoder_name,
            "encoder_kind": hw.encoder_kind,
            "decoder_hwaccel": hw.decoder_hwaccel[1] if hw.decoder_hwaccel else None,
            "whisper_device": hw.whisper_device,
            "whisper_compute_type": hw.whisper_compute_type,
            "sam2_device": hw.sam2_device,
            "summary": hw.summary,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/download/{job_id}")
async def download(job_id: str):
    out = LOCAL_JOBS_DIR / job_id / "output" / "output.mp4"
    if not out.exists():
        raise HTTPException(404, "output not ready")
    return FileResponse(
        str(out),
        media_type="video/mp4",
        filename=f"brawlhalla-{job_id}.mp4",
    )


# ---------------------------------------------------------------------------
# Self-update endpoints
# ---------------------------------------------------------------------------

# When updater.apply() finishes successfully we ask the parent process to
# restart us. Because uvicorn doesn't have a clean "restart" primitive, we
# write a "restart needed" flag and have the launcher (start.sh / start.bat /
# start.command) re-run the server when it sees that flag on shutdown.
RESTART_FLAG = ROOT / ".restart-requested"


def _restart_after_delay(delay_s: float = 1.5) -> None:
    """Set the restart flag, then exit the process with code 75 (EX_TEMPFAIL)
    which the launcher script interprets as 'please re-run me'."""
    def _kill():
        time.sleep(delay_s)
        try:
            RESTART_FLAG.write_text("1", encoding="utf-8")
        except OSError:
            pass
        # Exit 75 is the convention; launcher scripts loop on it.
        import os as _os
        _os._exit(75)
    threading.Thread(target=_kill, daemon=True).start()


@app.get("/api/version")
async def version():
    """Compare local install version against the latest GitHub release."""
    try:
        info = updater.check(ROOT)
        return JSONResponse({
            "current": info.current,
            "latest": info.latest,
            "update_available": info.update_available,
            "notes": info.notes,
            "published_at": info.published_at,
        })
    except Exception as e:
        # Don't fail loud — the version check is non-essential.
        return JSONResponse({
            "current": updater._read_local_version(ROOT),
            "latest": None,
            "update_available": False,
            "error": str(e),
        }, status_code=200)


@app.post("/api/update")
async def trigger_update():
    """Kick off the update in the background. Poll /api/update/status for progress."""
    cur = updater.state()
    if cur.get("stage") in ("downloading", "extracting", "deps"):
        raise HTTPException(409, "update already in progress")
    updater.apply_async(ROOT)
    return {"ok": True}


@app.get("/api/update/status")
async def update_status():
    st = updater.state()
    if st.get("stage") == "done" and st.get("progress") == 100 and not RESTART_FLAG.exists():
        # Schedule restart once we've reported done to the client.
        _restart_after_delay()
    return JSONResponse(st)



def _open_browser_when_ready() -> None:
    """Wait briefly for uvicorn to bind, then open the URL."""
    import socket
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.socket() as s:
                s.settimeout(0.2)
                s.connect(("127.0.0.1", PORT))
            break
        except OSError:
            time.sleep(0.2)
    webbrowser.open(f"http://127.0.0.1:{PORT}")


def main() -> None:
    # One-shot hardware detection on startup so the user sees what the
    # pipeline will actually use.
    try:
        from pipeline.modules import hardware
        hw = hardware.detect()
        hw_line = hw.summary
    except Exception as e:
        hw_line = f"detection failed ({e})"
    print()
    print("  ╔═══════════════════════════════════════════════════╗")
    print("  ║   Brawlhalla TikTok Editor — local server        ║")
    print(f"  ║   → http://127.0.0.1:{PORT}                          ║")
    print("  ║   Ctrl+C to stop                                  ║")
    print("  ╚═══════════════════════════════════════════════════╝")
    print(f"  Hardware: {hw_line}")
    print()
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
