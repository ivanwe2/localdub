"""The pipeline: one command, resumable, and it stops twice for your review.

    localdub run VIDEO --channel NAME --from bg --to en [--start 1:00 --duration 2:30]
                                   a new channel needs its languages; later runs just --channel NAME
    localdub run NAME              continue a run (after a review, or after an error)
    localdub voice NAME            compare voice samples from a run, then --pick one for the channel
    localdub channel NAME          a channel's settings (--speaker man|woman|none to change it)
    localdub languages             the languages localdub knows
    localdub check                 is everything it needs in place?

Everything a run makes is kept in ~/dub/runs/<name>/, so rerunning continues where it stopped.
"""

from __future__ import annotations

import argparse
import datetime as dt
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

from . import events, languages, media, review
from .segments import upgrade, upgrade_all
from .llm import LLMError, Models
from .mix import mix, mux, place, subtitles
from .settings import Profile, home, load_settings
from .text import (Speller, _is_name, build_segments, core, replace_words, shorten_to, spellcheck, text_of,
                   translate)
from .voice import RATE, cut_voice, seeds_tried, voice_all, voice_candidates

WAIT = "wait"


def iso3(code: str) -> str:
    """The ISO 639-2 code for a media file's language tag; "und" (undetermined) if unknown."""
    return languages.LANGUAGES[code].iso3 if code in languages.LANGUAGES else "und"


def log(msg: str = "") -> None:
    print(msg, file=sys.stderr, flush=True)


def fail(msg: str, **fields) -> int:
    """Report an error to people (stderr) and to the result event; returns exit code 1."""
    log(f"error: {msg}")
    events.note(error=msg, **fields)
    return 1


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


class Run:
    def __init__(self, name: str):
        self.name = name
        self.dir = home() / "runs" / name
        self.state_path = self.dir / "state.json"
        self.state = read_json(self.state_path) if self.state_path.exists() else {}

    def p(self, name: str) -> Path:
        return self.dir / name

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json(self.state_path, self.state)

    @property
    def segs(self) -> list[dict]:
        return upgrade_all(read_json(self.p("segments.json")))  # older runs: "bg"/"en" keys

    @segs.setter
    def segs(self, value: list[dict]) -> None:
        write_json(self.p("segments.json"), value)


class Ctx:
    def __init__(self, settings, profile, server, no_review, rerun):
        self.settings, self.profile, self.server = settings, profile, server
        self.no_review, self.rerun = no_review, rerun


def waiting(ctx: Ctx, kind: str, title: str, path: Path, extra: str = "") -> str:
    events.note(waiting_for={"kind": kind, "file": str(path)})
    log(f"\n== {title}")
    log(f"   {path}")
    if extra:
        log(f"   {extra}")
    log("   Edit it, change its first line to 'status: approved', save, then continue with:")
    log(f"   {ctx.rerun}")
    return WAIT


# --- stages ----------------------------------------------------------------------------------------

