"""Mix the source voice track with the background music using sidechain ducking.

We do this in ffmpeg ahead of the Remotion render so Remotion only has to mux
a single audio track. ffmpeg's `sidechaincompress` filter dynamically lowers
the music whenever the voice is loud.
"""
from __future__ import annotations

from pathlib import Path

from . import io_utils
from .. import config


def mix(video: Path, music: Path | None, out_dir: Path, music_db: float | None = None) -> Path:
    """Produce a single .m4a containing voice + (optionally) music with ducking."""
    key = io_utils.cache_key(video, *( [music] if music else [] ))
    out = out_dir / f"mixed-{key}.m4a"
    if out.exists():
        print(f"[audio] Cache hit: {out.name}")
        return out

    if music is None:
        # Just re-encode the voice track for clean handoff to Remotion.
        io_utils.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video),
            "-vn", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(out),
        ])
        return out

    music_gain = music_db if music_db is not None else config.MUSIC_DB_DEFAULT  # e.g. -18
    duck_gain = config.DUCK_DB  # e.g. -8 (extra reduction during voice)

    # Filter graph:
    #   voice  -> normalize a bit             -> [v]
    #   music  -> base volume(music_gain)     -> [m_base]
    #   m_base -> sidechain comp (driven by v) -> [m_ducked]
    # Then mix v + m_ducked.
    #
    # Why no `makeup` arg: setting a negative makeup confuses ffmpeg on some
    # builds and produces a "Result too large" error (libavfilter rounds the
    # compressed signal to a giant value). Instead we let the sidechain
    # attenuate naturally, and apply the duck_gain as an extra `volume` on the
    # ducked path.
    filter_complex = (
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        "acompressor=threshold=-20dB:ratio=2:attack=10:release=200[v];"
        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={music_gain}dB[m_base];"
        # Split voice for sidechain (compressor needs a separate copy as input).
        "[v]asplit=2[v_out][v_sc];"
        "[m_base][v_sc]sidechaincompress="
        "threshold=0.05:ratio=10:attack=20:release=350:level_sc=1[m_duck1];"
        f"[m_duck1]volume={duck_gain}dB[m_duck];"
        "[v_out][m_duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"
    )

    io_utils.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video),
        "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", filter_complex,
        "-map", "[mix]",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-shortest",
        str(out),
    ])
    print(f"[audio] Mixed → {out.name}")
    return out
