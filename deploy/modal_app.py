"""Brawlhalla TikTok auto-editor — Modal cloud deployment.

Runs the same pipeline as the local `./run.sh`, but in a Modal container with
~8 CPU / 16 GB RAM, and exposes a small web UI for uploading clips.

Deploy:
    pip install modal
    modal token new           # one-time login
    modal deploy deploy/modal_app.py

After deploy you'll get a URL like:
    https://<your-username>--brawlhalla-tiktok-editor-web.modal.run

Open it, drag-and-drop a video + (optional) music, watch the progress, download.
"""

import json
import shutil
import sys
import time
import traceback
import uuid
from pathlib import Path

import modal

APP_NAME = "brawlhalla-tiktok-editor"

# ---------------------------------------------------------------------------
# Container image: Python 3.11 + Node 20 + ffmpeg + Chrome headless
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "ffmpeg",
        "curl",
        "ca-certificates",
        "git",
        # Chromium runtime deps (Remotion bundles its own headless shell, but
        # we still need the system libs).
        "libnss3", "libatk1.0-0", "libatk-bridge2.0-0", "libcups2",
        "libdbus-1-3", "libdrm2", "libxkbcommon0", "libxcomposite1",
        "libxdamage1", "libxfixes3", "libxrandr2", "libgbm1", "libpango-1.0-0",
        "libcairo2", "libasound2",
    )
    # Node 20.
    .run_commands(
        "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
        "apt-get install -y nodejs",
    )
    # Python deps used by the pipeline.
    .pip_install_from_requirements(str(PROJECT_ROOT / "deploy" / "requirements.txt"))
    .pip_install("fastapi[standard]", "python-multipart")
    # Bring the pipeline source code into the image. We use copy=True so that
    # subsequent build steps (npm install) can see these files; this trades a
    # bit of cold-start build time for the ability to install Remotion deps.
    .add_local_dir(str(PROJECT_ROOT / "pipeline"), "/app/pipeline", copy=True)
    .add_local_dir(str(PROJECT_ROOT / "remotion"), "/app/remotion",
                   ignore=["node_modules", ".cache", "dist"], copy=True)
    .add_local_dir(str(PROJECT_ROOT / "deploy" / "frontend"), "/app/frontend", copy=True)
    # Install Remotion deps + pre-fetch the headless Chrome so first render is fast.
    .workdir("/app/remotion")
    .run_commands(
        "npm install --no-audit --no-fund",
        # Pre-download Chrome headless so cold-starts don't eat the first job.
        "npx remotion browser ensure",
    )
    .workdir("/app")
)

# ---------------------------------------------------------------------------
# Persistent storage: model cache + job files + status dict
# ---------------------------------------------------------------------------

model_cache = modal.Volume.from_name("editor-models", create_if_missing=True)
jobs_volume = modal.Volume.from_name("editor-jobs", create_if_missing=True)
status_dict = modal.Dict.from_name("editor-status", create_if_missing=True)

# ---------------------------------------------------------------------------
# Modal app
# ---------------------------------------------------------------------------

app = modal.App(APP_NAME)


def _set_status(job_id: str, **fields) -> None:
    """Merge-update the status dict for a job. Safe to call frequently."""
    cur = status_dict.get(job_id) or {}
    cur.update(fields)
    cur["updated_at"] = time.time()
    status_dict[job_id] = cur


