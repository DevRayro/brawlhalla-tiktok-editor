"""Orchestrator for the Brawlhalla TikTok auto-editor.

Steps:
  1. Discover inputs (video, music, notes.md)
  2. Probe video metadata
  3. Transcribe (word-level)
  4. Group words into karaoke subtitle groups
  5. Track Kaya (interactive seed click + CSRT)
  6. Plan camera (smoothed pos + dynamic zoom)
  7. Mix audio (voice + ducked music)
  8. Hand off everything to Remotion via a single render-input.json
  9. Render with Remotion
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Make this runnable as `python pipeline/run.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import config
from pipeline.modules import audiomix, camera, io_utils, tracker, transcribe
from pipeline.modules import action_tracker


def main() -> None:
    parser = argparse.ArgumentParser(description="Brawlhalla TikTok auto-editor")
    parser.add_argument("--retrack", action="store_true", help="Force re-running the tracker (re-asks for the seed click).")
    parser.add_argument("--no-render", action="store_true", help="Skip the final Remotion render (compose only).")
    parser.add_argument("--skip-tracking", action="store_true", help="Use a static centered camera (no Kaya tracking).")
    args = parser.parse_args()

    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Inputs
    inputs = io_utils.discover_inputs(config.INPUT_DIR)
    print(f"[main] Video: {inputs.video.name}")
    notes = inputs.notes or {}
    style = (notes.get("style") or "hype").lower()
    music_db = float(notes.get("music_db") or config.MUSIC_DB_DEFAULT)
    highlights_raw = notes.get("highlights") or []
    highlights_sec = [io_utils.parse_timestamp(h) for h in highlights_raw]
    if highlights_sec:
        print(f"[main] Highlights @ {highlights_sec}")

    # 2. Probe
    meta = io_utils.video_meta(inputs.video)
    print(f"[main] Source: {meta['width']}x{meta['height']} @ {meta['fps']:.2f}fps "
          f"({meta['duration']:.1f}s, {meta['n_frames']} frames)")

    # 3. Transcribe
    extra_terms = notes.get("extra_terms") or []
    if isinstance(extra_terms, str):
        extra_terms = [t.strip() for t in extra_terms.split(",") if t.strip()]
    transcript = transcribe.transcribe(inputs.video, config.WORK_DIR, extra_terms=extra_terms)

    # 4. Subtitle grouping
    sub_groups = transcribe.group_words(
        transcript["words"],
        group_size=config.SUB_GROUP_SIZE,
        max_gap=config.SUB_GROUP_MAX_GAP,
    )
    print(f"[main] {len(sub_groups)} subtitle groups")

    # 5. Track Kaya (only in tight framing mode).
    framing = (notes.get("framing") or config.FRAMING_MODE or "wide").lower()
    tight_backend = (notes.get("tight_tracker") or config.TIGHT_TRACKER or "action").lower()
    if framing == "wide" or args.skip_tracking:
        if framing == "wide":
            print("[main] Framing: WIDE (letterbox).")
        else:
            print("[main] Skipping tracking; using static centered camera.")
        n = meta["n_frames"]
        cx, cy = meta["width"] / 2, meta["height"] / 2
        track_data = {
            "fps": meta["fps"],
            "width": meta["width"],
            "height": meta["height"],
            "seed_frame": 0,
            "track": [{"frame": i, "x": cx, "y": cy, "w": 100, "h": 100, "ok": True} for i in range(n)],
        }
    else:
        print(f"[main] Framing: TIGHT (tracker backend: {tight_backend}).")
        if tight_backend == "sam2":
            track_data = tracker.track(inputs.video, meta, config.WORK_DIR, force=args.retrack)
        elif tight_backend == "action":
            track_data = action_tracker.track(inputs.video, meta, config.WORK_DIR, force=args.retrack)
        else:
            raise SystemExit(f"Unknown tight_tracker: {tight_backend}")

    # 6. Camera plan
    cam = camera.plan(
        track_data, inputs.video, config.WORK_DIR,
        out_w=config.OUT_W, out_h=config.OUT_H,
        highlights_sec=highlights_sec,
    )

    # 7. Audio mix
    mixed_audio = audiomix.mix(inputs.video, inputs.music, config.WORK_DIR, music_db=music_db)

    # 8. Stage assets in remotion/public/ (Remotion requires staticFile() for local assets).
    public_dir = config.REMOTION_DIR / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    video_pub = public_dir / "source.mp4"
    audio_pub = public_dir / "audio.m4a"
    _link_or_copy(inputs.video, video_pub)
    _link_or_copy(mixed_audio, audio_pub)

    # 9. Hand off to Remotion via a single input file.
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
        "style": style,
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
    render_input_path = config.WORK_DIR / "render-input.json"
    io_utils.write_json(render_input_path, render_input)
    print(f"[main] Render input → {render_input_path}")

    if args.no_render:
        print("[main] --no-render set, stopping before Remotion.")
        return

    # 10. Render with Remotion
    out_name = inputs.video.stem + "-tiktok.mp4"
    out_path = config.OUTPUT_DIR / out_name

    if not (config.REMOTION_DIR / "node_modules").exists():
        print("[main] Remotion deps not installed. Run ./setup.sh first.", file=sys.stderr)
        sys.exit(1)

    io_utils.run([
        "npx", "--no-install", "remotion", "render",
        "src/index.ts", "MainComp", str(out_path),
        "--props", str(render_input_path),
        "--codec", "h264",
        "--audio-codec", "aac",
        "--audio-bitrate", "192k",
        "--concurrency", "1",
        "--enforce-audio-track",
    ], cwd=str(config.REMOTION_DIR))

    print()
    print(f"[main] DONE → {out_path}")


def _link_or_copy(src: Path, dst: Path) -> None:
    """Copy src to dst (overwriting existing) only if needed.

    We copy rather than symlink because Remotion's bundler forwards symlinks
    to a temp dir and the resolved target ends up outside the bundle root,
    which causes 404s when Chrome fetches the asset.
    """
    if dst.exists() or dst.is_symlink():
        # If it's already a real file matching the source size, skip the copy.
        try:
            if (
                dst.is_file()
                and not dst.is_symlink()
                and dst.stat().st_size == src.stat().st_size
            ):
                return
        except OSError:
            pass
        dst.unlink()
    shutil.copy2(src, dst)


if __name__ == "__main__":
    main()
