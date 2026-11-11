"""A generated test video: made-up Bulgarian sentences (with typical spelling-by-ear errors), word timings,
and a matching video whose audio is a tone while 'he' speaks and silence in the pauses."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import numpy as np

SENTENCES = [
    "Днес отиваме на пазара в центъра на града.",
    "Искам да видим колко струват зеленчуците тази седмица.",
    "Миналия път доматите бяха ефтини, но сега не знам.",
    "Продавачът ни каза, че найемите на сергиите са се вдигнали.",
    "Това ми се стори доста странно.",
    "Купихме два килограма картофи и малко сирене.",
    "Сиренето беше най-ефтината стока на целия пазар.",
    "После Кирчу реши да опита мед от една баба.",
    "Медът беше толкова сладък, че купихме три буркана.",
    "След това седнахме в едно малко кафене.",
    "Кафето струваше два лева, което е добре.",
    "Фсички маси бяха заети, затова чакахме.",
    "Накрая се върнахме у дома с пълни торби.",
    "Утре ще пробваме другия пазар в квартала.",
    "Ако и там е ефтино, ще ви покажа какво купихме.",
    "Не забравяйте да се абонирате за още видеа.",
    "Ютубърите често казват това, но аз наистина го мисля.",
    "Благодаря, че гледахте, и до следващия път.",
]
LONG_PAUSE_AFTER = {6: 2.5}  # a longer pause between sentences 7 and 8


def words_with_times(start: float = 1.0) -> list[dict]:
    t, out = start, []
    for k, s in enumerate(SENTENCES):
        for w in s.split():
            d = 0.18 + 0.055 * len(w)
            out.append({"word": w, "start": round(t, 3), "end": round(t + d, 3)})
            t += d + 0.06
        t += 0.7 + LONG_PAUSE_AFTER.get(k, 0.0)
    return out


def make(dirpath: Path) -> tuple[Path, Path]:
    """Writes words.json and video.mp4 into dirpath; returns their paths."""
    dirpath.mkdir(parents=True, exist_ok=True)
    words = words_with_times()
    total = words[-1]["end"] + 2.0
    words_path = dirpath / "words.json"
    words_path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    rate = 44100
    audio = np.zeros(int(total * rate), dtype=np.float32)
    for k, w in enumerate(words):
        a, b = int(w["start"] * rate), int(w["end"] * rate)
        f = 140 + 15 * (k % 5)
        audio[a:b] = 0.3 * np.sin(2 * math.pi * f * np.arange(b - a) / rate)
    wav = dirpath / "voice.wav"
    pcm = (audio * 32767).astype("<i2").tobytes()
    raw = dirpath / "voice.pcm"
    raw.write_bytes(pcm)
    video = dirpath / "video.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1",
                    "-i", str(raw), str(wav)], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size=320x180:rate=25:duration={total:.2f}", "-i", str(wav),
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(video)], check=True)
    raw.unlink()
    wav.unlink()
    return video, words_path
