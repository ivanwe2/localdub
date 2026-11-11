"""Shorts: find moments worth a Short from the picture and the words; you pick (or --auto); it renders them.

    localdub shorts NAME [--lang dub|original] [--auto N] [--redo select|vision]

--lang dub       the dubbed video, with captions in the dub's language (default)
--lang original  the original video and audio, captions in the creator's own language, timed by
                 Nemotron's word timestamps; works for whatever language the channel speaks

1. motion   how much the picture changes, and the shot cuts (ffmpeg + numpy, about a minute)
2. vision   Gemma looks at the video: 4 frames per image, one line per frame and a 1-5 rating
            of how interesting it is to watch (cached per image, so it resumes)
            -> both are shared by the two languages
3. select   Gemma reads the speech sentence by sentence with what's on screen and proposes
            candidates of 25-60 s that stand alone, hook early and end on a payoff, strongest first
4. you      mark the ones you want in shorts/<lang>/candidates.txt  (or --auto N takes the top N)
5. render   1080x1920: the full frame over a blurred copy of itself, big burned-in captions
"""

from __future__ import annotations

import base64
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from . import media
from .llm import LLMError

VISION_PROMPT = """These are {n} frames from a video, at {times} (left to right, then top to bottom).
For each frame write one short line on what is shown: the place, people, what they are doing, notable
objects or text on screen. Then rate how interesting this stretch is to WATCH, from 1 (a static talking
head, nothing happening) to 5 (striking, funny, surprising or unusual to look at).
Reply with JSON only, one line per frame, like: {{"frames": ["a man in a kitchen opens a fridge", "close-up of
the empty fridge"], "interest": 3}}"""

SELECT_PROMPT = """You pick moments from a video to cut into YouTube Shorts (vertical, {min_s}-{max_s} seconds).
You get the video's speech, in {language}, as numbered sentences: number, start time, length, then in
brackets v = how interesting the picture is (1-5), c = shot cuts, m = movement (0 still, 1 some, 2 lots),
then the sentence, and after "|" what is on screen.

Choose {n} candidates. Each is a run of consecutive sentences, {min_s}-{max_s} seconds in total, that:
- makes sense on its own, without the rest of the video;
- hooks in the first 3 seconds (a question, a surprising claim, a striking sight);
- ends on a payoff (a punchline, a reveal, a verdict, a strong reaction), not mid-thought;
- has something worth looking at.
Candidates must not overlap; prefer variety across the video. List them from strongest to weakest.
Write the title in {language}; write hook, why and visual in English.
Reply with JSON only: {{"candidates": [{{"start": first sentence number, "end": last sentence number,
"title": "short catchy title", "hook": "what grabs attention at the start", "why": "why it works as a
Short", "visual": "what we see"}}]}}"""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --- 1. motion ----------------------------------------------------------------------------------------

def motion(video: Path, seconds: float) -> dict:
    """{"per_second": [0..1 change], "cuts": [times]} from a tiny grayscale copy at 4 fps."""
    w, h, fps = 64, 36, 4
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(video),
                        "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True)
    if p.returncode != 0:
        raise media.ToolError(p.stderr.decode()[-500:])
    frames = np.frombuffer(p.stdout, dtype=np.uint8)
    frames = frames[: len(frames) // (w * h) * (w * h)].reshape(-1, h * w).astype(np.float32)
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=1) / 255.0 if len(frames) > 1 else np.zeros(1)
    cuts = [round((i + 1) / fps, 2) for i in np.where(diff > 0.18)[0]]
    n = max(1, math.ceil(seconds))
    per = np.zeros(n)
    for i, d in enumerate(diff):
        sec = min(n - 1, int((i + 1) / fps))
        per[sec] = max(per[sec], d)
    return {"per_second": [round(float(x), 4) for x in per], "cuts": cuts}


# --- 2. vision ----------------------------------------------------------------------------------------