# Each job gets a function call. We cap at 1 hour and ask for 16 CPUs +
# decent memory. Remotion's H.264 render is heavily CPU-bound so this is
# where speed comes from.
@app.function(
    image=image,
    cpu=16.0,
    memory=32768,
    timeout=7200,
    max_containers=1,           # serialize jobs: avoid two heavy renders sharing CPU
    volumes={
        "/cache": model_cache,
        "/jobs": jobs_volume,
    },
)
def process_job(job_id: str) -> str:
    """Run the full pipeline for a job that's already been uploaded under
    /jobs/{job_id}/input. Returns the relative path to the output mp4."""
    sys.path.insert(0, "/app")
    import os

    # Point HuggingFace + Whisper caches at the volume so models persist.
    os.environ["HF_HOME"] = "/cache/huggingface"
    os.environ["XDG_CACHE_HOME"] = "/cache"

    from pipeline import config
    from pipeline.modules import audiomix, camera, io_utils, transcribe
    from pipeline.modules import action_tracker

    job_dir = Path("/jobs") / job_id
    in_dir = job_dir / "input"
    out_dir = job_dir / "output"
    work_dir = job_dir / "work"
    out_dir.mkdir(exist_ok=True, parents=True)
    work_dir.mkdir(exist_ok=True, parents=True)

    # Override pipeline paths to point at this job's dirs.
    config.INPUT_DIR = in_dir
    config.OUTPUT_DIR = out_dir
    config.WORK_DIR = work_dir
    # Remotion lives at /app/remotion in the image.
    config.REMOTION_DIR = Path("/app/remotion")

    try:
        _set_status(job_id, stage="discover", progress=2,
                    message="Lecture des fichiers…")
        inputs = io_utils.discover_inputs(in_dir)
        notes = inputs.notes or {}
        meta = io_utils.video_meta(inputs.video)
        _set_status(job_id, stage="discover", progress=5,
                    message=f"Source: {meta['width']}x{meta['height']} @ {meta['fps']:.0f}fps "
                            f"({meta['duration']:.0f}s)",
                    duration=meta["duration"], width=meta["width"], height=meta["height"])

        # 1. Transcribe.
        _set_status(job_id, stage="transcribe", progress=10,
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
        _set_status(job_id, progress=40, message=f"{len(transcript['words'])} mots transcrits")

        # 2. Track / camera plan.
        framing = (notes.get("framing") or config.FRAMING_MODE or "wide").lower()
        if framing == "wide":
            _set_status(job_id, stage="camera", progress=45,
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
            _set_status(job_id, stage="track", progress=42,
                        message="Mode TIGHT — analyse du mouvement…")
            track_data = action_tracker.track(inputs.video, meta, work_dir)
            _set_status(job_id, stage="camera", progress=55,
                        message="Calcul du plan caméra…")

        cam = camera.plan(track_data, inputs.video, work_dir,
                          out_w=config.OUT_W, out_h=config.OUT_H,
                          highlights_sec=[io_utils.parse_timestamp(h) for h in (notes.get("highlights") or [])])

        # 3. Audio mix.
        _set_status(job_id, stage="audio", progress=60, message="Mix audio (voix + musique duckée)…")
        music_db = float(notes.get("music_db") or config.MUSIC_DB_DEFAULT)
        mixed_audio = audiomix.mix(inputs.video, inputs.music, work_dir, music_db=music_db)

        # 4. Stage assets in remotion/public/.
        public_dir = config.REMOTION_DIR / "public"
        public_dir.mkdir(exist_ok=True, parents=True)
        video_pub = public_dir / f"{job_id}-source.mp4"
        audio_pub = public_dir / f"{job_id}-audio.m4a"
        shutil.copy2(inputs.video, video_pub)
        shutil.copy2(mixed_audio, audio_pub)

        render_input = {
            "videoSrc": video_pub.name,
            "audioSrc": audio_pub.name,
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
            "wideZoom": float(notes.get("wide_zoom") or config.WIDE_ZOOM),
            "hud": {
                "left": config.HUD_LEFT_PORTRAIT,
                "right": config.HUD_RIGHT_PORTRAIT,
                "outSize": config.HUD_OUT_SIZE,
                "outMargin": config.HUD_OUT_MARGIN,
                "swapCorners": config.HUD_SWAP_CORNERS,
                "show": notes.get("show_hud", True),
            },
        }
        render_input_path = work_dir / "render-input.json"
        io_utils.write_json(render_input_path, render_input)

        # 5. Remotion render.
        _set_status(job_id, stage="render", progress=65,
                    message="Rendu Remotion (peut prendre 5-15 min)…")
        out_path = out_dir / "output.mp4"
        import subprocess
        cmd = [
            "npx", "--no-install", "remotion", "render",
            "src/index.ts", "MainComp", str(out_path),
            "--props", str(render_input_path),
            "--codec", "h264",
            "--audio-codec", "aac",
            "--audio-bitrate", "192k",
            # 16 CPU container → use 12 concurrent renderers + leave room for
            # the muxer / encoder / OS. Remotion scales near-linearly up to
            # the CPU count.
            "--concurrency", "12",
            "--enforce-audio-track",
            # Force progress to standard form regardless of TTY detection.
            "--log=info",
        ]
        proc = subprocess.Popen(
            cmd, cwd=str(config.REMOTION_DIR),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0,  # raw read; we'll do our own line splitting
        )

        # Remotion progress bars use \r (carriage return) instead of \n, so
        # we read in chunks and split on either CR or LF. We also write each
        # complete "line" to the container stdout so it shows in `modal app
        # logs`, AND we extract progress via regex.
        import re
        # Both "Rendering frames" and "Rendered X/Y" formats are emitted
        # depending on Remotion version / log mode. We match either.
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
            # Split on either CR or LF; keep the trailing partial.
            *parts, buf = re.split(rb"[\r\n]", buf)
            for raw_line in parts:
                if not raw_line.strip():
                    continue
                # Echo a stripped version to container stdout for log visibility.
                line = raw_line.decode("utf-8", errors="replace").strip()
                # Progress detection.
                m = rendering_re.search(raw_line)
                if m:
                    cur, total = int(m.group(1)), int(m.group(2))
                    pct = 65 + int(20 * cur / total)
                    if pct > last_pct or time.time() - last_emit > 5:
                        _set_status(job_id, progress=pct,
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
                        _set_status(job_id, progress=pct,
                                    message=f"Encoding: {cur}/{total} frames")
                        last_pct = pct
                        last_emit = time.time()
                        print(f"[encode] {cur}/{total} ({pct}%)", flush=True)
                    continue
                # Print other lines (errors, infos) to the log.
                if line:
                    print(f"[remotion] {line}", flush=True)

        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"Remotion render failed (exit {rc})")

        # 6. Cleanup intermediate public files (keep things tidy).
        try:
            video_pub.unlink()
            audio_pub.unlink()
        except OSError:
            pass

        _set_status(job_id, stage="done", progress=100,
                    message="Terminé !", output_path=str(out_path))
        jobs_volume.commit()
        return str(out_path.relative_to(Path("/jobs")))

    except Exception as e:
        tb = traceback.format_exc()
        _set_status(job_id, stage="error", message=f"Erreur: {e}\n\n{tb}")
        jobs_volume.commit()
        raise


# ---------------------------------------------------------------------------
# Web app: upload / status / download / static frontend
# ---------------------------------------------------------------------------

@app.function(
    image=image,
    cpu=1.0,
    memory=2048,
    timeout=3600,
    volumes={"/jobs": jobs_volume},
)
@modal.asgi_app()
def web():
    from fastapi import FastAPI, UploadFile, File, Form, HTTPException
    from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    api = FastAPI(title="Brawlhalla TikTok Editor")

    FRONTEND_DIR = Path("/app/frontend")
    JOBS_ROOT = Path("/jobs")

    @api.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    api.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @api.post("/api/upload")
    async def upload(
        video: UploadFile = File(...),
        music: UploadFile = File(None),
        notes: str = Form(""),
        framing: str = Form("wide"),
        title: str = Form(""),
    ):
        """Receive files, stage them in the volume, kick off the pipeline."""
        job_id = uuid.uuid4().hex[:12]
        job_dir = JOBS_ROOT / job_id
        in_dir = job_dir / "input"
        in_dir.mkdir(parents=True, exist_ok=True)

        # Write video.
        video_path = in_dir / video.filename
        with video_path.open("wb") as f:
            while chunk := await video.read(1 << 20):
                f.write(chunk)
        video_size = video_path.stat().st_size

        # Optional music.
        music_size = 0
        if music and music.filename:
            music_path = in_dir / music.filename
            with music_path.open("wb") as f:
                while chunk := await music.read(1 << 20):
                    f.write(chunk)
            music_size = music_path.stat().st_size

        # notes.md
        frontmatter = (
            "---\n"
            f"framing: {framing}\n"
            f'title: "{title}"\n'
            "---\n\n"
            f"{notes}\n"
        )
        (in_dir / "notes.md").write_text(frontmatter, encoding="utf-8")
        jobs_volume.commit()

        # Kick off the pipeline (returns immediately with a function call id).
        _set_status(job_id, stage="queued", progress=0,
                    message="En file d'attente…")
        call = process_job.spawn(job_id)
        _set_status(job_id, call_id=call.object_id)

        return {
            "job_id": job_id,
            "video_size": video_size,
            "music_size": music_size,
        }

    @api.get("/api/status/{job_id}")
    async def status(job_id: str):
        st = status_dict.get(job_id)
        if st is None:
            raise HTTPException(404, "unknown job")
        return JSONResponse(st)

    @api.get("/api/download/{job_id}")
    async def download(job_id: str):
        out = JOBS_ROOT / job_id / "output" / "output.mp4"
        if not out.exists():
            raise HTTPException(404, "output not ready")
        return FileResponse(
            str(out),
            media_type="video/mp4",
            filename=f"brawlhalla-{job_id}.mp4",
        )

    return api
