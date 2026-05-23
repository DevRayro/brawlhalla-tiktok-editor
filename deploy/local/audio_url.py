"""Download an audio track from a URL (YouTube, SoundCloud, direct MP3, etc.).

Used when the user pastes a link in the music field instead of uploading a
file. We download with yt-dlp, transcode to a clean 192 kbps MP3 with ffmpeg,
and return the local file path.

The download runs in a thread pool from the FastAPI handler — yt-dlp is
synchronous and can take 10-60 seconds depending on network and source length.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional


# Conservative whitelist of URL hosts we trust. yt-dlp supports thousands of
# sites but we only need a handful for this app, and refusing the rest avoids
# the user accidentally pointing at a non-audio resource.
_ALLOWED_HOST_PATTERNS = [
    r"(?:^|\.)youtube\.com$",
    r"(?:^|\.)youtu\.be$",
    r"(?:^|\.)music\.youtube\.com$",
    r"(?:^|\.)soundcloud\.com$",
    r"(?:^|\.)bandcamp\.com$",
    r"(?:^|\.)vimeo\.com$",
    r"(?:^|\.)dailymotion\.com$",
    r"(?:^|\.)twitch\.tv$",
    r"(?:^|\.)mixcloud\.com$",
]

_DIRECT_MEDIA_RE = re.compile(
    r"\.(mp3|wav|m4a|aac|ogg|opus|flac|webm)(\?|$)", re.IGNORECASE
)


def is_supported_url(url: str) -> bool:
    """Quick pre-flight: does this look like a URL we can download from?
    We accept either a known streaming host OR a direct media file URL."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    # Direct media file: trust the extension.
    if _DIRECT_MEDIA_RE.search(url):
        return True
    # Otherwise check the host against the allowlist.
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return any(re.search(p, host) for p in _ALLOWED_HOST_PATTERNS)


def download(
    url: str,
    out_dir: Path,
    *,
    progress: Optional[Callable[[int, str], None]] = None,
    max_seconds: int = 600,
) -> Path:
    """Download `url` as a 192k MP3 into `out_dir`. Returns the file path.

    Args:
        url: Source URL (YouTube, SoundCloud, direct audio file…).
        out_dir: Destination directory; will be created if missing.
        progress: Optional callback receiving (percent, message). Called from
                  the yt-dlp progress hook.
        max_seconds: Reject sources longer than this (default 10 minutes).
                     Avoids accidentally downloading a 3-hour podcast.

    Raises:
        ValueError: URL not on the allowlist, or the source is too long.
        RuntimeError: yt-dlp failed.
    """
    if not is_supported_url(url):
        raise ValueError("URL non supportée (host non autorisé ou pas un lien audio direct)")

    out_dir.mkdir(parents=True, exist_ok=True)

    # Lazy import — the local server may run before the venv has yt-dlp
    # (e.g. just after a self-update that bumped requirements.txt).
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "yt-dlp n'est pas installé. Relance le setup ou pip install yt-dlp."
        ) from e

    if progress:
        progress(2, "Connexion à la source…")

    def _hook(d: dict) -> None:
        if not progress:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            if total:
                pct = 5 + int(70 * done / total)
                eta = d.get("eta") or 0
                msg = f"Téléchargement {done // 1024 // 1024}/{total // 1024 // 1024} MB"
                if eta:
                    msg += f" (≈{eta}s restantes)"
                progress(pct, msg)
            else:
                progress(50, f"Téléchargement {done // 1024} KB")
        elif d.get("status") == "finished":
            progress(75, "Conversion MP3…")

    # Use a temp dir so a partial download doesn't pollute the real out_dir.
    with tempfile.TemporaryDirectory(prefix="bh-audio-", dir=str(out_dir)) as tmp_str:
        tmp = Path(tmp_str)

        ydl_opts: dict = {
            "format": "bestaudio/best",
            "outtmpl": str(tmp / "%(title).80s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [_hook],
            "match_filter": _build_duration_filter(max_seconds),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
            except yt_dlp.utils.DownloadError as e:  # type: ignore[attr-defined]
                raise RuntimeError(f"Échec du téléchargement : {e}") from e

        # Find the resulting .mp3 in the temp dir.
        mp3s = list(tmp.glob("*.mp3"))
        if not mp3s:
            raise RuntimeError("Téléchargement réussi mais aucun MP3 produit (codec ?)")
        # Move to a stable name in out_dir (sanitize the title).
        title = (info or {}).get("title") or "music"
        safe = _safe_filename(title) + ".mp3"
        final = out_dir / safe
        # Avoid clobber by appending a counter if a same-named file exists.
        i = 1
        while final.exists():
            final = out_dir / f"{_safe_filename(title)}-{i}.mp3"
            i += 1
        shutil.move(str(mp3s[0]), final)
        if progress:
            progress(100, "Terminé")
        return final


def _build_duration_filter(max_seconds: int):
    """yt-dlp match_filter that rejects too-long sources before download."""
    def f(info_dict, *, incomplete=False):
        d = info_dict.get("duration")
        if d and d > max_seconds:
            return f"Source trop longue : {int(d)}s > {max_seconds}s"
        return None
    return f


def _safe_filename(name: str) -> str:
    """Sanitize a string for use as a filename across OSes."""
    name = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:80] or "audio"
