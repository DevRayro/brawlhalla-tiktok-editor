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


# In-process job registry.
JOBS: dict[str, dict] = {}
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
                          highlights_sec=[io_utils.parse_timestamp(h)
                                          for h in (notes.get("highlights") or [])])

        _set_status(job_id, stage="audio", progress=60,
                    message="Mix audio (voix + musique duckée)…")
        music_db = float(notes.get("music_db") or config.MUSIC_DB_DEFAULT)
        mixed_audio = audiomix.mix(inputs.video, inputs.music, work_dir, music_db=music_db)

        # Stage assets in remotion/public/.
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

        _set_status(job_id, stage="render", progress=65,
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
            "--codec", "h264",
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
                if line:
                    print(f"[remotion] {line}", flush=True)

        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"Remotion render failed (exit {rc})")

        try:
            video_pub.unlink()
            audio_pub.unlink()
        except OSError:
            pass

        _set_status(job_id, stage="done", progress=100,
                    message="Terminé !", output_path=str(out_path))

    except Exception as e:
        tb = traceback.format_exc()
        _set_status(job_id, stage="error", message=f"Erreur: {e}\n\n{tb}")
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
    print()
    print("  ╔═══════════════════════════════════════════════════╗")
    print("  ║   Brawlhalla TikTok Editor — local server        ║")
    print(f"  ║   → http://127.0.0.1:{PORT}                          ║")
    print("  ║   Ctrl+C to stop                                  ║")
    print("  ╚═══════════════════════════════════════════════════╝")
    print()
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