def stage_prep(run: Run, ctx: Ctx):
    src = Path(run.state["source"])
    if not media.has_audio(src):
        raise media.ToolError(f"{src} has no audio track")
    start, length = run.state.get("start"), run.state.get("length")
    video = src
    if start is not None or length is not None:
        video = run.p("video.mp4")
        log("   cutting the excerpt (the video is re-encoded so the cut is exact)")
        cut = ["-ss", f"{start or 0:.3f}", "-i", src] + (["-t", f"{length:.3f}"] if length else [])
        media.ffmpeg(*cut, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                     "-c:a", "aac", "-b:a", "192k", video)
    media.ffmpeg("-i", video, "-vn", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", run.p("audio44.wav"))
    media.ffmpeg("-i", run.p("audio44.wav"), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", run.p("audio16.wav"))
    run.state["video"] = str(video)
    run.state["seconds"] = media.duration(run.p("audio44.wav"))
    log(f"   {media.mmss(run.state['seconds'])} of audio")


def local(ctx: Ctx, tool: str) -> bool:
    """Does this tool ("nemo", "demucs") run on this machine instead of behind the model server?"""
    backend = ctx.settings[tool]["backend"]
    if backend not in ("http", "local"):
        raise RuntimeError(f"{tool}.backend is {backend!r}; use \"http\" or \"local\"")
    return backend == "local"


def stage_separate(run: Run, ctx: Ctx):
    if not local(ctx, "demucs"):
        log(f"   {ctx.settings['separation_model']} on the model server (roughly 1 minute per 10 minutes of video)")
        ctx.server.separate(run.p("audio44.wav"), run.p("vocals.wav"),
                            run.p("music.wav"))
        return
    ctx.server.unload_all()
    d = ctx.settings["demucs"]
    out = run.p("stems")
    shutil.rmtree(out, ignore_errors=True)
    log(f"   Demucs {d['model']} on {d['device']} (roughly 1 minute per 10 minutes of video)")
    media.run(d["python"], "-m", "demucs", "-n", d["model"], "--two-stems=vocals", "-d", d["device"],
              "-o", out, run.p("audio44.wav"))
    got = out / d["model"] / "audio44"
    shutil.move(got / "vocals.wav", run.p("vocals.wav"))
    shutil.move(got / "no_vocals.wav", run.p("music.wav"))
    shutil.rmtree(out)


def stage_transcribe(run: Run, ctx: Ctx):
    from .asr import transcribe_folder
    if local(ctx, "nemo"):
        ctx.server.unload_all()
    nemo = ctx.settings["nemo"]
    total = run.state["seconds"]
    limit = nemo["max_chunk_minutes"] * 60
    cuts = [0.0]
    if total > limit:  # one request is capped (29 min on a 16 GB card): split at pauses in the speech
        pauses = [(a + b) / 2 for a, b in media.silences(run.p("vocals.wav"), -35.0, 0.3)]
        while total - cuts[-1] > limit:
            lo, hi = cuts[-1] + limit * 0.6, cuts[-1] + limit
            near = [x for x in pauses if lo < x <= hi]
            cuts.append(max(near) if near else hi)
    cuts.append(total)
    work = run.p("asr")
    shutil.rmtree(work, ignore_errors=True)
    (work / "in").mkdir(parents=True)
    for k, (a, b) in enumerate(zip(cuts, cuts[1:])):
        media.ffmpeg("-ss", f"{a:.3f}", "-i", run.p("audio16.wav"), "-t", f"{b - a:.3f}",
                     "-c:a", "pcm_s16le", work / "in" / f"part{k:02d}.wav")
    where = nemo["device"] if local(ctx, "nemo") else "the model server"
    log(f"   Nemotron on {where}, {len(cuts) - 1} part(s)")
    got = transcribe_folder(ctx.settings, ctx.server, work / "in", work / "out", ctx.profile["source_lang"],
                            nemo["device"])
    words = []
    for k, offset in enumerate(cuts[:-1]):
        data = got.get(f"part{k:02d}")
        if data is None:
            raise media.ToolError(f"nemo-speech wrote no result for part {k}")
        for w in data.get("words", []):
            text = str(w.get("word", "")).strip()
            if text:
                words.append({"word": text, "start": round(w["start"] + offset, 3), "end": round(w["end"] + offset, 3)})
    if not words:
        raise media.ToolError("Nemotron heard no speech")
    write_json(run.p("transcript.json"), {"parts": cuts, "words": words})
    log(f"   {len(words)} words")


def stage_spelling(run: Run, ctx: Ctx):
    s = ctx.settings
    words = read_json(run.p("transcript.json"))["words"]
    segs = build_segments(words, s["max_segment_seconds"], s["chars_per_second"])
    report = spellcheck(segs, ctx.server, s, ctx.profile)
    run.segs = segs
    write_json(run.p("spelled.json"), segs)  # before your edits, so review 1 can be redone
    write_json(run.p("spelling.json"), {str(k): v for k, v in report.items()})
    fixes = sum(len(r["fixes"]) for r in report.values())
    learned = sum(len(r["learned"]) for r in report.values())
    unknown = sum(len(r["unknown"]) for r in report.values())
    names = sum(len(r["review"]) for r in report.values())
    log(f"   {len(segs)} sentences; {learned} words fixed as learned before, {fixes} new fixes, "
        f"{unknown} words unknown to the dictionary, {names} name suggestions")


def _changed_pairs(old: list[str], new: list[str]) -> list[tuple[int, int]]:
    """Word positions (old, new) that were replaced one for one."""
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "replace" and i2 - i1 == j2 - j1:
            out += list(zip(range(i1, i2), range(j1, j2)))
    return out


def learn_transcript(parsed: dict, segs: list[dict], report: dict, profile: Profile) -> dict:
    """Apply your edits of the transcript and remember what they teach.

    corrections   a misspelled word you fixed (or an automatic fix you kept) is fixed the same way next time;
                  a name you corrected mid-sentence too
    names         capitalised words mid-sentence that you wrote or left as they were
    accepted      words the dictionary didn't know that you left as they were
    An automatic fix you undid is forgotten and the original word accepted.
    """
    by_id = {s["id"]: s for s in segs}
    edits = {sid: t for sid, t in parsed["src"].items() if sid in by_id and t.split()}
    corr, names = profile["corrections"], profile["names"]
    accepted = set(profile["accepted_words"])
    base = Speller(profile["hunspell_dict"], [])
    unknown = base.unknown({core(w) for sid, t in edits.items() for w in t.split() + by_id[sid]["src"].split()})
    n = {"edited": 0, "corrections": 0, "names": 0, "accepted": 0}

    def add_correction(a, b):
        pa, pb = a.split("-"), b.split("-")
        pairs = [(x, y) for x, y in zip(pa, pb) if x != y] if len(pa) == len(pb) > 1 else [(a, b)]
        for x, y in pairs:  # 'най-ефтината' -> 'най-евтината' is learned as 'ефтината' -> 'евтината'
            if corr.get(x) != y:
                corr[x] = y
                n["corrections"] += 1

    def add_name(w):
        if w not in names:
            names[w] = ""
            n["names"] += 1

    for sid, text in edits.items():
        s, r = by_id[sid], report.get(sid, {})
        old, new = s["src"].split(), text.split()
        auto = {(core(b), core(a)) for a, b in r.get("fixes", []) + r.get("learned", [])}  # (fixed, original)
        for i, j in _changed_pairs(old, new):
            a, b = core(old[i]), core(new[j])
            if not a or not b or a == b:
                continue
            if (a, b) in auto:  # you undid an automatic fix: the original was right
                if corr.get(b) == a:
                    del corr[b]
                if Speller.is_misspelled(b, unknown) and b not in accepted:
                    accepted.add(b)
                    n["accepted"] += 1
                continue
            if Speller.is_misspelled(a, unknown) or (_is_name(old, i) and _is_name(new, j)):
                add_correction(a, b)
            if _is_name(new, j):
                add_name(b)
        final = [core(w) for w in new]
        for a, b in r.get("fixes", []):  # automatic fixes you kept become fixed rules
            ca, cb = core(a), core(b)
            if ca != cb and cb in final and ca not in final:
                add_correction(ca, cb)
        for w in r.get("unknown", []):  # unknown words you left as they were
            if w in final:
                if _is_name(new, final.index(w)):
                    add_name(w)
                elif w not in accepted:
                    accepted.add(w)
                    n["accepted"] += 1
        if text != s["src"]:
            replace_words(s, text)
            s["src"] = text_of(s["words"])
            n["edited"] += 1
    profile["accepted_words"] = sorted(accepted)
    return n


def run_voiceprint(run: Run, embedder, segs: list[dict]):
    """The speaker's fingerprint from this video's separated vocals (cached in the run)."""
    from .speaker import voiceprint
    path = run.p("voiceprint.npy")
    if path.exists():
        return np.load(path)
    vp = voiceprint(embedder, segs, run.p("vocals.wav"))
    if vp is not None:
        np.save(path, vp)
    return vp


# --- the voice sample: candidates to listen to, compare and pick ----------------------------------

CANDIDATES = "voice-candidates.json"


def load_candidates(run: Run) -> dict | None:
    """The run's voice-candidates.json, as version 2 (see make_candidates). A version 1 file (a list,
    sorted by score and numbered by position in review 1) is read as one, numbered the same way."""
    path = run.p(CANDIDATES)
    if not path.exists():
        return None
    data = read_json(path)
    if isinstance(data, list):
        data = {"version": 2, "lines": [], "candidates": [{**c, "n": n, "tests": []} for n, c in enumerate(data, start=1)],
                "current": None, "picked": None}
    return data


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def clone_tests(run: Run, ctx: Ctx, sample: Path, lines: list[str]) -> list[str]:
    """The test sentences cloned from this voice sample, as files in the run's voice-candidates/
    (paths relative to the run). They are named after the sample's and the sentences' checksums, so
    asking again for the same sample costs nothing."""
    import base64
    lang = ctx.profile["target_lang"]
    key = hashlib.md5((_md5(sample) + lang + "\n".join(lines)).encode()).hexdigest()[:10]
    sample_b64 = None
    out = []
    for i, line in enumerate(lines, start=1):
        rel = f"voice-candidates/test-{key}-{i}.wav"
        trim = run.p(rel)
        if not trim.exists():
            sample_b64 = sample_b64 or base64.b64encode(sample.read_bytes()).decode()
            raw = trim.with_suffix(".raw.wav")
            raw.write_bytes(ctx.server.tts(line, lang, sample_b64, 42,
                                           label=f"test sentence {i} from {sample.stem}"))
            media.trim_silence(raw, trim, RATE)
            raw.unlink()
        out.append(rel)
    return out


def make_candidates(run: Run, ctx: Ctx, segs: list[dict], current: bool) -> dict:
    """Up to 3 voice-sample candidates from the run's separated speech (the cleanest stretches of
    10-20 s), and with `current` the channel's voice now. Each gets the dub language's test sentences
    cloned from it; with the speaker model installed, each is scored by how much its clones sound
    like the speaker's real voice, and the list is sorted by that. Written to voice-candidates.json:
      {"version": 2, "lang", "lines": [test sentences], "scored": bool, "picked": null,
       "candidates": [{"n", "start", "end", "ids", "snr_db", "path", "tests": [paths], "sim"}],
       "current": {"from", "path", "tests", "sim"} or null}
    `n` stays with its stretch whatever the order (review 1's and --pick's numbers); paths are
    relative to the run."""
    from .speaker import embed_files, get_embedder, similarity
    target = languages.LANGUAGES.get(ctx.profile["target_lang"])
    lines = list(target.calibration) if target else []
    run.p("voice-candidates").mkdir(exist_ok=True)
    found = voice_candidates(segs, run.p("vocals.wav"), run.p("music.wav"), k=3)
    cands = []
    for n, c in enumerate(found, start=1):
        rel = f"voice-candidates/candidate-{n}.wav"
        cut_voice(run.p("vocals.wav"), c["start"], c["end"], run.p(rel))
        cands.append({**c, "n": n, "path": rel, "sim": None})
    cur = None
    voice = ctx.profile.voice_path
    if current and voice is not None and voice.exists():
        rel = f"voice-candidates/current-{_md5(voice)[:10]}.wav"
        if not run.p(rel).exists():
            shutil.copyfile(voice, run.p(rel))
        cur = {"from": ctx.profile.data.get("voice_from") or "", "path": rel, "sim": None}
    samples = cands + ([cur] if cur else [])
    if lines and samples:
        log(f"   cloning {len(lines)} test sentences from each of {len(samples)} voice samples to compare them")
    for s in samples:
        s["tests"] = clone_tests(run, ctx, run.p(s["path"]), lines) if lines else []
    embedder = get_embedder(ctx.settings) if lines and samples else None
    vp = run_voiceprint(run, embedder, segs) if embedder is not None else None
    if vp is not None:
        for s in samples:
            sims = [similarity(e, vp) for e in embed_files(embedder, [run.p(t) for t in s["tests"]])]
            s["sim"] = round(float(np.mean(sims)), 3)
        for c in cands:
            log(f"   candidate {c['n']} ({media.mmss(c['start'])}-{media.mmss(c['end'])}): "
                f"sounds like the speaker {c['sim']:.2f}")
        if cur:
            log(f"   the channel's voice now: sounds like the speaker {cur['sim']:.2f}")
        cands.sort(key=lambda c: -c["sim"])
    data = {"version": 2, "lang": ctx.profile["target_lang"], "lines": lines, "scored": vp is not None,
            "candidates": cands, "current": cur, "picked": None}
    write_json(run.p(CANDIDATES), data)
    return data


def set_voice(run: Run, profile: Profile, choice: str) -> str:
    """Makes `choice` the channel's voice sample and says where it came from: "auto" (the best
    candidate), a candidate's number, a stretch of this video ("01:20-01:38") or a WAV file's path.
    The run's voice-candidates.json records the pick."""
    dst = profile.dir / "voice.wav"
    profile.dir.mkdir(parents=True, exist_ok=True)
    choice = choice.strip()
    data = load_candidates(run)
    cands = data["candidates"] if data else []
    if choice.lower() in ("", "auto") or choice.isdigit():
        if choice.isdigit():
            cand = next((c for c in cands if c["n"] == int(choice)), None)
            if cand is None:
                raise RuntimeError(f"there is no voice candidate {choice} (there are: "
                                   f"{', '.join(str(c['n']) for c in cands) or 'none'})")
        else:
            cand = cands[0] if cands else None
        sample = run.p(cand["path"]) if cand else run.p("voice-sample.wav")
        if not choice or not sample.exists():
            raise RuntimeError("a voice sample is needed: a candidate's number, a stretch like "
                               "'voice: 01:20-01:38' or a WAV file's path")
        shutil.copyfile(sample, dst)
        if cand:
            sim = f", sounds like the speaker {cand['sim']:.2f}" if cand.get("sim") is not None else ""
            source = f"{run.name}, candidate {cand['n']} ({media.mmss(cand['start'])}-{media.mmss(cand['end'])}{sim})"
        else:
            source = f"{run.name}, picked automatically"
        picked = cand["n"] if cand else "auto"
    elif choice.lower().endswith(".wav"):
        f = Path(choice).expanduser()
        if not f.exists():
            raise RuntimeError(f"voice file {f} not found")
        media.ffmpeg("-i", f, "-ac", "1", "-ar", RATE, "-c:a", "pcm_s16le", dst)
        source = picked = str(f)
    else:
        try:
            a, b = (media.parse_mmss(x) for x in choice.split("-", 1))
        except ValueError:
            raise RuntimeError(f"can't read the voice choice '{choice}' (expected e.g. 01:20-01:38)") from None
        if not 5 <= b - a <= 30:
            raise RuntimeError("the voice stretch should be 5-30 seconds long (10-20 is best)")
        cut_voice(run.p("vocals.wav"), a, b, dst)
        source = f"{run.name} {choice}"
        picked = choice
    profile["voice_ref"] = "voice.wav"
    profile["voice_from"] = source
    mark_picked(run, data, picked)
    return source


def mark_picked(run: Run, data: dict | None, picked) -> None:
    """Records the pick in the run's voice-candidates.json, so it no longer waits for one."""
    if data is not None:
        data["picked"] = picked
        write_json(run.p(CANDIDATES), data)


def stage_review1(run: Run, ctx: Ctx):
    path = run.p("review-1-transcript.txt")
    segs = upgrade_all(read_json(run.p("spelled.json")))
    report = {int(k): v for k, v in read_json(run.p("spelling.json")).items()}
    profile = ctx.profile
    shorts_only = run.state.get("mode") == "shorts"  # no dub: no voice sample to choose
    has_voice = shorts_only or (profile.voice_path is not None and profile.voice_path.exists())
    if not path.exists():
        voice = None
        if not has_voice:
            data = make_candidates(run, ctx, segs, current=False)
            if data["candidates"]:  # the review shows where they are
                voice = {"candidates": [{**c, "path": str(run.p(c["path"]))} for c in data["candidates"]]}
            else:
                voice = {"path": None}
        count = review.write_transcript_review(path, segs, report, voice, profile["source_lang"], captions=shorts_only)
        if ctx.no_review:
            if not has_voice:
                log(f"   voice sample: {set_voice(run, profile, 'auto')}")
                profile.save()
            log("   review skipped (--no-review); nothing is learned from this run")
            return None
        if count == 0 and voice is None:
            log("   nothing doubtful in the transcript: no review needed")
            return None
        what = f"{count} lines to check" + (", and the voice sample to listen to" if voice else "")
        title = "The transcript review is ready (it becomes the captions)" if shorts_only else \
            "Review 1 of 2 is ready (the transcript)"
        return waiting(ctx, "review1", title, path, what + ".")
    parsed = review.parse(path, profile["source_lang"], profile["target_lang"])
    if parsed["status"] != "approved":
        return waiting(ctx, "review1", "The transcript review is still waiting for you" if shorts_only else
                       "Review 1 of 2 is still waiting for you", path)
    n = learn_transcript(parsed, segs, report, profile)
    if not shorts_only:
        data = load_candidates(run)
        picked_meanwhile = data is not None and data.get("picked") is not None and has_voice  # localdub voice --pick
        if picked_meanwhile and (parsed["voice"] or "auto").lower() == "auto":
            log(f"   voice sample: {profile.data.get('voice_from') or 'as picked'} (picked with localdub voice)")
        elif parsed["voice"] is not None or not has_voice:
            log(f"   voice sample: {set_voice(run, profile, parsed['voice'] or 'auto')}")
    profile.save()
    run.segs = segs
    log(f"   {n['edited']} lines edited; learned {n['corrections']} corrections, {n['names']} names, "
        f"{n['accepted']} accepted words")


def stage_translate(run: Run, ctx: Ctx):
    segs = run.segs
    partial = run.p("translate-partial.json")  # lines done so far, kept if the stage is interrupted
    done = {k: upgrade(v) for k, v in read_json(partial).items()} if partial.exists() else {}
    for s in segs:
        for k in ("tgt", "model_tgt", "take", "shortened_from"):
            s.pop(k, None)
        d = done.get(str(s["id"]))
        if d and d["src"] == s["src"]:
            s["tgt"] = d["tgt"]
    if done:
        log(f"   {sum(1 for s in segs if s.get('tgt'))} lines already translated before the interruption")

    def save():
        write_json(partial, {str(s["id"]): {"src": s["src"], "tgt": s["tgt"]}
                             for s in segs if s.get("tgt") and s["tgt"] != "[MISSING]"})
        done_lines = [s for s in segs if s.get("tgt")]
        events.emit("progress", stage="translate", label="translating", done=len(done_lines), total=len(segs),
                    line=done_lines[-1]["id"] if done_lines else None,
                    at=done_lines[-1]["end"] if done_lines else None)

    translate(segs, ctx.server, ctx.settings, ctx.profile, progress=save)
    run.segs = segs
    partial.unlink(missing_ok=True)
    over = sum(1 for s in segs if len(s["tgt"]) > s["budget"])
    missing = sum(1 for s in segs if s["tgt"] == "[MISSING]")
    log(f"   {len(segs)} lines; {over} still over their character budget" + (f", {missing} MISSING" if missing else ""))


def stage_review2(run: Run, ctx: Ctx):
    path = run.p("review-2-translation.txt")
    segs = run.segs
    if not path.exists():
        review.write_translation_review(path, segs, ctx.profile["source_lang"], ctx.profile["target_lang"])
        if ctx.no_review:
            log("   review skipped (--no-review); nothing is learned from this run")
            return None
        over = sum(1 for s in segs if len(s["tgt"]) > s["budget"])
        return waiting(ctx, "review2", "Review 2 of 2 is ready (the translation)", path,
                       f"{len(segs)} lines, {over} marked OVER (they will be sped up, up to "
                       f"{round((ctx.settings['max_speedup'] - 1) * 100)}%). An empty {review.label(ctx.profile['target_lang'])} line is left silent.")
    parsed = review.parse(path, ctx.profile["source_lang"], ctx.profile["target_lang"])
    if parsed["status"] != "approved":
        return waiting(ctx, "review2", "Review 2 of 2 is still waiting for you", path)
    for s in segs:
        if s["id"] in parsed["tgt"]:
            s["tgt"] = parsed["tgt"][s["id"]]
    missing = [s["id"] for s in segs if s["tgt"].strip() == "[MISSING]"]
    if missing:
        raise RuntimeError(f"review 2: lines {missing} still say [MISSING]; write them (or leave the line empty) "
                           f"and set 'status: approved' again")
    today = dt.date.today().isoformat()
    entries = [{"src": s["src"], "tgt": s["tgt"], "model_tgt": s["model_tgt"], "edited": s["tgt"] != s["model_tgt"],
                "video": run.name, "date": today} for s in segs if s["tgt"].strip()]
    ctx.profile.remember(entries)
    ctx.profile["glossary"].update(parsed["glossary"])
    ctx.profile.save()
    run.segs = segs
    log(f"   {sum(e['edited'] for e in entries)} lines edited; remembered {len(entries)} lines and "
        f"{len(parsed['glossary'])} glossary terms for this channel")


def voicing(run: Run, ctx: Ctx, segs: list[dict]):
    """Ready to voice: the GPUs freed for a check on this machine, and with takes to compare, the speaker
    model and the speaker's voiceprint (None, None without them)."""
    if local(ctx, "nemo"):  # the voice clone stays on the server (GPU 0), the check runs here (GPU 1)
        ctx.server.unload_all(keep_task="voice")
        log(f"   {ctx.settings['tts_model']} on the model server, checking each take with Nemotron on "
            f"{ctx.settings['nemo']['check_device']}")
    else:  # llama-swap runs both side by side (its "voicing" group)
        log(f"   {ctx.settings['tts_model']}, checking each take with {ctx.settings['asr_model']} (both on the model server)")
    embedder = vp = None
    if ctx.settings.get("takes_per_line", 1) > 1:
        from .speaker import get_embedder
        embedder = get_embedder(ctx.settings)
        vp = run_voiceprint(run, embedder, segs) if embedder is not None else None
        if vp is not None:
            log(f"   comparing up to {ctx.settings['takes_per_line']} good takes per line by how much they sound like the speaker")
    return embedder, vp


def stage_voice(run: Run, ctx: Ctx):
    segs = run.segs
    for s in segs:  # a redo starts from the translated text, not from an earlier automatic shortening
        if s.get("shortened_from"):
            s["tgt"] = s.pop("shortened_from")
    lang = ctx.profile["target_lang"]
    embedder, vp = voicing(run, ctx, segs)
    report = voice_all(segs, run.dir, ctx.server, ctx.settings, ctx.profile, lang, embedder, vp)
    over = [s for s in segs if s.get("take") and s["take"]["ok"] and "OVER" in s["take"]["fit"]]
    if over and ctx.settings["auto_shorten"]:
        # too long in every take: the text itself doesn't fit. Shorten to the length the voice needs, re-voice.
        log(f"   {len(over)} lines are too long in every take: shortening them to their measured length")
        speed = ctx.settings["max_speedup"]
        before = {s["id"]: s["tgt"] for s in over}
        items = [(s, max(8, int(len(s["tgt"]) * float(s["slot"]) * speed / s["take"]["spoken"] * 0.92)))
                 for s in over]
        changed = shorten_to(items, ctx.server, ctx.settings, ctx.profile, f"shortening {len(over)} lines to fit")
        for s in changed:
            s["shortened_from"] = before[s["id"]]
        if changed:
            log(f"   re-voicing {len(changed)} shortened lines")
            if local(ctx, "nemo"):  # shortening loaded the text model again
                ctx.server.unload_all(keep_task="voice")
            redo = {s["id"] for s in changed}
            report = sorted([r for r in report if r["id"] not in redo] +
                            voice_all(changed, run.dir, ctx.server, ctx.settings, ctx.profile, lang, embedder, vp),
                            key=lambda r: r["id"])
    run.segs = segs
    write_json(run.p("voice.json"), report)
    takes = [r for r in report if "tries" in r]
    first_seed = ctx.settings["seeds"][0]
    first = sum(1 for r in takes if r["ok"] and r["seed"] == first_seed)
    later = sum(1 for r in takes if r["ok"] and r["seed"] != first_seed)
    failed = sum(1 for r in takes if not r["ok"])
    over = sum(1 for r in takes if "OVER" in r["fit"])
    log(f"   {first} lines kept the first take, {later} a later one (it passed, fitted or sounded more like the speaker), "
        f"{failed} never passed the check; "
        f"{over} still over their slot")
    sims = sorted(r["sim"] for r in takes if r.get("sim") is not None)
    if sims:
        run.state["voice_similarity"] = {"median": sims[len(sims) // 2], "lowest_10pct": sims[len(sims) // 10]}
        log(f"   sounds like the speaker: median {sims[len(sims) // 2]:.2f}, lowest 10% below {sims[len(sims) // 10]:.2f}")


def write_checklist(path: Path, segs: list[dict], video: Path, tgt: str) -> int:
    lines = [f"# Lines worth a listen in {video.name}", ""]
    count = 0
    for s in segs:
        notes = []
        t = s.get("take")
        if s.get("tgt", "").strip() and not t:
            notes.append("no audio")
        if t and not t["ok"]:
            notes.append(f"failed the check after {t['tries']} takes ({t['why']}); heard: \"{t['heard']}\"")
        if s.get("overlap", 0) > 0.1:
            notes.append(f"runs {s['overlap']:.1f}s into the next line ({t['fit']})")
        if s.get("flags"):
            notes.append("; ".join(s["flags"]))
        if s.get("shortened_from"):
            notes.append(f"shortened to fit, was: \"{s['shortened_from']}\"")
        if notes:
            count += 1
            lines += [f"[{s['id']}] {media.mmss(s['start'])}   " + "   ".join(notes), f"{review.label(tgt)}: {s.get('tgt', '')}", ""]
    if count == 0:
        lines.append("Nothing: every line passed the check and fits.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


def stage_mix(run: Run, ctx: Ctx):
    segs = run.segs
    profile = ctx.profile
    src_l, tgt_l = profile["source_lang"], profile["target_lang"]
    timing = place(segs, run.state["seconds"], run.p("dub-voice.wav"))
    run.segs = segs
    log(f"   {timing['delayed']} lines start a little late to let the line before finish; "
        f"{timing['overlapping']} still overlap the next line")
    levels = mix(run.p("dub-voice.wav"), run.p("vocals.wav"), run.p("music.wav"), run.p("dub-mix.wav"),
                 ctx.settings["lufs"])
    out = run.p(f"{run.name}.{tgt_l}.mp4")
    mux(Path(run.state["video"]), run.p("dub-mix.wav"), out, iso3(tgt_l), iso3(src_l))
    subtitles(segs, run.p(f"{run.name}.{src_l}.srt"), run.p(f"{run.name}.{tgt_l}.srt"), tgt_l)
    n = write_checklist(run.p("check.txt"), segs, out, ctx.profile["target_lang"])
    run.state["outputs"] = {"video": str(out), "subtitles": [str(run.p(f"{run.name}.{tgt_l}.srt")),
                                                            str(run.p(f"{run.name}.{src_l}.srt"))],
                            "check": str(run.p("check.txt")), "lines_to_check": n, "levels": levels}
    log(f"   mix at {levels['final_lufs']} LUFS; dub levelled to the speaker's original voice ({levels['voice_lufs']} LUFS)")


STAGES = [
    ("prep", "cut and extract the audio", stage_prep),
    ("separate", "split the speaker's voice from the music and effects", stage_separate),
    ("transcribe", "transcribe with Nemotron", stage_transcribe),
    ("spelling", "sentences and spelling", stage_spelling),
    ("review1", "your check of the transcript and voice sample", stage_review1),
    ("translate", "translate", stage_translate),
    ("review2", "your check of the translation", stage_review2),
    ("voice", "voice every line in the speaker's voice, checking each take", stage_voice),
    ("mix", "mix over the music, write the video and subtitles", stage_mix),
]
NAMES = [s[0] for s in STAGES]
SHORTS_ONLY = NAMES[:NAMES.index("review1") + 1]  # a Shorts-only run: the transcript, then its Shorts
SHORTS_LANGS = ("original", "dub")  # run --shorts N makes both, in this order


def run_stages(state: dict) -> list[str]:
    """The stages of this run: all of them for a dub, up to review 1 for a Shorts-only run."""
    return SHORTS_ONLY if state.get("mode") == "shorts" else NAMES
STAGE_FILES = {  # removed when a stage is redone
    "review1": ["review-1-transcript.txt", "voice-sample.wav", "voice-candidates", "voice-candidates.json"],
    "translate": ["translate-partial.json"],
    "review2": ["review-2-translation.txt"],
}  # voice takes are kept: they are named by the line's text and the voice sample, so a redo reuses them


# --- commands ------------------------------------------------------------------------------------------

def default_name(src: Path, start, length) -> str:
    name = re.sub(r"[^\w.-]+", "-", src.stem).strip("-") or "video"
    if start is not None or length is not None:
        name += f"-{int(start or 0)}s" + (f"-{int(length)}s" if length else "")
    return name


def channel_profile(name: str, args, settings: dict, need_dictionary: bool = False,
                    shorts_only: bool = False) -> Profile | str:
    """The channel's profile for this run, or why it can't be used. A new channel gets its languages
    (and speaker) from --from/--to/--speaker; an existing one must not be given others. A Shorts-only
    run needs only the video's language: such a channel gets its dub language on its first dub, once.
    With `need_dictionary`, a new channel whose spelling dictionary is missing isn't made at all."""
    profile = Profile(name)
    if profile.is_new:
        if not (args.source and (args.target or shorts_only)):
            return (f"channel '{name}' is new: give the video's language and the dub's with --from and --to, "
                    f"e.g. --from bg --to en (localdub languages lists them; Shorts only: --from alone)")
        try:
            profile.set_languages(args.source, args.target, settings)
        except ValueError as e:
            return str(e)
        if need_dictionary and (missing := missing_dictionary(profile)):
            return missing  # before it's saved: a new try may choose other languages
        profile["speaker"] = args.speaker or ""
        profile.save()
        dub = f" → {profile['target_language_name']}" if profile.dubs else " (Shorts only so far)"
        log(f"localdub: new channel '{name}': {profile['source_language_name']}{dub}")
        return profile
    if args.source and args.source != profile["source_lang"]:
        return (f"channel '{name}' is for {profile['source_language_name']} videos ({profile['source_lang']}); "
                f"for {args.source} use another channel name")
    if not profile.dubs and not shorts_only:
        if not args.target:
            return (f"channel '{name}' has made only Shorts so far: give its dub language once with --to, "
                    f"e.g. --to en (localdub languages lists them)")
        try:
            profile.set_languages(profile["source_lang"], args.target, settings)
        except ValueError as e:
            return str(e)
        profile.save()
        log(f"localdub: channel '{name}' now dubs into {profile['target_language_name']}")
    given = (profile["source_lang"], args.target or profile["target_lang"])
    if profile.dubs and given != (profile["source_lang"], profile["target_lang"]):
        return (f"channel '{name}' dubs {profile['source_lang']} → {profile['target_lang']}; "
                f"for {given[0]} → {given[1]} use another channel name")
    if args.speaker and args.speaker != profile["speaker"]:
        profile["speaker"] = args.speaker
        profile.save()
    return profile


def cmd_run(args) -> int:
    settings = load_settings()
    target = Path(args.video).expanduser()
    if not target.exists() and (home() / "runs" / args.video / "state.json").exists():
        run = Run(args.video)  # continue by run name
    else:
        if not target.exists():
            return fail(f"{target} not found (and no run called '{args.video}' in {home() / 'runs'})")
        start = media.parse_mmss(args.start) if args.start else None
        length = media.parse_mmss(args.duration) if args.duration else None
        run = Run(args.name or default_name(target.resolve(), start, length))
        if not run.state:
            if not args.channel:
                return fail("a new run needs --channel NAME (the channel whose profile it uses and teaches)")
            checked = channel_profile(args.channel, args, settings, need_dictionary=True,  # before the run exists
                                      shorts_only=args.shorts_only)
            if isinstance(checked, str):
                return fail(checked)
            if missing := missing_dictionary(checked):
                return fail(missing)
            run.state = {"source": str(target.resolve()), "channel": args.channel, "start": start, "length": length,
                         "created": dt.datetime.now().isoformat(timespec="seconds"),
                         "mode": "shorts" if args.shorts_only else "dub", "done": [], "timings": {}}
            run.save()
    st = run.state
    events.note(run=run.name, channel=st["channel"], dir=str(run.dir))
    if args.channel and args.channel != st["channel"]:
        return fail(f"run '{run.name}' belongs to channel '{st['channel']}'; use --name for a separate run")
    shorts_only = st.get("mode") == "shorts"
    if args.shorts_only and not shorts_only:
        return fail(f"run '{run.name}' is a dub; for Shorts only start a new run (--name)")
    checked = channel_profile(st["channel"], args, settings, shorts_only=shorts_only)
    if isinstance(checked, str):
        return fail(checked)
    names = run_stages(st)
    if args.redo:
        if args.redo not in names:
            return fail(f"--redo takes one of: {', '.join(names)}")
        for name in names[names.index(args.redo):]:
            if name in st["done"]:
                st["done"].remove(name)
            for f in STAGE_FILES.get(name, []):
                p = run.p(f)
                shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
        run.save()
    profile = checked
    if not {"spelling", "review1"} <= set(st["done"]) and (missing := missing_dictionary(profile)):
        return fail(missing)  # the spelling stages need it: say so now, not after the GPU work
    ctx = Ctx(settings, profile, Models(settings), args.no_review, f"localdub run {run.name}")
    log(f"localdub: run '{run.name}' (channel '{st['channel']}') in {run.dir}")
    shorts_after = ["original"] if shorts_only else list(SHORTS_LANGS) if args.shorts else []
    events.emit("start", command="run", run=run.name, channel=st["channel"], dir=str(run.dir),
                mode=st.get("mode", "dub"), stages=names + [f"shorts-{lang}" for lang in shorts_after],
                done=list(st["done"]))
    for name, what, fn in STAGES:
        if name in st["done"] or name not in names:
            continue
        log(f"\n== {name}: {what}")
        events.emit("stage", stage=name, status="started")
        t0 = time.monotonic()
        try:
            result = fn(run, ctx)
        except (media.ToolError, LLMError, RuntimeError, OSError) as e:
            run.save()
            log(f"Fix the cause, then continue with: {ctx.rerun}")
            return fail(f"in stage '{name}': {e}", stage=name)
        if result == WAIT:
            run.save()
            events.emit("stage", stage=name, status="waiting")
            return 3
        took = round(time.monotonic() - t0, 1)
        st["done"].append(name)
        st["timings"][name] = took
        run.save()
        events.emit("stage", stage=name, status="done", seconds=took)
        log(f"   ({took:.0f}s)")
    for lang in shorts_after:
        stage = f"shorts-{lang}"
        events.emit("stage", stage=stage, status="started")
        t0 = time.monotonic()
        rc = make_shorts(run, settings, lang, auto=args.shorts)
        if rc != 0:
            return rc
        st["timings"][stage] = round(time.monotonic() - t0, 1)
        run.save()
        events.emit("stage", stage=stage, status="done", seconds=st["timings"][stage])
    machine = sum(v for k, v in st["timings"].items() if not k.startswith("review"))
    if shorts_only:
        events.note(shorts=st.get("shorts", {}), timings=st["timings"])
        log("\n== done")
        for f in st["shorts"]["original"]:
            log(f"   {f}")
        log("   time per stage: " + ", ".join(f"{k} {v:.0f}s" for k, v in st["timings"].items()))
        log(f"   machine time in total: {machine / 60:.1f} min")
        return 0
    out = st["outputs"]
    events.note(outputs=out, shorts=st.get("shorts", {}), timings=st["timings"])
    log("\n== done")
    log(f"   video:      {out['video']}")
    log(f"   subtitles:  {out['subtitles'][0]}  (and {Path(out['subtitles'][1]).name})")
    log(f"   check list: {out['check']}  ({out['lines_to_check']} lines worth a listen)")
    for lang, files in (st.get("shorts") or {}).items() if args.shorts else []:
        log(f"   Shorts ({lang}): " + ", ".join(Path(f).name for f in files))
    log("   time per stage: " + ", ".join(f"{k} {v:.0f}s" for k, v in st["timings"].items()))
    log(f"   machine time in total: {machine / 60:.1f} min")
    return 0


def check_models(settings: dict, used: list[str], line) -> None:
    """`check`'s lines for the model servers: each one the `used` tasks are on answers (llama-swap's
    key is there), and lists the task's model. A voice or separation server of the openai kind is the
    tool's own server, which lists nothing: that it answers is enough."""
    from . import models
    ai = Models(settings)
    configs = models.servers(settings)
    by_server: dict[str, list[str]] = {}
    for t in used:
        by_server.setdefault(ai.tasks[t]["server"], []).append(t)
    for name, tasks in by_server.items():
        if name not in ai.servers:
            line(False, f"server '{name}' for {', '.join(tasks)}", "add it: localdub models --server NAME --url URL")
            continue
        server, cfg = ai.servers[name], configs[name]
        if server.kind == "llama-swap" and cfg.get("key_env"):
            line(bool(server.key), f"API key of '{name}' (${cfg['key_env']} or {cfg.get('key_file')})",
                 "set it in the environment or in that file, or: localdub models --server NAME --key-stdin")
        where = f"model server '{name}' ({server.kind}) at {server.url}"
        if models.is_remote(server.url):
            log(f"  note  '{name}' is off this network: frames, transcripts and voice samples are sent there")
        try:
            listed = server.models(timeout=10)
        except LLMError as e:
            answered = ": HTTP " in str(e)  # it's there, it just doesn't list models
            line(answered and all(t in ("voice", "separation") for t in tasks), where, str(e))
            continue
        line(True, where)
        for t in tasks:
            m = ai.model(t)
            direct = server.kind == "openai" and t in ("voice", "separation")
            hint = ("add it to the llama-swap config (see deploy/gpu/llama-swap.yaml)" if server.kind == "llama-swap"
                    else f"it lists: {', '.join(listed[:8]) or 'nothing'}")
            line(direct or m in listed, f"{t}: model {m}", hint)


def cmd_models(args) -> int:
    """`localdub models`: which model does each task, on which server; change them; try one."""
    from . import models as m
    settings = load_settings()
    if args.server:
        name = args.server
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", name):
            return fail(f"server name {name!r}: letters, digits, '.', '_' and '-', at most 32")
        if args.remove:
            if why := m.remove_server(name, settings):
                return fail(why)
            log(f"localdub: removed the server '{name}'")
        else:
            if args.url is None and name not in m.servers(settings):
                return fail(f"server '{name}' is new: give its --url")
            if args.url is not None and not re.fullmatch(r"https?://[^\s]+", args.url):
                return fail(f"--url {args.url!r}: an http:// or https:// address")
            if args.url is not None or args.kind:
                if m.save_server(name, args.url, args.kind, settings, new_key=args.key_stdin):
                    log(f"localdub: server '{name}' moved to another address: its saved key is forgotten "
                        f"(give it again with --key-stdin)")
            if args.key_stdin:
                m.store_key(name, sys.stdin.readline().strip())  # stdin, so the key is in no command line
            log(f"localdub: server '{name}' saved")
        settings = load_settings()
    for item in args.set or []:
        task, _, rest = item.partition("=")
        server, _, model = rest.partition("/")  # a model's name may have more slashes (org/model)
        if task not in m.TASKS or not server or not model:
            return fail(f"--set {item!r}: TASK=SERVER/MODEL, e.g. text=main/gemma4-31b (tasks: {', '.join(m.TASKS)})")
        if server not in m.servers(settings):
            return fail(f"--set {item!r}: there's no server '{server}' (add it with --server {server} --url ...)")
        m.save_task(task, server, model)
        log(f"localdub: {task} is done by {model} on '{server}'")
    for item in args.effort or []:
        use, _, level = item.partition("=")
        if use not in m.EFFORTS or level not in m.EFFORT_LEVELS:
            return fail(f"--effort {item!r}: USE=LEVEL, uses {', '.join(m.EFFORTS)}, levels {', '.join(m.EFFORT_LEVELS)}")
        m.save_effort(use, level)
        log(f"localdub: {use} thinks at '{level}'")
    settings = load_settings()
    if args.test:
        result = m.try_task(args.test, settings)
        events.emit("models_test", **result)
        speed = f", {result['tok_per_s']} tok/s" if result["tok_per_s"] else ""
        log(f"  {'ok  ' if result['ok'] else 'FAIL'}  {args.test}: {result['model']} on '{result['server']}' "
            f"({result['seconds']} s{speed}): {result['detail']}")
        if not result["ok"]:
            events.note(error=result["detail"])
        return 0 if result["ok"] else 1
    info = m.overview(settings)
    events.emit("models", **info)
    for s in info["servers"]:
        state = f"{len(s['models'])} models" if s["models"] else ("answers" if s["reachable"] else f"not reachable: {s['error']}")
        note = ", off this network" if s["remote"] else ""
        log(f"server {s['name']} ({s['kind']}{note}) {s['url']}: {state}; key {'set' if s['key_set'] else 'none'}")
    for t in info["tasks"]:
        log(f"  {t['title']:<11} {t['model']} on '{t['server']}'" + ("" if t["listed"] else "  (its server doesn't list it)"))
    log("thinking: " + ", ".join(f"{use} {level}" for use, level in info["efforts"].items()))
    return 0


def cmd_guide(args) -> int:
    """`localdub guide`: the guide for AI agents driving localdub (guide.md, shipped with the package)."""
    from importlib.resources import files
    text = files("localdub").joinpath("guide.md").read_text(encoding="utf-8")
    if args.json:
        events.emit("guide", text=text)
    else:
        print(text)
    return 0


def cmd_check(args) -> int:
    settings = load_settings()
    ok = True

    def line(good: bool, what: str, hint: str = ""):
        nonlocal ok
        ok &= good
        events.emit("check", what=what, ok=good, hint=hint)
        log(f"  {'ok  ' if good else 'FAIL'}  {what}" + ("" if good or not hint else f"   -> {hint}"))

    cfg = home() / "settings.json"
    log(f"localdub check (settings: {cfg}{'' if cfg.exists() else ', defaults'})")
    for tool in ("ffmpeg", "ffprobe", "hunspell"):
        line(shutil.which(tool) is not None, tool, f"apt-get install {tool if tool != 'ffprobe' else 'ffmpeg'}")
    dictionaries: dict[str, list[str]] = {}  # the dictionaries the channels need, and which channels
    for name in channel_names():
        if dict_name := Profile(name)["hunspell_dict"]:
            dictionaries.setdefault(dict_name, []).append(name)
    if not dictionaries:
        log("  (no channels yet: their spelling dictionaries are checked once they exist; see localdub languages)")
    for dict_name, users in sorted(dictionaries.items()):
        problem = missing_dictionary(Profile(users[0]))
        line(problem is None, f"hunspell {dict_name} dictionary (channel {', '.join(users)})",
             problem.split(": ", 1)[-1] if problem else "")
    nemo, d = settings["nemo"], settings["demucs"]
    for tool, backend in (("nemo", nemo["backend"]), ("demucs", d["backend"])):
        line(backend in ("http", "local"), f"{tool}.backend: {backend}", 'use "http" (the model server) or "local"')
    used = ["text", "vision", "voice"]  # the tasks done on a model server
    fix = "set the right path in ~/dub/settings.json (see README, Settings)"
    if nemo["backend"] == "local":
        line(Path(nemo["bin"]).exists() or shutil.which(nemo["bin"]) is not None, f"nemo-speech: {nemo['bin']}", fix)
        line(Path(nemo["model"]).exists(), f"Nemotron model: {nemo['model']}", fix)
        line(Path(nemo["config"]).exists(), f"Nemotron config: {nemo['config']}", fix)
    else:
        used.append("hearing")
    if d["backend"] == "local":
        try:
            out = media.run(d["python"], "-c", "import demucs, torch; print(torch.cuda.is_available())").strip()
            line(out == "True", f"Demucs + CUDA torch in {d['python']}", "torch can't see the GPU")
        except (media.ToolError, OSError) as e:
            line(False, f"Demucs in {d['python']}", str(e).splitlines()[0])
    else:
        used.append("separation")
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
        line(" ass " in p.stdout, "ffmpeg can burn in captions (libass)", "install an ffmpeg built with libass")
    except OSError:
        pass
    font = subprocess.run(["fc-match", "-f", "%{file}", "DejaVu Sans:bold"], capture_output=True, text=True) \
        if shutil.which("fc-match") else None
    line(bool(font and "DejaVu" in font.stdout), "caption font (DejaVu Sans)", "apt-get install fontconfig fonts-dejavu-core")
    from .speaker import get_embedder
    emb = get_embedder(settings)
    if emb is None:
        line(False, "speaker fingerprints (picks the voice by likeness)", "pip install -e .[voice], or set speaker.enabled false")
    else:
        try:
            emb.embed([np.random.default_rng(0).normal(0, 0.1, 16000).astype(np.float32)])
            line(True, f"speaker fingerprints ({settings['speaker']['model']} on {settings['speaker']['device']})")
        except Exception as e:  # noqa: BLE001
            line(False, "speaker fingerprints", str(e)[:200])
    check_models(settings, used, line)
    log("all good" if ok else "fix the FAIL lines above")
    if not ok:
        events.note(error="some checks failed")
    return 0 if ok else 1


def make_shorts(run: Run, settings: dict, lang: str, auto: int = 0, redo: str | None = None) -> int:
    """0 = rendered, 3 = waiting for you to pick, 1 = error."""
    from . import shorts as sh
    profile = Profile(run.state["channel"])
    language = profile["source_language_name"] if lang == "original" else profile["target_language_name"]
    work = run.p("shorts")
    out_dir = work / lang
    out_dir.mkdir(parents=True, exist_ok=True)
    cand_txt, cand_json, vision_json = out_dir / "candidates.txt", out_dir / "candidates.json", work / "vision.json"
    if redo == "vision":
        shutil.rmtree(work / "sheets", ignore_errors=True)
        vision_json.unlink(missing_ok=True)
    if redo in ("vision", "select"):
        cand_txt.unlink(missing_ok=True)
        cand_json.unlink(missing_ok=True)
    segs = run.segs
    rerun = f"localdub shorts {run.name} --lang {lang}"
    if not cand_txt.exists():
        server = Models(settings)
        video, seconds = Path(run.state["video"]), run.state["seconds"]
        try:
            log(f"\n== Shorts in {language} ({lang}) from '{run.name}'")
            mpath = work / "motion.json"
            if mpath.exists():
                mot = read_json(mpath)
            else:
                log("   motion: shot cuts and movement")
                t0 = time.monotonic()
                mot = sh.motion(video, seconds)
                write_json(mpath, mot)
                log(f"   {len(mot['cuts'])} cuts ({time.monotonic() - t0:.0f}s)")
            sheets = sh.make_sheets(video, work / "sheets", settings["shorts"]["frame_every"], seconds)
            vision = sh.look(sheets, vision_json, server)
            cands = sh.select(segs, vision, mot, server, settings, lang, language)
        except (media.ToolError, LLMError, RuntimeError, OSError) as e:
            log(f"Fix the cause, then run again (finished steps are kept): {rerun}")
            return fail(str(e), stage=f"shorts-{lang}")
        if not cands:
            return fail(f"no usable candidates came back; try again with: {rerun} --redo select", stage=f"shorts-{lang}")
        write_json(cand_json, cands)
        sh.write_candidates(cand_txt, rerun, cands, segs, lang, auto=min(auto, len(cands)))
        log(f"   {len(cands)} candidates, strongest first: {cand_txt}")
    status, picked = sh.read_candidates(cand_txt)
    if status != "approved" or not picked:
        events.note(waiting_for={"kind": "shorts", "lang": lang, "file": str(cand_txt)})
        log(f"\n== Pick your {language} Shorts\n   {cand_txt}")
        log("   Mark the ones to render with [x], set 'status: approved', save, then run:")
        log(f"   {rerun}")
        return 3
    try:
        made = render_shorts(run, settings, lang, picked, language)
    except (media.ToolError, RuntimeError) as e:
        return fail(str(e), stage=f"shorts-{lang}")
    run.state.setdefault("shorts", {})[lang] = made
    run.save()
    return 0


def render_shorts(run: Run, settings: dict, lang: str, picked: list[dict], language: str) -> list[str]:
    """Render these picked Shorts (each replaces its earlier file); their paths."""
    from . import shorts as sh
    out_dir = run.p("shorts") / lang
    video = Path(run.state["video"] if lang == "original" else run.state["outputs"]["video"])
    profile = Profile(run.state["channel"])
    code = profile["source_lang"] if lang == "original" else profile["target_lang"]
    segs = run.segs
    log(f"   rendering {len(picked)} {language} Shorts from {video.name}")
    made = []
    for pick in picked:
        out = out_dir / f"short-{pick['n']}-{sh.slug(pick['title'])}.mp4"
        t0 = time.monotonic()
        sh.render(video, segs, pick, out, out_dir, settings["shorts"]["title"], lang, code)
        made.append(str(out))
        log(f"   {out.name}: {media.duration(out):.0f}s, rendered in {time.monotonic() - t0:.0f}s")
    return made


def shorts_with_line(run: Run, line: int) -> list[dict]:
    """The rendered dub Shorts this line is in (picked in shorts/dub/candidates.txt)."""
    from . import shorts as sh
    cand_txt = run.p("shorts") / "dub" / "candidates.txt"
    if not run.state.get("shorts", {}).get("dub") or not cand_txt.exists():
        return []
    status, picked = sh.read_candidates(cand_txt)
    if status != "approved":
        return []
    order = [s["id"] for s in run.segs]
    at = order.index(line)
    return [p for p in picked if p["start"] in order and p["end"] in order
            and order.index(p["start"]) <= at <= order.index(p["end"])]


def cmd_shorts(args) -> int:
    run = Run(args.name)
    events.note(run=run.name, dir=str(run.dir))
    shorts_only = run.state.get("mode") == "shorts"
    lang = args.lang or ("original" if shorts_only else "dub")
    done = run.state.get("done", [])
    if shorts_only and lang == "dub":
        return fail(f"run '{args.name}' is Shorts only: it has no dub to cut from (--lang original)")
    if not run.state or "review1" not in done or (not shorts_only and "mix" not in done):
        return fail(f"run '{args.name}' isn't finished yet (localdub run {args.name})")
    events.emit("start", command="shorts", run=run.name, channel=run.state["channel"], dir=str(run.dir),
                lang=lang)
    rc = make_shorts(run, load_settings(), lang, args.auto, args.redo)
    if rc == 0:
        events.note(shorts={lang: run.state["shorts"][lang]})
        log("\n== done")
        for f in run.state["shorts"][lang]:
            log(f"   {f}")
    return rc


def cmd_voice(args) -> int:
    """`localdub voice RUN`: voice-sample candidates from the run's speech and the channel's voice
    now, each with test sentences cloned from it (and scored), to listen to and compare; waits for a
    pick (exit 3). `--pick N|current|MM:SS-MM:SS|file.wav` makes that the channel's voice."""
    run = Run(args.name)
    events.note(run=run.name, dir=str(run.dir))
    if not run.state:
        return fail(f"no run called '{args.name}' in {home() / 'runs'}")
    segs_file = run.p("segments.json") if run.p("segments.json").exists() else run.p("spelled.json")
    if not (segs_file.exists() and run.p("vocals.wav").exists()):
        return fail(f"run '{run.name}' isn't transcribed yet: voice samples come from its separated speech "
                    f"(localdub run {run.name} gets it there)")
    profile = Profile(run.state["channel"])
    if not profile.is_new and not profile.dubs:
        return fail(f"channel '{profile.name}' has made only Shorts so far: a voice is picked for a dub language "
                    f"(its first dub: localdub run VIDEO --channel {profile.name} --to LANG)")
    events.emit("start", command="voice", run=run.name, channel=profile.name, dir=str(run.dir))
    if args.pick is not None:
        if args.pick == "current":
            if profile.voice_path is None or not profile.voice_path.exists():
                return fail(f"channel '{profile.name}' has no voice yet: pick a candidate")
            source = profile.data.get("voice_from") or "the channel's voice"
            mark_picked(run, load_candidates(run), "current")
        else:
            try:
                source = set_voice(run, profile, args.pick)
            except RuntimeError as e:
                return fail(str(e))
            profile.save()
        events.note(voice=source)
        log(f"localdub: channel '{profile.name}' now dubs with: {source}")
        log(f"   to re-dub this run with it: localdub run {run.name} --redo voice")
        return 0
    settings = load_settings()
    ctx = Ctx(settings, profile, Models(settings), False, f"localdub voice {run.name}")
    log(f"localdub: voice samples for channel '{profile.name}' from run '{run.name}'")
    data = make_candidates(run, ctx, upgrade_all(read_json(segs_file)), current=True)
    if not data["candidates"] and not data["current"]:
        return fail("no clean stretch of 10-20 s of speech was found; give one with --pick 01:20-01:38")
    if not data["scored"]:
        log("   (no scores: the speaker model isn't installed, pip install localdub[voice]; listen to choose)")
    events.note(waiting_for={"kind": "voice", "file": str(run.p(CANDIDATES))})
    log(f"\n== Listen and compare: {run.p('voice-candidates')}")
    log(f"   then: localdub voice {run.name} --pick N (or current, or a stretch like 01:20-01:38)")
    return 3


def line_info(s: dict) -> dict:
    """One line of a dub, as the `line` event shows it."""
    t = s.get("take") or {}
    return {"id": s["id"], "start": s["start"], "end": s["end"], "src": s.get("src", ""), "tgt": s.get("tgt", ""),
            "placed": s.get("placed"), "seconds": t.get("seconds"), "passed": t.get("ok"), "why": t.get("why") or None,
            "heard": t.get("heard"), "fit": t.get("fit"), "shortened_from": s.get("shortened_from"),
            "fixed": bool(s.get("fixed"))}


def cmd_line(args) -> int:
    """`localdub line RUN ID`: one line of a finished dub, to fix it without redoing the rest. `--text` gives it new
    words (empty: the line stays silent), `--new-take` voices the same words again with new seeds. Either way the
    line is voiced and checked as usual, the dub is mixed again, and the dubbed Shorts it is in are rendered again."""
    run = Run(args.name)
    events.note(run=run.name, dir=str(run.dir))
    st = run.state
    if not st:
        return fail(f"no run called '{args.name}' in {home() / 'runs'}")
    if st.get("mode") == "shorts":
        return fail(f"run '{run.name}' is Shorts only: it has no dub")
    if "mix" not in st.get("done", []):
        return fail(f"run '{run.name}' isn't dubbed yet (localdub run {run.name}); lines are fixed after the dub")
    segs = run.segs
    s = next((x for x in segs if x["id"] == args.id), None)
    if s is None:
        return fail(f"run '{run.name}' has no line {args.id} (its lines: {segs[0]['id']}-{segs[-1]['id']})")
    text = sys.stdin.readline() if args.text_stdin else args.text
    text = " ".join(text.split()) if text is not None else None  # one line
    profile = Profile(st["channel"])
    tgt_l = profile["target_lang"]
    if text is None and not args.new_take:  # just show it
        info = line_info(s)
        events.emit("line", run=run.name, **info)
        placed = f", dub at {media.mmss(info['placed'])} for {info['seconds']:.1f}s" if info["seconds"] else ""
        log(f"line {s['id']} of '{run.name}': {media.mmss(s['start'])}-{media.mmss(s['end'])}{placed}")
        log(f"{review.label(profile['source_lang'])} {info['src']}")
        log(f"{review.label(tgt_l)} {info['tgt']}")
        if info["passed"] is not None:
            log(f"check: {'passed' if info['passed'] else 'failed (' + str(info['why']) + ')'}, "
                f"heard \"{info['heard']}\"; {info['fit']}")
        log(f"fix it: localdub line {run.name} {s['id']} --text \"...\"  (or --new-take)")
        return 0
    remix_only = st.get("mix_pending") and (text is None or text == s.get("tgt", "")) and not args.new_take
    if text is not None and text == s.get("tgt", "") and not args.new_take and not remix_only:
        return fail(f"line {s['id']} says that already; for another take of it: --new-take")
    settings = load_settings()
    ctx = Ctx(settings, profile, Models(settings), True, f"localdub line {run.name} {s['id']}")
    redo = shorts_with_line(run, s["id"])
    steps = [] if remix_only else [("voice", f"voice line {s['id']} again, checking each take")]
    steps += [("mix", "mix over the music, write the video and subtitles")]
    steps += [("shorts-dub", f"render the {len(redo)} dubbed Shorts with this line again")] if redo else []
    events.emit("start", command="line", run=run.name, channel=st["channel"], dir=str(run.dir), mode="dub",
                stages=[name for name, _ in steps], done=[], line=s["id"])
    log(f"localdub: line {s['id']} of '{run.name}' ({media.mmss(s['start'])})")

    def voice_line():
        if text is not None and text != s.get("tgt", ""):
            log(f"   was: {s.get('tgt', '')}\n   now: {text or '(silent)'}")
            if text:  # the channel learns from it, as from review 2
                profile.remember([{"src": s["src"], "tgt": text, "model_tgt": s.get("model_tgt", s.get("tgt", "")),
                                   "edited": True, "video": run.name, "date": dt.date.today().isoformat()}])
            s["tgt"] = text
            s.pop("shortened_from", None)
        if args.new_take and profile.voice_path and profile.voice_path.exists():
            tried = seeds_tried(run.dir, s, profile.voice_path)
            first = max(tried + settings["seeds"]) + 1
            ctx.settings = {**settings, "seeds": list(range(first, first + len(settings["seeds"])))}
            log(f"   new takes, seeds {ctx.settings['seeds'][0]}-{ctx.settings['seeds'][-1]}")
        embedder, vp = voicing(run, ctx, segs)
        report = voice_all([s], run.dir, ctx.server, ctx.settings, profile, tgt_l, embedder, vp, label="re-voicing")
        s["fixed"] = True
        path = run.p("voice.json")
        old = read_json(path) if path.exists() else []
        write_json(path, sorted([r for r in old if r["id"] != s["id"]] + report, key=lambda r: r["id"]))
        t = s.get("take")
        if not t:
            log("   no words: the line is left silent")
        else:
            log(f"   {'passed' if t['ok'] else 'failed (' + t['why'] + ')'} the check after {t['tries']} "
                f"take{'s' if t['tries'] > 1 else ''}, heard \"{t['heard']}\"; {t['fit']}")
        run.segs = segs
        st["mix_pending"] = True  # if the mix fails, the same command mixes again
        run.save()

    def mix_again():
        ctx.settings = settings
        stage_mix(run, ctx)
        st.pop("mix_pending", None)
        run.save()

    made = []

    def shorts_again():
        made.extend(render_shorts(run, settings, "dub", redo, profile["target_language_name"]))

    work = {"voice": voice_line, "mix": mix_again, "shorts-dub": shorts_again}
    for name, what in steps:
        log(f"\n== {name}: {what}")
        events.emit("stage", stage=name, status="started")
        t0 = time.monotonic()
        try:
            work[name]()
        except (media.ToolError, LLMError, RuntimeError, OSError) as e:
            log(f"Fix the cause, then run the same command again: {ctx.rerun}")
            return fail(f"in stage '{name}': {e}", stage=name)
        events.emit("stage", stage=name, status="done", seconds=round(time.monotonic() - t0, 1))
    events.note(line=line_info(s), outputs=st["outputs"], shorts={"dub": made} if made else {})
    log("\n== done")
    log(f"   video:      {st['outputs']['video']}")
    for f in made:
        log(f"   Short:      {f}")
    return 0


def channel_names() -> list[str]:
    """Every channel with a saved profile."""
    root = home() / "channels"
    return sorted(p.parent.name for p in root.glob("*/profile.json")) if root.exists() else []


def dictionary_installed(name: str) -> bool:
    """Can hunspell load this dictionary? (An empty name means the language has none.)"""
    if not name:
        return False
    try:
        Speller(name, []).unknown(["a"])
        return True
    except (RuntimeError, OSError):
        return False


def missing_dictionary(profile: Profile) -> str | None:
    """Why the channel's spelling dictionary can't be used (with what to install), or None if it can.
    A language without a dictionary is fine: its spelling fixes are skipped."""
    name = profile["hunspell_dict"]
    if not name or dictionary_installed(name):
        return None
    lang = languages.LANGUAGES.get(profile["source_lang"])
    package = lang.apt if lang and lang.apt else f"the hunspell {name} dictionary"
    return (f"the {profile['source_language_name']} spelling dictionary ({name}) isn't installed: "
            f"apt-get install {package}")


def channel_info(profile: Profile) -> dict:
    return {
        "name": profile.name,
        "source": profile["source_lang"],
        "target": profile["target_lang"] or None,
        "source_name": profile["source_language_name"],
        "target_name": profile["target_language_name"] or None,
        "speaker": profile["speaker"] or None,
        "voice": profile.voice_path is not None and profile.voice_path.exists(),
        "voice_from": profile.data.get("voice_from"),
    }


def cmd_channel(args) -> int:
    profile = Profile(args.name)
    if profile.is_new:
        return fail(f"no channel called '{args.name}'; a channel is made by its first run "
                    f"(localdub run VIDEO --channel {args.name} --from .. --to ..)")
    if args.speaker is not None:
        profile["speaker"] = "" if args.speaker == "none" else args.speaker
        profile.save()
    info = channel_info(profile)
    voice = (info["voice_from"] or "set") if info["voice"] else "none yet"
    log(f"channel '{info['name']}': {info['source_name']} → {info['target_name'] or '(Shorts only so far)'}, "
        f"speaker: {info['speaker'] or 'not set'}, voice sample: {voice}")
    events.emit("channel", **info)
    return 0


def cmd_languages(args) -> int:
    settings = load_settings()
    if args.apt is not None:  # for scripts: the dictionary packages to install, on stdout
        codes = set(languages.LANGUAGES) if "all" in args.apt else set(args.apt)
        codes |= {Profile(name)["source_lang"] for name in channel_names()}
        packages = set()
        for code in codes:
            try:
                packages.add(languages.get(code).apt)
            except ValueError as e:
                return fail(str(e))
        print(" ".join(sorted(p for p in packages if p)))
        return 0
    dub = set(languages.dub_languages(settings))
    rows = []
    for lang in languages.LANGUAGES.values():
        installed = dictionary_installed(lang.hunspell)
        rows.append({"code": lang.code, "name": lang.name, "dub": lang.code in dub,
                     "dictionary": lang.hunspell or None, "dictionary_installed": installed, "apt": lang.apt or None})
        dict_note = "dictionary installed" if installed else f"no dictionary (apt-get install {lang.apt})"
        log(f"  {lang.code}  {lang.name:<11} {'can be dubbed into' if lang.code in dub else 'video only':<19} {dict_note}")
    events.emit("languages", languages=rows)
    return 0


def main(argv=None) -> int:
    from . import __version__
    ap = argparse.ArgumentParser(prog="localdub", description="Dub a video into another language in the creator's own voice.",
                                 epilog="Guides: 'localdub guide' (for AI agents: the loop, the files a run waits on, "
                                        "the events) and docs/cli.md in the repository (for people).")
    ap.add_argument("--version", action="version", version=f"localdub {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)  # options every command takes
    common.add_argument("--json", action="store_true",
                        help="also print machine-readable events on stdout, one JSON object per line (see events.py)")
    r = sub.add_parser("run", parents=[common], help="start or continue a run")
    r.add_argument("video", help="the video file, or the name of a run to continue")
    r.add_argument("--channel", help="channel profile to use and teach (needed for a new run)")
    r.add_argument("--start", help="only dub an excerpt starting here (mm:ss or seconds)")
    r.add_argument("--duration", help="excerpt length (mm:ss or seconds)")
    r.add_argument("--name", help="run name (default: from the file name)")
    r.add_argument("--no-review", action="store_true", help="skip both reviews (nothing is learned)")
    r.add_argument("--redo", metavar="STAGE", help=f"redo from this stage on: {', '.join(NAMES)}")
    r.add_argument("--shorts", type=int, default=0, metavar="N",
                   help="when the video is done, also make the top N Shorts in both languages, without stopping "
                        "(with --shorts-only: render the top N; 0 = stop for your pick)")
    r.add_argument("--shorts-only", action="store_true",
                   help="no dub: transcribe, then Shorts in the video's language (a new channel needs only --from)")
    r.add_argument("--from", dest="source", metavar="LANG",
                   help="a new channel's video language, e.g. bg (see: localdub languages)")
    r.add_argument("--to", dest="target", metavar="LANG",
                   help="a new channel's dub language, e.g. en (or, once, for a channel that made only Shorts)")
    r.add_argument("--speaker", choices=["man", "woman"],
                   help="who speaks, so gendered languages translate 'I was tired' right (optional)")
    s = sub.add_parser("shorts", parents=[common], help="propose Shorts from a finished run, then render the ones you pick")
    s.add_argument("name", help="the run's name")
    s.add_argument("--lang", choices=["dub", "original"],
                   help="dub: the dubbed video (the default for a dub); original: the creator's own audio and "
                        "language (the only one for a Shorts-only run)")
    s.add_argument("--auto", type=int, default=0, metavar="N", help="render the top N candidates without stopping")
    s.add_argument("--redo", choices=["vision", "select"], help="look at the video again / propose new candidates")
    v = sub.add_parser("voice", parents=[common],
                       help="compare voice samples from a run (and the channel's voice now), and pick one")
    v.add_argument("name", help="the run's name (it must be transcribed)")
    v.add_argument("--pick", metavar="CHOICE",
                   help="make this the channel's voice: a candidate's number, 'current' (keep it), a stretch "
                        "of the run's video (01:20-01:38) or a WAV file")
    li = sub.add_parser("line", parents=[common],
                        help="show one line of a finished dub, or fix it (new words or another take); the dub and "
                             "the dubbed Shorts with it are made again")
    li.add_argument("name", help="the run's name (a finished dub)")
    li.add_argument("id", type=int, help="the line's number, as in the reviews and check.txt")
    words = li.add_mutually_exclusive_group()
    words.add_argument("--text", help="the line's new words in the dub language (\"\" leaves the line silent)")
    words.add_argument("--text-stdin", action="store_true", help="read the new words from stdin (one line)")
    li.add_argument("--new-take", action="store_true", help="voice the line again with new seeds")
    sub.add_parser("check", parents=[common], help="check that the tools, models and server are in place")
    sub.add_parser("guide", parents=[common], help="the guide for AI agents driving localdub: the loop, files, events")
    from .models import EFFORT_LEVELS, EFFORTS, KINDS, TASKS
    mo = sub.add_parser("models", parents=[common],
                        help="which model does each task (text, vision, voice, hearing, separation), on which server; "
                             "change them, or try one")
    mo.add_argument("--server", metavar="NAME", help="add or change a server (--url, --kind, --key-stdin), or --remove it")
    mo.add_argument("--url", help="the server's address, e.g. http://gpu-box:9876 or https://api.example.com")
    mo.add_argument("--kind", choices=KINDS, help="llama-swap, or openai: any OpenAI-compatible server")
    mo.add_argument("--key-stdin", action="store_true", help="read the server's API key from stdin (empty: no key)")
    mo.add_argument("--remove", action="store_true", help="remove the --server")
    mo.add_argument("--set", nargs="+", metavar="TASK=SERVER/MODEL",
                    help=f"which model does a task ({', '.join(TASKS)}), e.g. text=main/gemma4-31b")
    mo.add_argument("--effort", nargs="+", metavar="USE=LEVEL",
                    help=f"how hard the text model thinks: {', '.join(EFFORTS)} = {'|'.join(EFFORT_LEVELS)}")
    mo.add_argument("--test", choices=list(TASKS), help="try a task with one small real request")
    c = sub.add_parser("channel", parents=[common], help="show a channel's settings, or change who speaks")
    c.add_argument("name", help="the channel's name")
    c.add_argument("--speaker", choices=["man", "woman", "none"], help="set who speaks ('none' = not set)")
    lg = sub.add_parser("languages", parents=[common], help="the languages localdub knows and can dub into")
    lg.add_argument("--apt", nargs="*", metavar="LANG",
                    help="instead: print the Debian packages with the dictionaries for these languages ('all': "
                         "every language) and for every channel's video language (for install scripts)")
    args = ap.parse_args(argv)
    events.configure(args.json)
    try:
        commands = {"run": cmd_run, "shorts": cmd_shorts, "voice": cmd_voice, "check": cmd_check,
                    "models": cmd_models, "guide": cmd_guide, "channel": cmd_channel, "languages": cmd_languages,
                    "line": cmd_line}
        rc = commands[args.cmd](args)
    except Exception as e:  # a bug, not a handled error: still end the event stream with a result
        events.note(error=f"{type(e).__name__}: {e}")
        events.finish(1)
        raise
    events.finish(rc)
    return rc
