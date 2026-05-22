"""Strip backgrounds from every catgirl image in deploy/frontend/ and emit
clean PNGs into deploy/frontend/mascots/.
"""
from __future__ import annotations

from pathlib import Path

from rembg import remove
from PIL import Image


SRC_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
ROOT = Path(__file__).resolve().parent / "frontend"
OUT_DIR = ROOT / "mascots"


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    # Skip files that are part of the app itself.
    skip_names = {"app.js", "index.html", "styles.css"}
    candidates = [
        p for p in ROOT.iterdir()
        if p.is_file() and p.suffix.lower() in SRC_EXTS and p.name not in skip_names
    ]
    print(f"Found {len(candidates)} source images")

    for i, src in enumerate(sorted(candidates), 1):
        out = OUT_DIR / f"mascot-{i:02d}.png"
        if out.exists():
            print(f"  [{i}] cached → {out.name}")
            continue
        print(f"  [{i}] processing {src.name}…")
        with src.open("rb") as f:
            data = f.read()
        result = remove(data)
        # Convert bytes to PIL, then save as PNG (rembg returns PNG bytes already)
        with out.open("wb") as f:
            f.write(result)
        # Trim transparent borders so the mascot fills its box nicely.
        im = Image.open(out).convert("RGBA")
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
        # Cap size so we don't ship a 4K png.
        max_side = 600
        if max(im.size) > max_side:
            scale = max_side / max(im.size)
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        im.save(out, optimize=True)
        print(f"     → {out.name} ({im.size[0]}x{im.size[1]})")

    # Move the originals out of frontend/ so they don't get bundled / served.
    archive = ROOT.parent / "_mascot-sources"
    archive.mkdir(exist_ok=True)
    for src in candidates:
        dst = archive / src.name
        src.rename(dst)
    print(f"Originals moved to {archive}")
    print(f"Done. {len(list(OUT_DIR.glob('mascot-*.png')))} mascots in {OUT_DIR}")


if __name__ == "__main__":
    main()
