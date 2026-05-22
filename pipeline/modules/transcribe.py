"""Word-level transcription via faster-whisper.

Output schema (cached as JSON):
{
  "language": "fr",
  "words": [
    {"text": "salut", "start": 0.12, "end": 0.41},
    ...
  ]
}

We bias Whisper with a domain-specific `initial_prompt` (Brawlhalla legend
names, attack moves, FR slang) and apply a post-pass find/replace for the
most common mishearings.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import io_utils
from .. import config


LEXICON_DIR = Path(__file__).resolve().parent.parent / "lexicon"


def _audio_for_whisper(video: Path, dst: Path) -> None:
    """Extract a 16kHz mono wav, the format Whisper prefers."""
    io_utils.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dst),
    ])


def _load_lexicon(extra_terms: list[str] | None = None) -> str:
    """Build the `initial_prompt` text from the lexicon file + per-video extras.

    Whisper caps the prompt at ~244 tokens; we keep it compact (one big
    comma-separated list).
    """
    path = LEXICON_DIR / "brawlhalla.txt"
    terms: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            terms.append(line)
    if extra_terms:
        terms.extend(t.strip() for t in extra_terms if t and t.strip())

    # Format as a natural-language sentence — works better than a raw list.
    if not terms:
        return ""
    joined = ", ".join(terms)
    return f"Commentaire d'un joueur de Brawlhalla en français. Termes du jeu: {joined}."


def _load_replacements() -> dict[str, str]:
    path = LEXICON_DIR / "replacements.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k.lower(): v for k, v in data.items() if not k.startswith("_")}


_REPLACEMENT_PATTERN_CACHE: tuple[re.Pattern[str], dict[str, str]] | None = None


def _apply_replacements(text: str, replacements: dict[str, str]) -> str:
    """Whole-word, case-insensitive find/replace. Multi-word keys supported."""
    global _REPLACEMENT_PATTERN_CACHE
    if not replacements:
        return text

    if _REPLACEMENT_PATTERN_CACHE is None or _REPLACEMENT_PATTERN_CACHE[1] is not replacements:
        # Sort by length desc so longer phrases match before shorter substrings.
        keys = sorted(replacements.keys(), key=len, reverse=True)
        # Word boundaries don't match across spaces — use a custom alternation.
        pattern = re.compile(
            r"(?<![\wÀ-ÿ'])(" + "|".join(re.escape(k) for k in keys) + r")(?![\wÀ-ÿ'])",
            re.IGNORECASE,
        )
        _REPLACEMENT_PATTERN_CACHE = (pattern, replacements)
    pattern, _ = _REPLACEMENT_PATTERN_CACHE

    def sub(m: re.Match[str]) -> str:
        return replacements[m.group(1).lower()]

    return pattern.sub(sub, text)


def transcribe(video: Path, cache_dir: Path, extra_terms: list[str] | None = None) -> dict[str, Any]:
    """Run Whisper, return word-level segments. Cached by source hash + lexicon hash."""
    # Cache key includes a hash of the lexicon files so changes invalidate.
    lex_files = [LEXICON_DIR / "brawlhalla.txt", LEXICON_DIR / "replacements.json"]
    lex_hash_inputs = [video] + [p for p in lex_files if p.exists()]
    key = io_utils.cache_key(*lex_hash_inputs)
    extras_key = ""
    if extra_terms:
        # Stable hash of the per-video extras.
        import hashlib
        extras_key = "-" + hashlib.sha1("|".join(sorted(extra_terms)).encode()).hexdigest()[:6]
    cache_path = cache_dir / f"transcript-{key}{extras_key}.json"
    if cache_path.exists():
        print(f"[transcribe] Cache hit: {cache_path.name}")
        return io_utils.read_json(cache_path)

    wav_path = cache_dir / f"audio-{io_utils.cache_key(video)}.wav"
    if not wav_path.exists():
        print("[transcribe] Extracting audio for Whisper...")
        _audio_for_whisper(video, wav_path)

    # Lazy import so the rest of the pipeline can be inspected without torch.
    from faster_whisper import WhisperModel

    # Auto-detect the best device. CUDA > CPU. (MPS isn't supported by
    # faster-whisper / ctranslate2 on Apple Silicon.)
    device = "cpu"
    compute_type = "int8"
    try:
        import torch
        if torch.cuda.is_available():
            device = "cuda"
            compute_type = "float16"
            print(f"[transcribe] CUDA detected: {torch.cuda.get_device_name(0)}")
    except Exception:
        pass

    print(f"[transcribe] Loading model {config.WHISPER_MODEL} on {device} ({compute_type})...")
    model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute_type)

    initial_prompt = _load_lexicon(extra_terms)
    if initial_prompt:
        print(f"[transcribe] Using lexicon prompt ({len(initial_prompt)} chars)")

    print("[transcribe] Transcribing (word timestamps)...")
    segments, info = model.transcribe(
        str(wav_path),
        language=config.WHISPER_LANG,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        beam_size=5,
        initial_prompt=initial_prompt or None,
    )

    replacements = _load_replacements()
    n_replaced = 0

    words: list[dict[str, Any]] = []
    for seg in segments:
        if not seg.words:
            continue
        for w in seg.words:
            txt = (w.word or "").strip()
            if not txt:
                continue
            if replacements:
                new_txt = _apply_replacements(txt, replacements)
                if new_txt != txt:
                    n_replaced += 1
                    txt = new_txt
            words.append({
                "text": txt,
                "start": float(w.start),
                "end": float(w.end),
                "prob": float(getattr(w, "probability", 0.0) or 0.0),
            })

    result = {"language": info.language, "words": words}
    io_utils.write_json(cache_path, result)
    print(f"[transcribe] {len(words)} words, {n_replaced} fixed by lexicon → {cache_path.name}")
    return result


def group_words(words: list[dict[str, Any]], group_size: int, max_gap: float) -> list[dict[str, Any]]:
    """Group words into 2-4 word subtitle groups, respecting silence gaps.

    Each group has start/end and a list of words with their relative timing.
    """
    groups: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    last_end = -1.0

    def flush() -> None:
        if not cur:
            return
        groups.append({
            "start": cur[0]["start"],
            "end": cur[-1]["end"],
            "words": [{"text": w["text"], "start": w["start"], "end": w["end"]} for w in cur],
        })

    for w in words:
        gap = w["start"] - last_end if last_end >= 0 else 0
        if cur and (len(cur) >= group_size or gap > max_gap):
            flush()
            cur = []
        cur.append(w)
        last_end = w["end"]
    flush()
    return groups
