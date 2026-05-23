"""Self-update logic for the local server.

Strategy: download the latest GitHub release tarball, extract it on top of
the install dir (preserving .venv, models, node_modules, _local_jobs). If
requirements.txt or remotion/package.json changed, run pip / npm install
automatically. Then ask the parent process to restart.

We talk to the GitHub Releases API (no auth needed for public repos) and
trust the published tag — no extra signature verification. Good enough for
a friend-distributed app.
"""
from __future__ import annotations

import io
import json
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


REPO = "DevRayro/brawlhalla-tiktok-editor"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases/latest"


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context that uses certifi's CA bundle when available, so
    we don't fall victim to the macOS Python.org installer not having any
    system CAs configured."""
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()

# Paths in the install tree that we MUST NEVER overwrite, even if they
# exist in the new tarball. These hold downloaded models, the venv,
# user-generated jobs, and everything else that's expensive or irreplaceable.
PRESERVE = [
    ".venv",
    "models",
    "_local_jobs",
    "remotion/node_modules",
    "remotion/public",  # may contain user-uploaded source/audio mid-render
    ".install-complete",
    "input",            # local user files
    "output",
    "work",
]


@dataclass
class VersionInfo:
    current: str       # e.g. "1.0.6"
    latest: str        # e.g. "1.0.7"
    update_available: bool
    download_url: str  # tarball_url from the GitHub release
    notes: str         # body of the release notes
    published_at: str


def _read_local_version(install_dir: Path) -> str:
    """Read the version from package.json or VERSION file. Falls back to 0.0.0."""
    candidates = [
        install_dir / "VERSION",
        install_dir / "remotion" / "package.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        if p.suffix == ".json":
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                v = data.get("version")
                if v:
                    return str(v)
            except Exception:
                pass
        else:
            txt = p.read_text(encoding="utf-8").strip()
            if txt:
                return txt
    return "0.0.0"


def _semver_cmp(a: str, b: str) -> int:
    """Compare 'X.Y.Z' strings. Returns -1, 0, or 1."""
    pa = [int(x) for x in (a.lstrip("v").split(".") + ["0", "0", "0"])[:3] if x.isdigit() or x == "0"]
    pb = [int(x) for x in (b.lstrip("v").split(".") + ["0", "0", "0"])[:3] if x.isdigit() or x == "0"]
    if pa < pb: return -1
    if pa > pb: return 1
    return 0


def check(install_dir: Path) -> VersionInfo:
    current = _read_local_version(install_dir)
    req = urllib.request.Request(
        RELEASES_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "brawlhalla-editor"},
    )
    with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
        data = json.loads(resp.read())
    tag = (data.get("tag_name") or "0.0.0").lstrip("v")
    return VersionInfo(
        current=current,
        latest=tag,
        update_available=_semver_cmp(current, tag) < 0,
        download_url=data.get("tarball_url") or "",
        notes=data.get("body") or "",
        published_at=data.get("published_at") or "",
    )


# ---------------------------------------------------------------------------
# Apply update
# ---------------------------------------------------------------------------

@dataclass
class UpdateState:
    stage: str = "idle"          # idle | downloading | extracting | deps | done | error
    progress: int = 0            # 0..100
    message: str = ""
    error: str = ""


_state: UpdateState = UpdateState()
_state_lock = threading.Lock()


def state() -> dict:
    with _state_lock:
        return asdict(_state)


def _set(**fields) -> None:
    with _state_lock:
        for k, v in fields.items():
            setattr(_state, k, v)


def _download_tarball(url: str, dst: Path) -> None:
    _set(stage="downloading", progress=5, message="Téléchargement de la mise à jour…")
    req = urllib.request.Request(url, headers={"User-Agent": "brawlhalla-editor"})
    with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        with dst.open("wb") as f:
            read = 0
            while True:
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                if total > 0:
                    pct = 5 + int(45 * read / total)
                    _set(progress=pct,
                         message=f"Téléchargement {read//1024//1024}/{total//1024//1024} MB")


def _safe_copy_tree(src: Path, dst: Path) -> tuple[bool, bool]:
    """Recursively copy `src` into `dst`, skipping any path that's in PRESERVE.
    Returns (requirements_changed, package_json_changed)."""
    req_changed = False
    pkg_changed = False
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        rel_posix = rel.as_posix()
        # Don't overwrite preserved paths.
        if any(rel_posix == p or rel_posix.startswith(p + "/") for p in PRESERVE):
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Detect dependency-affecting changes before overwriting.
            if rel_posix == "pipeline/requirements.txt" and target.exists():
                if target.read_bytes() != item.read_bytes():
                    req_changed = True
            elif rel_posix == "remotion/package.json" and target.exists():
                if target.read_bytes() != item.read_bytes():
                    pkg_changed = True
            shutil.copy2(item, target)
    return req_changed, pkg_changed


def _run(cmd: list[str], cwd: Path) -> int:
    """Run a subprocess, ignore output (we already report status separately)."""
    return subprocess.call(cmd, cwd=str(cwd))


def apply(install_dir: Path) -> None:
    """Run the whole update flow. Sets _state along the way. Caller should
    schedule a restart when stage='done'."""
    try:
        info = check(install_dir)
        if not info.update_available:
            _set(stage="done", progress=100,
                 message=f"Déjà à jour (v{info.current})")
            return
        if not info.download_url:
            raise RuntimeError("URL de téléchargement manquante dans la release")

        with tempfile.TemporaryDirectory(prefix="bh-update-") as tmp:
            tmp_path = Path(tmp)
            tarball = tmp_path / "release.tar.gz"
            _download_tarball(info.download_url, tarball)

            _set(stage="extracting", progress=55, message="Extraction…")
            with tarfile.open(tarball, "r:gz") as tf:
                tf.extractall(tmp_path)

            # GitHub tarballs unpack into a single top-level dir with a hash
            # suffix (e.g. DevRayro-brawlhalla-tiktok-editor-abcd123).
            top_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
            if not top_dirs:
                raise RuntimeError("L'archive téléchargée est vide")
            new_root = top_dirs[0]

            _set(stage="extracting", progress=70, message="Application des nouveaux fichiers…")
            req_changed, pkg_changed = _safe_copy_tree(new_root, install_dir)

            # Bump pip / npm if the manifests changed.
            if req_changed:
                _set(stage="deps", progress=80,
                     message="Nouvelles dépendances Python détectées, installation…")
                py = install_dir / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
                if py.exists():
                    _run([str(py), "-m", "pip", "install", "-r",
                          str(install_dir / "pipeline" / "requirements.txt")], install_dir)

            if pkg_changed:
                _set(stage="deps", progress=90,
                     message="Nouvelles dépendances Remotion détectées, installation…")
                _run(["npm", "install", "--no-audit", "--no-fund"],
                     install_dir / "remotion")

        _set(stage="done", progress=100,
             message=f"Mise à jour terminée (v{info.latest}). Redémarrage…")

    except Exception as e:
        _set(stage="error", error=str(e),
             message=f"Échec de la mise à jour : {e}")


def apply_async(install_dir: Path) -> None:
    """Kick off apply() in a background thread."""
    _set(stage="downloading", progress=0, message="Démarrage…", error="")
    threading.Thread(target=apply, args=(install_dir,), daemon=True).start()
