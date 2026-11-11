"""Place the voiced lines on a timeline, mix over the music, write subtitles and the video."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import media
from .text import text_of
from .voice import RATE, read_audio


def place(segs: list[dict], total_s: float, dst: Path, max_delay: float = 1.0, gap: float = 0.08) -> dict:
    """Lay the takes on one track. A line normally starts where he started it; if the line before
    is still speaking, it starts just after it, up to `max_delay` late. Most lines finish early,
    so the delay is absorbed within a line or two. Sets s["placed"] and s["overlap"] (seconds the
    line still runs into the next one)."""
    track = np.zeros(int(total_s * RATE) + 1, dtype=np.float32)
    voiced = [s for s in segs if s.get("take")]
    cursor, delayed = float("-inf"), 0
    for s in voiced:
        start = float(s["start"])
        if cursor + gap > start:
            start = min(cursor + gap, start + max_delay)
            delayed += 1
        s["placed"] = round(start, 3)
        cursor = start + float(s["take"]["seconds"])
        clip = read_audio(Path(s["take"]["file"]), RATE)
        i = int(round(start * RATE))
        j = min(len(track), i + len(clip))
        if j > i:
            track[i:j] += clip[: j - i]
    for a, b in zip(voiced, voiced[1:]):
        a["overlap"] = round(max(0.0, a["placed"] + a["take"]["seconds"] - b["placed"]), 2)
    if voiced:
        voiced[-1]["overlap"] = 0.0
    for s in segs:
        if not s.get("take"):
            s.pop("placed", None)
            s.pop("overlap", None)
    pcm = (np.clip(track, -1.0, 1.0) * 32767).astype("<i2")
    raw = dst.with_suffix(".pcm")
    raw.write_bytes(pcm.tobytes())
    media.ffmpeg("-f", "s16le", "-ar", RATE, "-ac", "1", "-i", raw, "-c:a", "pcm_s16le", dst)
    raw.unlink()
    return {"delayed": delayed, "overlapping": sum(1 for s in voiced if s.get("overlap", 0) > 0.1)}


def mix(placed: Path, vocals: Path, music: Path, dst: Path, lufs: float) -> dict:
    """Set the dub as loud as his voice was in the original, add the music bed, bring the mix to `lufs`.

    Fixed gains (measured, then applied), so the voice/music balance is the original's and the
    music keeps its dynamics; a limiter only catches peaks.
    """
    voice_lufs = max(-40.0, min(-10.0, media.loudness(vocals)))
    dub_lufs = media.loudness(placed)
    gain = max(-20.0, min(30.0, voice_lufs - dub_lufs))
    premix = dst.with_name("premix.wav")
    media.ffmpeg("-i", placed, "-i", music, "-filter_complex",
                 f"[0:a]volume={gain:.2f}dB,aresample=48000,aformat=channel_layouts=stereo[d];"
                 f"[1:a]aresample=48000,aformat=channel_layouts=stereo[m];"
                 f"[d][m]amix=inputs=2:normalize=0:duration=longest",
                 "-c:a", "pcm_f32le", premix)
    final_gain = max(-20.0, min(20.0, lufs - media.loudness(premix)))
    media.ffmpeg("-i", premix, "-af", f"volume={final_gain:.2f}dB,alimiter=limit=0.89:level=0",
                 "-ar", "48000", "-c:a", "pcm_s16le", dst)
    premix.unlink()
    return {"voice_lufs": round(voice_lufs, 1), "dub_gain_db": round(gain, 1), "final_gain_db": round(final_gain, 1),
            "final_lufs": round(media.loudness(dst), 1)}


def mux(video: Path, dub: Path, dst: Path, dub_lang: str, orig_lang: str) -> None:
    """Video copied as is; the dub is the default audio track, the original audio the second one."""
    cmd = ["-i", video, "-i", dub, "-map", "0:v:0", "-map", "1:a:0"]
    meta = ["-metadata:s:a:0", f"language={dub_lang}", "-disposition:a:0", "default"]
    enc = ["-c:v", "copy", "-c:a:0", "aac", "-b:a:0", "192k"]
    if media.has_audio(video):
        cmd += ["-map", "0:a:0"]
        meta += ["-metadata:s:a:1", f"language={orig_lang}", "-disposition:a:1", "0"]
        try:  # keep the original track untouched if MP4 can carry its codec
            media.ffmpeg(*cmd, *enc, "-c:a:1", "copy", *meta, "-movflags", "+faststart", dst)
            return
        except media.ToolError:
            enc += ["-c:a:1", "aac", "-b:a:1", "192k"]
    media.ffmpeg(*cmd, *enc, *meta, "-movflags", "+faststart", dst)


# --- subtitles ---------------------------------------------------------------------------------------

def _wrap(text: str, width: int = 42) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    return lines + ([cur] if cur else [])


def _write(path: Path, cues: list[tuple[float, float, str]]) -> None:
    path.write_text("\n".join(f"{i}\n{media.ts(a)} --> {media.ts(b)}\n{t}\n"
                              for i, (a, b, t) in enumerate(cues, start=1)), encoding="utf-8")


def subtitles(segs: list[dict], src_path: Path, tgt_path: Path) -> None:
    src, tgt = [], []
    for s in segs:
        cur = []
        for w in s["words"]:  # source cues follow the word times
            if cur and len(_wrap(text_of(cur + [w]))) > 2:
                src.append((cur[0]["start"], cur[-1]["end"], "\n".join(_wrap(text_of(cur)))))
                cur = []
            cur.append(w)
        if cur:
            src.append((cur[0]["start"], cur[-1]["end"], "\n".join(_wrap(text_of(cur)))))
        if not s.get("en", "").strip():
            continue
        # target cues: from where the dub line starts for as long as it speaks, shared out by length
        begin = s.get("placed", s["start"])
        spoken = s["take"]["seconds"] if s.get("take") else s["end"] - s["start"]
        stop = begin + max(spoken, 1.0)
        lines = _wrap(s["en"])
        chunks = ["\n".join(lines[i:i + 2]) for i in range(0, len(lines), 2)]
        total = sum(len(c) for c in chunks)
        t = begin
        for k, c in enumerate(chunks):
            end = stop if k == len(chunks) - 1 else t + (stop - begin) * len(c) / total
            tgt.append((t, end, c))
            t = end
    _write(src_path, src)
    _write(tgt_path, tgt)
