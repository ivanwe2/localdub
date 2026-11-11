"""ffmpeg / ffprobe helpers."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


class ToolError(RuntimeError):
    pass


def need(*tools: str) -> None:
    for t in tools:
        if shutil.which(t) is None:
            raise ToolError(f"{t} not found on PATH")


def run(*cmd, cwd=None) -> str:
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=cwd)
    if p.returncode != 0:
        raise ToolError(f"command failed ({p.returncode}): {' '.join(map(str, cmd))}\n{p.stderr.strip()[-1500:]}")
    return p.stdout


def ffmpeg(*args) -> None:
    run("ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args)


def duration(path: Path) -> float:
    return float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path))


def has_audio(path: Path) -> bool:
    return "audio" in run("ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path)


def loudness(path: Path) -> float:
    """Integrated loudness in LUFS (EBU R128)."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af", "ebur128=framelog=quiet",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
    if not m:
        raise ToolError(f"could not measure loudness of {path}")
    return float(m[-1])


def silences(path: Path, noise_db: float = -35.0, min_s: float = 0.4) -> list[tuple[float, float]]:
    """(start, end) of silent stretches."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af",
                        f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", p.stderr)]
    return list(zip(starts, ends))


def trim_silence(src: Path, dst: Path, rate: int) -> None:
    """Mono PCM at `rate`, leading and trailing silence removed."""
    f = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05"
    ffmpeg("-i", src, "-af", f"{f},areverse,{f},areverse", "-ac", "1", "-ar", rate, "-c:a", "pcm_s16le", dst)


def tempo(src: Path, dst: Path, factor: float, rate: int) -> None:
    ffmpeg("-i", src, "-af", f"atempo={factor:.4f}", "-ac", "1", "-ar", rate, "-c:a", "pcm_s16le", dst)


def ts(t: float, sep: str = ",") -> str:
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}"


def mmss(t: float) -> str:
    s = int(t)
    return f"{s // 60:02d}:{s % 60:02d}.{int((t - s) * 10)}"


def parse_mmss(text: str) -> float:
    """'1:23', '01:23.5' or '83.5' -> seconds."""
    parts = text.strip().split(":")
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    return sec
