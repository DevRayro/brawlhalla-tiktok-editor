"""I/O helpers: locate input files, hash them for caching, run ffmpeg."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


@dataclass
class Inputs:
    video: Path
    music: Path | None
    notes: dict[str, Any]
    notes_text: str


def _file_hash(p: Path, chunk_size: int = 1 << 20) -> str:
    """SHA-1 of the first + last MB and the size — fast and good enough for cache keying."""
    size = p.stat().st_size
    h = hashlib.sha1()
    h.update(str(size).encode())
    with p.open("rb") as f:
        h.update(f.read(chunk_size))
        if size > chunk_size:
            f.seek(max(0, size - chunk_size))
            h.update(f.read(chunk_size))
    return h.hexdigest()[:16]


def discover_inputs(input_dir: Path) -> Inputs:
    """Find the video, optional music, and notes in `input_dir`."""
    videos = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS)
    if not videos:
        raise SystemExit(f"No video found in {input_dir}. Drop an .mp4 there.")
    if len(videos) > 1:
        print(f"[io] Multiple videos found, using: {videos[0].name}")
    video = videos[0]

    musics = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in AUDIO_EXTS)
    music = musics[0] if musics else None
    if music:
        print(f"[io] Music: {music.name}")
    else:
        print("[io] No music file found, skipping background track.")

    notes_path = input_dir / "notes.md"
    notes, notes_text = _parse_notes(notes_path)
    return Inputs(video=video, music=music, notes=notes, notes_text=notes_text)


def _parse_notes(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    # YAML frontmatter parsing.
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        print(f"[io] Warning: notes.md frontmatter is not valid YAML ({e}); ignoring.")
        meta = {}
    return meta, m.group(2).strip()


def parse_timestamp(value: Any) -> float:
    """Accept '1:23', '01:23.5', or a float/int seconds value."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if ":" in s:
        parts = s.split(":")
        parts = [float(p) for p in parts]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return float(s)


def video_meta(path: Path) -> dict[str, Any]:
    """Run ffprobe to fetch fps, duration, dimensions."""
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ]
    out = subprocess.check_output(cmd)
    data = json.loads(out)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, den = v["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    return {
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": fps,
        "duration": float(data["format"]["duration"]),
        "n_frames": int(v.get("nb_frames", 0)) or round(float(data["format"]["duration"]) * fps),
    }


def cache_key(*paths: Path) -> str:
    """Combined hash of multiple inputs, for cache invalidation."""
    h = hashlib.sha1()
    for p in paths:
        h.update(_file_hash(p).encode())
    return h.hexdigest()[:16]


def cache_key_str(*parts: str) -> str:
    """Hash of arbitrary string parameters, for cache invalidation."""
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8"))
    return h.hexdigest()[:12]


def run(cmd: list[str], **kwargs: Any) -> None:
    """Run a subprocess, stream output, raise on failure."""
    print(f"[run] {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kwargs)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
