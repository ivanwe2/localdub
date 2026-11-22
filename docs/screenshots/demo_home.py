"""A made-up localdub home for the README screenshots: runs, lines, stems, voice samples and a video,
all generated here (no real media, speech or voices). Usage: python3 demo_home.py <home folder>"""
import json
import math
import random
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

home = Path(sys.argv[1])
random.seed(7)

# (start, end, the original, the dub): a market video, made up
SEGS = [
    (2, 9, "Добро утро от съботния пазар.", "Good morning from the Saturday market."),
    (10, 17, "Днес търсим най-хубавото сирене.", "Today we're looking for the best cheese."),
    (18, 26, "Тази жена идва тук от четиридесет години.", "This woman has come here for forty years."),
    (28, 35, "Казва, че тайната е в млякото.", "She says the secret is in the milk."),
    (36, 44, "Опитваме първо краве, после овче.", "We try cow's first, then sheep's."),
    (46, 55, "Овчето е по-солено, но по-меко.", "The sheep's is saltier, but softer."),
    (57, 63, "Килограмът е дванадесет лева.", "A kilo is twelve leva."),
    (64, 72, "Тук сиренето идва от планината.", "Here the cheese comes from the mountains."),
    (74, 83, "Продавачът ни дава да опитаме мед.", "The seller lets us taste some honey."),
    (85, 93, "Липовият мед е най-светъл.", "Linden honey is the lightest."),
    (95, 104, "Купуваме два буркана за вкъщи.", "We buy two jars for home."),
    (106, 118, "После отиваме при зеленчуците.", "Then we head to the vegetables."),
    (120, 131, "Доматите тази година закъсняха.", "The tomatoes are late this year."),
    (133, 142, "Но чушките вече са сладки.", "But the peppers are already sweet."),
    (144, 155, "Ще ги изпечем довечера.", "We'll roast them tonight."),
    (157, 167, "Ако ви хареса, абонирайте се.", "If you liked it, subscribe."),
]
LENGTH = 170


def segments(with_tgt=True, voiced=False):
    out = [{"id": i + 1, "start": a, "end": b, "src": s, **({"tgt": t} if with_tgt else {}),
            "slot": b - a, "budget": round((b - a) * 15)} for i, (a, b, s, t) in enumerate(SEGS)]
    if voiced:  # every line voiced; two of them worth a listen, as the line fixer lists them
        for seg in out:
            spoken = round(min(seg["slot"] - 0.4, len(seg["tgt"]) / 14), 2)
            seg.update(placed=seg["start"] + 0.1, overlap=0.0,
                       take={"seed": 42, "ok": True, "wer": 0.0, "why": "", "heard": seg["tgt"], "tries": 1,
                             "fit": "fits", "spoken": spoken, "sim": 0.95, "seconds": spoken, "file": ""})
        out[6]["take"].update(ok=False, why="last word 'leva' missing", heard="A kilo is twelve", tries=3, seed=1234)
        out[9]["take"].update(fit="sped up 15%, OVER by 0.6s", seconds=8.6)
        out[9]["overlap"] = 0.7
    return out


def speech_envelope(rate):
    """Loudness over time like speech: words of 0.15-0.5 s with short gaps inside each line."""
    env = [0.008] * (LENGTH * rate)
    for a, b, *_ in SEGS:
        t = a + random.uniform(0, 0.3)
        while t < b - 0.2:
            word = random.uniform(0.15, 0.5)
            peak = random.uniform(0.35, 1.0)
            for i in range(int(t * rate), min(int((t + word) * rate), len(env))):
                x = (i / rate - t) / word
                env[i] = max(env[i], peak * max(0.0, math.sin(math.pi * x)) ** 0.7)
            t += word + random.choice([0.04, 0.06, 0.1, 0.25])
    return env