def make_sheets(video: Path, out: Path, every: float, seconds: float) -> list[dict]:
    """2x2 contact sheets of frames every `every` seconds: [{"file", "times"}]."""
    out.mkdir(parents=True, exist_ok=True)
    if not any(out.glob("sheet-*.jpg")):
        media.ffmpeg("-i", video, "-vf",
                     f"fps=1/{every},scale=480:270:force_original_aspect_ratio=decrease,"
                     f"pad=480:270:(ow-iw)/2:(oh-ih)/2,tile=2x2", "-q:v", "3", out / "sheet-%04d.jpg")
    sheets = []
    for k, f in enumerate(sorted(out.glob("sheet-*.jpg"))):
        times = [(4 * k + i) * every for i in range(4) if (4 * k + i) * every < seconds]
        if times:
            sheets.append({"file": str(f), "times": times})
    return sheets


def look(sheets: list[dict], cache_path: Path, server, model: str) -> dict:
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [s for s in sheets if s["file"] not in cache]
    if todo:
        log(f"   {model} looks at {len(todo)} images of 4 frames (about {len(todo) * 3 // 60 + 1} min)")
    t0 = time.monotonic()
    for i, s in enumerate(todo, start=1):
        img = base64.b64encode(Path(s["file"]).read_bytes()).decode()
        prompt = VISION_PROMPT.format(n=len(s["times"]), times=", ".join(media.mmss(t)[:5] for t in s["times"]))
        user = [{"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]
        try:
            reply = server.chat_json(model, "You describe video frames briefly and factually.", user, "none", 800,
                                     f"frames {i}/{len(todo)}", quiet=i % 10 != 0 and i != len(todo))
        except LLMError as e:  # one unreadable answer costs one image's description, not the run
            log(f"   frames {i}/{len(todo)}: skipped ({e})")
            reply = {}
        frames = [str(x).strip() for x in reply.get("frames", [])][: len(s["times"])]
        try:
            interest = max(1, min(5, int(reply.get("interest", 1))))
        except (TypeError, ValueError):
            interest = 1
        cache[s["file"]] = {"times": s["times"], "frames": frames, "interest": interest}
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    if todo:
        log(f"   looked at {len(todo)} images in {time.monotonic() - t0:.0f}s")
    return cache


# --- 3. select ----------------------------------------------------------------------------------------

def span(s: dict, lang: str) -> tuple[float, float]:
    """Where the line is heard: the dub take where it was placed, or his own words in the original."""
    if lang == "original":
        words = s.get("words") or []
        return (float(words[0]["start"]), float(words[-1]["end"])) if words else (float(s["start"]), float(s["end"]))
    begin = float(s.get("placed", s["start"]))
    end = begin + float(s["take"]["seconds"]) if s.get("take") else float(s["end"])
    return begin, max(end, float(s["end"]))


def text(s: dict, lang: str) -> str:
    return (s.get("bg", "") if lang == "original" else s.get("en", "")).strip()


def timeline(segs: list[dict], vision: dict, mot: dict, lang: str) -> str:
    shots = sorted(((t, f, v["interest"]) for v in vision.values() for t, f in zip(v["times"], v["frames"])))
    per = mot["per_second"]
    lines, last_desc = [], None
    for k, s in enumerate(segs):
        a, b = span(s, lang)
        nxt = float(segs[k + 1]["start"]) if k + 1 < len(segs) else b + 4
        in_span = [x for x in shots if a - 2 <= x[0] < max(nxt, a + 2)]
        v = max((x[2] for x in in_span), default=1)
        cuts = sum(1 for c in mot["cuts"] if a <= c < nxt)
        window = per[int(a):max(int(a) + 1, int(nxt))] or [0]
        move = float(np.mean(window))
        m = 2 if move > 0.06 else 1 if move > 0.02 else 0
        descs = []
        for x in in_span:
            if x[1] and x[1] != last_desc:
                descs.append(x[1])
                last_desc = x[1]
        line = f"{s['id']} {media.mmss(a)[:5]} {b - a:.0f}s [v{v} c{cuts} m{m}] {text(s, lang) or '(no speech)'}"
        if descs:
            line += " | " + "; ".join(descs[:2])
        lines.append(line)
    return "\n".join(lines)


def select(segs, vision, mot, server, settings, lang: str, language: str) -> list[dict]:
    sh = settings["shorts"]
    system = SELECT_PROMPT.format(n=sh["count"], min_s=sh["min_seconds"], max_s=sh["max_seconds"], language=language)
    reply = server.chat_json(settings["text_model"], system, timeline(segs, vision, mot, lang), sh["select_effort"],
                             12000, f"choosing {language} Shorts")
    by_id = {s["id"]: s for s in segs}
    order = [s["id"] for s in segs]
    out, used = [], set()
    for c in reply.get("candidates", []):  # kept in Gemma's order: strongest first
        try:
            a, b = int(c["start"]), int(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if a not in by_id or b not in by_id or order.index(a) > order.index(b):
            continue
        ids = order[order.index(a):order.index(b) + 1]
        if used & set(ids):
            continue
        start, end = span(by_id[a], lang)[0], span(by_id[b], lang)[1]
        if not sh["min_seconds"] * 0.7 <= end - start <= sh["max_seconds"] * 1.4:
            continue
        used |= set(ids)
        out.append({"start": a, "end": b, "from": round(start, 2), "to": round(end, 2),
                    "title": str(c.get("title", "")).strip().replace('"', "'"), "hook": str(c.get("hook", "")).strip(),
                    "why": str(c.get("why", "")).strip(), "visual": str(c.get("visual", "")).strip()})
    return out


def write_candidates(path: Path, rerun: str, cands: list[dict], segs: list[dict], lang: str, auto: int = 0) -> None:
    by_id = {s["id"]: s for s in segs}
    lines = [
        "status: approved" if auto else "status: pending",
        "# Shorts candidates, strongest first. Mark the ones to render with an x, like [x] (3 is a good number),",
        f"# set the first line to 'status: approved', save, and run '{rerun}' again.",
        "# To cut one differently, change its 'sentences A-B' (numbers as in check.txt); you can edit the title too.",
        f"# (--auto {auto}: the top {auto} were picked automatically)" if auto else "",
        "",
    ]
    for k, c in enumerate(cands, start=1):
        mark = "x" if k <= auto else " "
        lines += [
            f"[{mark}] {k}   {media.mmss(c['from'])[:5]}-{media.mmss(c['to'])[:5]} ({c['to'] - c['from']:.0f}s)   "
            f"sentences {c['start']}-{c['end']}   \"{c['title']}\"",
            f"    hook:   {c['hook']}",
            f"    why:    {c['why']}",
            f"    screen: {c['visual']}",
            f"    starts: {text(by_id[c['start']], lang)}",
            f"    ends:   {text(by_id[c['end']], lang)}",
            "",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_PICK = re.compile(r"^\[(.?)\]\s*(\d+)\s.*?sentences\s+(\d+)\s*-\s*(\d+)(?:.*?\"(.*)\")?")


def read_candidates(path: Path) -> tuple[str, list[dict]]:
    status, picked = "pending", []
    for raw in path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.lower().startswith("status:"):
            status = line.split(":", 1)[1].strip().lower()
            continue
        m = _PICK.match(line)
        if m and m.group(1).strip().lower() in ("x", "v", "✓", "y", "*"):
            picked.append({"n": int(m.group(2)), "start": int(m.group(3)), "end": int(m.group(4)),
                           "title": (m.group(5) or "").strip()})
    return status, picked


# --- 5. render ----------------------------------------------------------------------------------------

def _ass_time(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _ass_text(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _chunks(words: list[str], max_chars: int = 18) -> list[list[int]]:
    """Indices of 1-3 words per caption (one line at this size), as Shorts captions usually are;
    a new caption after punctuation, or before a word that would make the line too long."""
    out, cur = [], []
    for i, w in enumerate(words):
        if cur and (len(cur) == 3 or len(" ".join(words[j] for j in cur + [i])) > max_chars):
            out.append(cur)
            cur = []
        cur.append(i)
        if re.search(r"[.!?,;:…]$", w) and not re.fullmatch(r"(Mr|Mrs|Ms|Dr|St|г|гр|ул)\.", w):
            out.append(cur)
            cur = []
    return out + ([cur] if cur else [])


def captions(segs: list[dict], offset: float, length: float, title: str, lang: str) -> str:
    head = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        "Style: Cap,DejaVu Sans,84,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,"
        "70,70,500,1",
        "Style: Title,DejaVu Sans,62,&H0000E1FF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,8,"
        "70,70,330,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    caps = []

    def add(a, b, words):
        a, b = a - offset, b - offset
        if b > 0 and a < length:
            caps.append([max(0.0, a), min(b, length), " ".join(words)])

    for s in segs:
        if lang == "original":  # his own words, each with its real time
            ws = [w for w in s.get("words") or [] if str(w["word"]).strip()]
            groups = _chunks([w["word"] for w in ws])
            for g, nxt in zip(groups, groups[1:] + [None]):
                end = ws[nxt[0]]["start"] if nxt else ws[g[-1]]["end"] + 0.3
                add(ws[g[0]]["start"], end, [ws[i]["word"] for i in g])
            continue
        words = text(s, lang).split()  # the dub: no word times, so share the take's length by characters
        if not words or not s.get("take"):
            continue
        a = span(s, lang)[0]
        dur = float(s["take"]["seconds"])
        groups = [[words[i] for i in g] for g in _chunks(words)]
        total = sum(len(" ".join(g)) + 1 for g in groups)
        t = a
        for g in groups:
            d = dur * (len(" ".join(g)) + 1) / total
            add(t, t + d, g)
            t += d
    caps.sort()
    for c, nxt in zip(caps, caps[1:]):  # one caption at a time: each ends where the next begins
        c[1] = min(c[1], nxt[0])
    ev = [f"Dialogue: 0,{_ass_time(0)},{_ass_time(length)},Title,,0,0,0,,{_ass_text(title)}"] if title else []
    ev += [f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Cap,,0,0,0,,{_ass_text(s)}" for a, b, s in caps if b - a > 0.05]
    return "\n".join(head + ev) + "\n"


def render(video: Path, segs: list[dict], pick: dict, out: Path, work: Path, show_title: bool, lang: str) -> Path:
    order = [s["id"] for s in segs]
    by_id = {s["id"]: s for s in segs}
    if pick["start"] not in by_id or pick["end"] not in by_id or order.index(pick["start"]) > order.index(pick["end"]):
        raise RuntimeError(f"candidate {pick['n']}: sentences {pick['start']}-{pick['end']} don't exist in this run")
    part = [by_id[i] for i in order[order.index(pick["start"]):order.index(pick["end"]) + 1]]
    start = max(0.0, span(part[0], lang)[0] - 0.25)
    end = span(part[-1], lang)[1] + 0.6
    length = end - start
    ass = work / f"short-{pick['n']}.ass"
    ass.write_text(captions(part, start, length, pick["title"] if show_title else "", lang), encoding="utf-8")
    graph = ("[0:v]split[a][b];"
             "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2,"
             "eq=brightness=-0.07[bg];"
             "[b]scale=1080:-2[fg];"
             f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass=filename={ass}[v]")
    media.ffmpeg("-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", video, "-filter_complex", graph,
                 "-map", "[v]", "-map", "0:a:0", "-af", f"afade=t=in:d=0.15,afade=t=out:st={length - 0.35:.2f}:d=0.35",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out)
    return out


def slug(s: str) -> str:
    s = re.sub(r"[^\w]+", "-", s.lower()).strip("-")
    return s[:40] or "short"
