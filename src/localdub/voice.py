"""Voice sample, TTS takes with an automatic check, and fitting lines to their slots.

The check: every take is transcribed back with Nemotron (on the second GPU, while
the TTS model sits on the first) and compared with the script. A take passes if the
word error rate is low and the last word is there, which catches the clipped endings
("vide" for "video"). Lines that fail are voiced again with the next seed, up to
len(settings["seeds"]) takes; the best take wins.
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from . import media
from .asr import transcribe_folder

RATE = 24000


# --- audio as numbers ----------------------------------------------------------------------

def read_audio(path: Path, rate: int = 16000) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
                        "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], capture_output=True)
    if p.returncode != 0:
        raise media.ToolError(p.stderr.decode()[-500:])
    return np.frombuffer(p.stdout, dtype=np.float32)


def frame_db(x: np.ndarray, rate: int = 16000, frame_s: float = 0.05) -> np.ndarray:
    n = int(rate * frame_s)
    usable = len(x) // n * n
    if usable == 0:
        return np.full(1, -120.0)
    rms = np.sqrt(np.mean(x[:usable].reshape(-1, n) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


# --- voice sample -------------------------------------------------------------------------------

def voice_candidates(segs: list[dict], vocals: Path, music: Path, k: int = 3,
                     min_s: float = 10.0, max_s: float = 20.0) -> list[dict]:
    """Up to k non-overlapping runs of whole sentences (10-20 s, no long pauses) where his voice is
    loudest relative to the music underneath it, cleanest first."""
    v = frame_db(read_audio(vocals))
    m = frame_db(read_audio(music))
    n = max(len(v), len(m))
    v = np.pad(v, (0, n - len(v)), constant_values=-120.0)
    m = np.pad(m, (0, n - len(m)), constant_values=-120.0)
    fps = 20.0
    scored = []
    for i in range(len(segs)):
        for j in range(i, len(segs)):
            span = segs[j]["end"] - segs[i]["start"]
            if span > max_s:
                break
            if j > i and segs[j]["start"] - segs[j - 1]["end"] > 1.5:
                break
            if any(s.get("flags") for s in segs[i:j + 1]) or span < min_s:
                continue
            a, b = int(segs[i]["start"] * fps), int(segs[j]["end"] * fps)
            if b <= a or b > len(v):
                continue
            speech = v[a:b]
            active = speech > (np.max(v) - 35)
            if active.mean() < 0.6:
                continue
            snr = float(np.mean(speech[active]) - np.mean(m[a:b][active]))
            scored.append((snr + 0.2 * min(span, 16.0), i, j))  # prefer a bit longer, up to ~16 s
    picked: list[dict] = []
    for score, i, j in sorted(scored, reverse=True):
        start, end = max(0.0, segs[i]["start"] - 0.15), segs[j]["end"] + 0.25
        if any(start < p["end"] and p["start"] < end for p in picked):
            continue
        picked.append({"start": start, "end": end, "ids": [s["id"] for s in segs[i:j + 1]], "snr_db": round(score, 1)})
        if len(picked) == k:
            break
    return picked


def pick_voice(segs: list[dict], vocals: Path, music: Path, min_s: float = 10.0, max_s: float = 20.0) -> dict | None:
    found = voice_candidates(segs, vocals, music, 1, min_s, max_s)
    return found[0] if found else None


def cut_voice(vocals: Path, start: float, end: float, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    media.ffmpeg("-ss", f"{start:.3f}", "-i", vocals, "-t", f"{end - start:.3f}", "-ac", "1", "-ar", RATE,
                 "-c:a", "pcm_s16le", dst)


# --- comparing text -------------------------------------------------------------------------------

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen " \
        "sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + number_words(n % 100))
    for size, word in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= size:
            rest = n % size
            return number_words(n // size) + " " + word + ("" if rest == 0 else " " + number_words(rest))
    return str(n)


_CYR = dict(zip("абвгдежзийклмнопрстуфхцчшщъьюяыэё",
                ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                 "u", "f", "h", "ts", "ch", "sh", "sht", "a", "", "yu", "ya", "y", "e", "yo"]))
_ABBR = {"mr": "mister", "mrs": "missus", "ms": "miss", "dr": "doctor", "vs": "versus"}


def normalize(text: str) -> list[str]:
    text = "".join(_CYR.get(c, c) for c in text.lower())  # Nemotron sometimes writes names in Cyrillic
    text = text.replace("%", " percent")
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    text = re.sub(r"\d+", lambda m: " " + number_words(int(m.group(0))) + " " if len(m.group(0)) < 10 else m.group(0), text)
    text = re.sub(r"(\w)k\b", r"\1 thousand", text)
    words = re.findall(r"[\w']+", text.replace("-", " "))
    return [_ABBR.get(w, w) for w in words]


def _same(a: str, b: str) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.7


def wer(ref: list[str], hyp: list[str]) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    d = list(range(len(hyp) + 1))
    for i in range(1, len(ref) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(hyp) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (0 if _same(ref[i - 1], hyp[j - 1]) else 1))
            prev, d[j] = d[j], cur
    return d[len(hyp)] / len(ref)


def judge(script: str, heard: str) -> tuple[bool, float, str]:
    ref, hyp = normalize(script), normalize(heard)
    w = wer(ref, hyp)
    joined = difflib.SequenceMatcher(None, "".join(ref), "".join(hyp)).ratio()
    ends = bool(ref) and "".join(hyp).endswith(ref[-1][-3:])
    if joined >= 0.85 and ends:  # same sounds, different word breaks: non-stop / nonstop, sports centre / sportscentre
        return True, min(w, round(1 - joined, 3)), ""
    ending = bool(ref) and any(_same(ref[-1], h) for h in hyp[-2:])
    if not ending:
        return False, w, f"last word '{ref[-1] if ref else ''}' missing"
    if w > 0.3:
        return False, w, f"{round(w * 100)}% words differ"
    return True, w, ""


# --- voicing ----------------------------------------------------------------------------------------------

def voice_all(segs, run_dir: Path, server, settings, profile, lang: str, embedder=None, vp=None) -> list[dict]:
    """Voice every line. With a speaker model (`embedder`) and his voiceprint (`vp`), each line gets up to
    settings["takes_per_line"] good takes and the one that sounds most like him is kept."""
    work = run_dir / "voice"
    for d in ("raw", "trim", "fit"):
        (work / d).mkdir(parents=True, exist_ok=True)
    ref = profile.voice_path
    if ref is None or not ref.exists():
        raise RuntimeError("no voice sample for this channel yet")
    ref_bytes = ref.read_bytes()
    ref_b64 = base64.b64encode(ref_bytes).decode()
    ref_md5 = hashlib.md5(ref_bytes).hexdigest()

    def take_name(s, seed):
        h = hashlib.md5((ref_md5 + s["en"]).encode()).hexdigest()[:8]
        return f"{s['id']:04d}-{h}-s{seed}"

    for s in segs:
        s.pop("take", None)
    seeds = settings["seeds"]
    max_speedup = settings["max_speedup"]
    takes: dict[int, list[dict]] = {s["id"]: [] for s in segs}
    pending = [s for s in segs if s["en"].strip() and s["en"] != "[MISSING]"]
    checks_path = work / "checks.json"  # check results per take, so a redo only checks new takes
    checks = json.loads(checks_path.read_text(encoding="utf-8")) if checks_path.exists() else {}

    def fits(t, s) -> bool:
        return t["dur"] <= float(s["slot"]) * max_speedup + 0.05

    want = max(1, int(settings.get("takes_per_line", 2))) if embedder is not None and vp is not None else 1

    def done(s) -> bool:
        return sum(1 for t in takes[s["id"]] if t["ok"] and fits(t, s)) >= want or \
            (want > 1 and any(t["ok"] and fits(t, s) for t in takes[s["id"]]) and len(takes[s["id"]]) >= want)

    # A line is done once it has a take that passes the check AND fits its slot (after at most
    # max_speedup). Takes vary in pace by about +-20%, so a too-long line gets the next seed too.
    for round_no, seed in enumerate(seeds, start=1):
        if not pending:
            break
        t0 = time.monotonic()
        check_dir = work / f"check-{round_no}"
        if check_dir.exists():
            shutil.rmtree(check_dir)
        check_dir.mkdir()
        new, to_check = 0, []
        for s in pending:
            name = take_name(s, seed)
            raw, trim = work / "raw" / f"{name}.wav", work / "trim" / f"{name}.wav"
            if not raw.exists():
                raw.write_bytes(server.tts(settings["tts_model"], s["en"], lang, ref_b64, seed))
                new += 1
            if name not in checks or not trim.exists():
                media.trim_silence(raw, trim, RATE)
                media.ffmpeg("-i", trim, "-ac", "1", "-ar", 16000, "-c:a", "pcm_s16le", check_dir / f"{name}.wav")
                to_check.append(name)
        print(f"   take {round_no} (seed {seed}): {len(pending)} lines, {new} newly voiced in "
              f"{time.monotonic() - t0:.0f}s, checking {len(to_check)}", file=sys.stderr)
        if to_check:
            nemo = settings["nemo"]
            got = transcribe_folder(nemo, check_dir, work / f"check-{round_no}-out", lang, nemo["check_device"])
            for s in pending:
                name = take_name(s, seed)
                if name in to_check:
                    checks[name] = {"heard": got.get(name, {}).get("text", ""),
                                    "dur": round(media.duration(work / "trim" / f"{name}.wav"), 3)}
            if want > 1:  # how much each new take sounds like him
                from .speaker import embed_files, similarity
                for name, e in zip(to_check, embed_files(embedder, [work / "trim" / f"{x}.wav" for x in to_check])):
                    checks[name]["sim"] = similarity(e, vp)
            checks_path.write_text(json.dumps(checks, ensure_ascii=False), encoding="utf-8")
        still, failed, longer = [], 0, 0
        for s in pending:
            name = take_name(s, seed)
            c = checks[name]
            ok, w, why = judge(s["en"], c["heard"])
            if want > 1 and "sim" not in c:  # checked before similarity was on
                from .speaker import embed_files, similarity
                c["sim"] = checks[name]["sim"] = similarity(embed_files(embedder, [work / "trim" / f"{name}.wav"])[0], vp)
            c = {"ok": ok, "wer": round(w, 3), "why": why, "heard": c["heard"], "dur": c["dur"], "sim": c.get("sim")}
            takes[s["id"]].append({"seed": seed, **c, "file": str(work / "trim" / f"{name}.wav")})
            if not done(s):
                still.append(s)
                failed += not c["ok"]
                longer += c["ok"] and not fits(c, s)
        more = len(still) - failed - longer
        print(f"   take {round_no}: {len(pending) - len(still)} done, {failed} failed the check, "
              f"{longer} too long for their slot" + (f", {more} get another take to compare" if more else ""),
              file=sys.stderr)
        pending = still

    # choose and fit: a passing take that fits, else the shortest passing one, else the closest
    report = []
    for s in segs:
        options = takes.get(s["id"]) or []
        if not options:
            report.append({"id": s["id"], "status": "no audio"})
            continue
        passing = [t for t in options if t["ok"]]
        fitting = [t for t in passing if fits(t, s)]
        if fitting:  # the one that sounds most like him, else the most accurate
            best = max(fitting, key=lambda t: (t.get("sim") or 0.0, -t["wer"]))
        elif passing:
            best = min(passing, key=lambda t: t["dur"])
        else:
            best = min(options, key=lambda t: (t["wer"], t["dur"]))
        src = Path(best["file"])
        fit = work / "fit" / f"{s['id']:04d}.wav"
        d = best["dur"]
        slot = float(s["slot"])
        action = "fits"
        if d > slot:
            factor = min(d / slot, max_speedup)
            media.tempo(src, fit, factor, RATE)
            after = media.duration(fit)
            action = f"sped up {round((factor - 1) * 100)}%"
            if after > slot + 0.05:
                action += f", OVER by {after - slot:.1f}s"
        else:
            shutil.copyfile(src, fit)
        s["take"] = {"seed": best["seed"], "ok": best["ok"], "wer": best["wer"], "why": best["why"],
                     "heard": best["heard"], "tries": len(options), "fit": action, "spoken": d,
                     "sim": best.get("sim"),
                     "seconds": round(media.duration(fit), 3), "file": str(fit)}
        report.append({"id": s["id"], **s["take"]})
    return report