def music_envelope(rate):
    level, env = 0.3, []
    for i in range(LENGTH * rate):
        if i % rate == 0:
            level = min(0.5, max(0.15, level + random.uniform(-0.04, 0.04)))
        t = i / rate
        env.append(level * (0.85 + 0.15 * math.sin(2 * math.pi * t / 11)))
    return env


def write_wav(path: Path, env, rate=2000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, e * (random.random() * 2 - 1))) * 32000)) for e in env))


def stems(d: Path):
    write_wav(d / "vocals.wav", speech_envelope(2000))
    write_wav(d / "music.wav", music_envelope(2000))


def tone(path: Path, seconds=1.0, rate=8000):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_wav(path, [0.0] * int(seconds * rate), rate)


def video(path: Path):
    """An abstract warm-gradient video (no people), so the page's players show a picture."""
    if not shutil.which("ffmpeg"):
        path.write_bytes(b"")
        return
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "gradients=s=640x360:c0=0x2a1208:c1=0xff5b2e:c2=0x16181b:c3=0xffb547:speed=0.004:d=40:r=10",
                    "-c:v", "libx264", "-crf", "34", "-pix_fmt", "yuv420p", str(path)], check=True)


def run(name, done, timings, files=None, seconds=float(LENGTH), outputs=None, created="2026-11-20T10:00:00",
        channel="market-tales", **extra):
    d = home / "runs" / name
    d.mkdir(parents=True, exist_ok=True)
    state = {"source": f"{home}/videos/{name}.mp4", "channel": channel, "created": created,
             "done": done, "timings": timings, "seconds": seconds, **extra}
    if outputs:
        state["outputs"] = outputs
    (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
    for rel, content in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return d


ch = home / "channels" / "market-tales"
ch.mkdir(parents=True, exist_ok=True)
(ch / "profile.json").write_text(json.dumps({"source_lang": "bg", "target_lang": "en", "source_language_name": "Bulgarian",
                                              "target_language_name": "English", "speaker": "woman", "voice_ref": "voice.wav"}))
tone(ch / "voice.wav")
(home / "videos").mkdir(parents=True, exist_ok=True)
for v in ("market-day-ep12.mp4", "yt-k3Zp9xQ2-rooftop-garden.mp4"):
    (home / "videos" / v).write_bytes(b"x" * 1000)

before_voice = {"prep": 1.2, "separate": 20.4, "transcribe": 13.1, "spelling": 17.0, "review1": 300, "translate": 47.2, "review2": 240}
finished = ["prep", "separate", "transcribe", "spelling", "review1", "translate", "review2", "voice", "mix"]

# on air: being voiced
d = run("market-day-ep12", finished[:7], before_voice, {"segments.json": segments()}, created="2026-11-20T16:00:00")
stems(d)
lines = ["Hi everyone, welcome back to the channel, today we have something really fun for you.",
         "I honestly did not expect it to be this cheap, but here we are.",
         "So we packed our bags, grabbed the camera, and went straight to the city centre.",
         "Let me know in the comments what you would have done in my place."]
cands = []
for n, (a, b, sim) in enumerate([(64.2, 76.0, 0.968), (18.4, 29.9, 0.957), (120.3, 131.0, 0.931)], start=1):
    tone(d / f"voice-candidates/candidate-{n}.wav")
    tests = [f"voice-candidates/test-c{n}-{k}.wav" for k in range(1, 5)]
    for t in tests:
        tone(d / t)
    cands.append({"n": n, "start": a, "end": b, "sim": sim, "path": f"voice-candidates/candidate-{n}.wav", "tests": tests})
current_tests = [f"voice-candidates/test-cur-{k}.wav" for k in range(1, 5)]
for t in current_tests:
    tone(d / t)
tone(d / "voice-candidates/current.wav")
(d / "voice-candidates.json").write_text(json.dumps({
    "version": 2, "lang": "en", "lines": lines, "scored": True, "candidates": cands,
    "current": {"from": "harbour-walk, candidate 2 (00:41-00:53)", "sim": 0.956, "path": "voice-candidates/current.wav",
                "tests": current_tests},
    "picked": None}), encoding="utf-8")

# waiting for review 1
review1 = "\n".join([
    "status: pending", "# Review 1 of 2: the transcript.", "",
    "[9] 01:14.6   name? Кирчу -> Кирчо (not applied)   unknown: ютубъри",
    "BG: Хлебарят Кирчу ни дава да опитаме от питката, поздрави на ютубъри.", "",
    "[11] 01:35.8   unknown: дванайсе",
    "BG: Купуваме два хляба по дванайсе лева.", "",
    "[12] 01:46.3   fixed: найемите -> наемите",
    "BG: После минаваме покрай наемите сергии на пазара.", "",
])
d = run("village-bakery", finished[:4], {"prep": 1, "separate": 22, "transcribe": 15, "spelling": 18},
        {"review-1-transcript.txt": review1, "spelled.json": segments(False)}, seconds=495.0, created="2026-11-20T14:00:00")
stems(d)

# finished, waiting for its Shorts to be picked
shorts = "\n".join([
    "status: pending", "# Shorts candidates, strongest first.", "",
    '[ ] 1   00:02-00:35 (33s)   sentences 1-4   "The secret is in the milk"',
    "    hook:   Good morning from the Saturday market.", "    why:    A whole little story with a strong first line.",
    "    screen: hands at the cheese stall", "    starts: Good morning from the Saturday market.", "    ends:   She says the secret is in the milk.", "",
    '[ ] 2   01:04-01:33 (29s)   sentences 8-10   "Mountain cheese and linden honey"',
    "    hook:   Here the cheese comes from the mountains.", "    why:    Tasting on camera, quick and warm.",
    "    screen: the honey jar in the light", "    starts: Here the cheese comes from the mountains.", "    ends:   Linden honey is the lightest.", "",
    '[ ] 3   02:00-02:47 (47s)   sentences 13-16   "Late tomatoes, sweet peppers"',
    "    hook:   The tomatoes are late this year.", "    why:    The ending of the video; weaker without context.",
    "    screen: vegetables", "    starts: The tomatoes are late this year.", "    ends:   If you liked it, subscribe.", "",
])
d = run("cheese-tour", finished, {**before_voice, "voice": 105, "mix": 4},
        {"segments.json": segments(voiced=True), "shorts/dub/candidates.txt": shorts},
        outputs={"video": f"{home}/runs/cheese-tour/cheese-tour.en.mp4", "subtitles": []}, created="2026-11-19T18:00:00")
stems(d)
video(d / "cheese-tour.en.mp4")

# done
d = run("rooftop-garden", finished, {**before_voice, "voice": 212, "mix": 6}, {"segments.json": segments(voiced=True)},
        seconds=612.0, outputs={"video": f"{home}/runs/rooftop-garden/rooftop-garden.en.mp4", "subtitles": []},
        created="2026-11-18T12:00:00")
(d / "rooftop-garden.en.mp4").write_bytes(b"")

# Shorts only, on a channel that has no dub language yet: its transcript checked, the Shorts proposed
clips = home / "channels" / "market-clips"
clips.mkdir(parents=True, exist_ok=True)
(clips / "profile.json").write_text(json.dumps({"source_lang": "bg", "target_lang": "", "source_language_name": "Bulgarian",
                                                "target_language_name": "", "voice_ref": ""}))
source = home / "videos" / "saturday-market.mp4"
video(source)
d = run("saturday-clips", finished[:5], {"prep": 1.1, "separate": 19.8, "transcribe": 12.7, "spelling": 16.2},
        {"segments.json": segments(False), "shorts/original/candidates.txt": shorts.replace("Shorts candidates", "Shorts candidates (original)")},
        created="2026-11-21T09:00:00", channel="market-clips", mode="shorts", video=str(source))
stems(d)
print("demo home ready:", home)
