"""The pipeline: one command, resumable, and it stops twice for your review.

    localdub run VIDEO --channel NAME [--start 1:00 --duration 2:30]
    localdub run NAME              continue a run (after a review, or after an error)
    localdub check                 is everything it needs in place?

Everything a run makes is kept in ~/dub/runs/<name>/, so rerunning continues where it stopped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from . import media, review
from .llm import LLMError, Server
from .mix import mix, mux, place, subtitles
from .settings import Profile, home, load_settings
from .text import (Speller, _is_name, build_segments, core, replace_words, shorten_to, spellcheck, text_of,
                   translate)
from .voice import RATE, cut_voice, voice_all, voice_candidates

WAIT = "wait"
LANG3 = {"bg": "bul", "en": "eng", "de": "deu", "es": "spa", "fr": "fra", "it": "ita", "ru": "rus", "pt": "por"}


def log(msg: str = "") -> None:
    print(msg, file=sys.stderr, flush=True)


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
        return read_json(self.p("segments.json"))

    @segs.setter
    def segs(self, value: list[dict]) -> None:
        write_json(self.p("segments.json"), value)


class Ctx:
    def __init__(self, settings, profile, server, no_review, rerun):
        self.settings, self.profile, self.server = settings, profile, server
        self.no_review, self.rerun = no_review, rerun


def waiting(ctx: Ctx, title: str, path: Path, extra: str = "") -> str:
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


def stage_separate(run: Run, ctx: Ctx):
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
    ctx.server.unload_all()
    nemo = ctx.settings["nemo"]
    total = run.state["seconds"]
    limit = nemo["max_chunk_minutes"] * 60
    cuts = [0.0]
    if total > limit:  # one request is capped (29 min on a 16 GB card): split at pauses in his speech
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
    log(f"   Nemotron on {nemo['device']}, {len(cuts) - 1} part(s)")
    got = transcribe_folder(nemo, work / "in", work / "out", ctx.profile["source_lang"], nemo["device"])
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
    """Apply your BG edits and remember what they teach.

    corrections   a misspelled word you fixed (or an automatic fix you kept) is fixed the same way next time;
                  a name you corrected mid-sentence too
    names         capitalised words mid-sentence that you wrote or left as they were
    accepted      words the dictionary didn't know that you left as they were
    An automatic fix you undid is forgotten and the original word accepted.
    """
    by_id = {s["id"]: s for s in segs}
    edits = {sid: t for sid, t in parsed["bg"].items() if sid in by_id and t.split()}
    corr, names = profile["corrections"], profile["names"]
    accepted = set(profile["accepted_words"])
    base = Speller(profile["hunspell_dict"], [])
    unknown = base.unknown({core(w) for sid, t in edits.items() for w in t.split() + by_id[sid]["bg"].split()})
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
        old, new = s["bg"].split(), text.split()
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
        if text != s["bg"]:
            replace_words(s, text)
            s["bg"] = text_of(s["words"])
            n["edited"] += 1
    profile["accepted_words"] = sorted(accepted)
    return n


def run_voiceprint(run: Run, embedder, segs: list[dict]):
    """His fingerprint from this video's separated vocals (cached in the run)."""
    from .speaker import voiceprint
    path = run.p("voiceprint.npy")
    if path.exists():
        return np.load(path)
    vp = voiceprint(embedder, segs, run.p("vocals.wav"))
    if vp is not None:
        np.save(path, vp)
    return vp


def score_candidates(run: Run, ctx: Ctx, segs: list[dict], cands: list[dict]) -> None:
    """Clone a few test sentences from each candidate sample; rank the samples by how much the clones
    sound like his real voice. Leaves the order alone if the speaker model isn't available."""
    import base64
    from .speaker import CALIBRATION, embed_files, get_embedder, similarity
    lines = CALIBRATION.get(ctx.profile["target_lang"])
    embedder = get_embedder(ctx.settings) if lines else None
    if embedder is None:
        return
    vp = run_voiceprint(run, embedder, segs)
    if vp is None:
        return
    log(f"   cloning {len(lines)} test sentences from each of {len(cands)} candidate samples to compare them")
    for k, c in enumerate(cands, start=1):
        sample_b64 = base64.b64encode(Path(c["path"]).read_bytes()).decode()
        files = []
        for i, line in enumerate(lines, start=1):
            raw, trim = run.p(f"voice-candidates/candidate-{k}-test-{i}.raw.wav"), run.p(f"voice-candidates/candidate-{k}-test-{i}.wav")
            if not trim.exists():
                raw.write_bytes(ctx.server.tts(ctx.settings["tts_model"], line, ctx.profile["target_lang"], sample_b64, 42))
                media.trim_silence(raw, trim, RATE)
                raw.unlink()
            files.append(trim)
        c["sim"] = round(float(np.mean([similarity(e, vp) for e in embed_files(embedder, files)])), 3)
        log(f"   candidate {k} ({media.mmss(c['start'])}-{media.mmss(c['end'])}): sounds like him {c['sim']:.2f}")
    cands.sort(key=lambda c: -c["sim"])


def set_voice(run: Run, profile: Profile, choice: str) -> str:
    dst = profile.dir / "voice.wav"
    profile.dir.mkdir(parents=True, exist_ok=True)
    choice = choice.strip()
    cands = read_json(run.p("voice-candidates.json")) if run.p("voice-candidates.json").exists() else []
    if choice.lower() in ("", "auto") or choice.isdigit():
        k = int(choice) if choice.isdigit() else 1
        cand = Path(cands[k - 1]["path"]) if 0 < k <= len(cands) else run.p("voice-sample.wav")
        if not choice or not cand.exists():
            raise RuntimeError("review 1 needs a voice sample: write a candidate's number, a stretch like "
                               "'voice: 01:20-01:38' or a WAV file's path on the voice line")
        shutil.copyfile(cand, dst)
        sim = f", sounds like him {cands[k - 1]['sim']:.2f}" if cands and cands[k - 1].get("sim") is not None else ""
        source = f"{run.name}, candidate {k} ({media.mmss(cands[k - 1]['start'])}-{media.mmss(cands[k - 1]['end'])}{sim})" \
            if 0 < k <= len(cands) else f"{run.name}, picked automatically"
    elif choice.lower().endswith(".wav"):
        f = Path(choice).expanduser()
        if not f.exists():
            raise RuntimeError(f"review 1: voice file {f} not found")
        media.ffmpeg("-i", f, "-ac", "1", "-ar", RATE, "-c:a", "pcm_s16le", dst)
        source = str(f)
    else:
        try:
            a, b = (media.parse_mmss(x) for x in choice.split("-", 1))
        except ValueError:
            raise RuntimeError(f"review 1: can't read the voice line '{choice}' (expected e.g. 01:20-01:38)") from None
        if not 5 <= b - a <= 30:
            raise RuntimeError("review 1: the voice stretch should be 5-30 seconds long (10-20 is best)")
        cut_voice(run.p("vocals.wav"), a, b, dst)
        source = f"{run.name} {choice}"
    profile["voice_ref"] = "voice.wav"
    profile["voice_from"] = source
    return source


def stage_review1(run: Run, ctx: Ctx):
    path = run.p("review-1-transcript.txt")
    segs = read_json(run.p("spelled.json"))
    report = {int(k): v for k, v in read_json(run.p("spelling.json")).items()}
    profile = ctx.profile
    has_voice = profile.voice_path is not None and profile.voice_path.exists()
    if not path.exists():
        voice = None
        if not has_voice:
            cands = voice_candidates(segs, run.p("vocals.wav"), run.p("music.wav"), k=3)
            if cands:
                run.p("voice-candidates").mkdir(exist_ok=True)
                for k, c in enumerate(cands, start=1):
                    c["path"] = str(run.p(f"voice-candidates/candidate-{k}.wav"))
                    cut_voice(run.p("vocals.wav"), c["start"], c["end"], Path(c["path"]))
                score_candidates(run, ctx, segs, cands)
                write_json(run.p("voice-candidates.json"), cands)
                voice = {"candidates": cands}
            else:
                voice = {"path": None}
        count = review.write_transcript_review(path, segs, report, voice)
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
        return waiting(ctx, "Review 1 of 2 is ready (the transcript)", path, what + ".")
    parsed = review.parse(path)
    if parsed["status"] != "approved":
        return waiting(ctx, "Review 1 of 2 is still waiting for you", path)
    n = learn_transcript(parsed, segs, report, profile)
    if parsed["voice"] is not None or not has_voice:
        log(f"   voice sample: {set_voice(run, profile, parsed['voice'] or 'auto')}")
    profile.save()
    run.segs = segs
    log(f"   {n['edited']} lines edited; learned {n['corrections']} corrections, {n['names']} names, "
        f"{n['accepted']} accepted words")


def stage_translate(run: Run, ctx: Ctx):
    segs = run.segs
    partial = run.p("translate-partial.json")  # lines done so far, kept if the stage is interrupted
    done = read_json(partial) if partial.exists() else {}
    for s in segs:
        for k in ("en", "model_en", "take", "shortened_from"):
            s.pop(k, None)
        d = done.get(str(s["id"]))
        if d and d["bg"] == s["bg"]:
            s["en"] = d["en"]
    if done:
        log(f"   {sum(1 for s in segs if s.get('en'))} lines already translated before the interruption")

    def save():
        write_json(partial, {str(s["id"]): {"bg": s["bg"], "en": s["en"]}
                             for s in segs if s.get("en") and s["en"] != "[MISSING]"})

    translate(segs, ctx.server, ctx.settings, ctx.profile, progress=save)
    run.segs = segs
    partial.unlink(missing_ok=True)
    over = sum(1 for s in segs if len(s["en"]) > s["budget"])
    missing = sum(1 for s in segs if s["en"] == "[MISSING]")
    log(f"   {len(segs)} lines; {over} still over their character budget" + (f", {missing} MISSING" if missing else ""))


def stage_review2(run: Run, ctx: Ctx):
    path = run.p("review-2-translation.txt")
    segs = run.segs
    if not path.exists():
        review.write_translation_review(path, segs)
        if ctx.no_review:
            log("   review skipped (--no-review); nothing is learned from this run")
            return None
        over = sum(1 for s in segs if len(s["en"]) > s["budget"])
        return waiting(ctx, "Review 2 of 2 is ready (the translation)", path,
                       f"{len(segs)} lines, {over} marked OVER (they will be sped up, up to "
                       f"{round((ctx.settings['max_speedup'] - 1) * 100)}%). An empty EN line is left silent.")
    parsed = review.parse(path)
    if parsed["status"] != "approved":
        return waiting(ctx, "Review 2 of 2 is still waiting for you", path)
    for s in segs:
        if s["id"] in parsed["en"]:
            s["en"] = parsed["en"][s["id"]]
    missing = [s["id"] for s in segs if s["en"].strip() == "[MISSING]"]
    if missing:
        raise RuntimeError(f"review 2: lines {missing} still say [MISSING]; write them (or leave EN empty) "
                           f"and set 'status: approved' again")
    today = dt.date.today().isoformat()
    entries = [{"src": s["bg"], "tgt": s["en"], "model_en": s["model_en"], "edited": s["en"] != s["model_en"],
                "video": run.name, "date": today} for s in segs if s["en"].strip()]
    ctx.profile.remember(entries)
    ctx.profile["glossary"].update(parsed["glossary"])
    ctx.profile.save()
    run.segs = segs
    log(f"   {sum(e['edited'] for e in entries)} lines edited; remembered {len(entries)} lines and "
        f"{len(parsed['glossary'])} glossary terms for this channel")


def stage_voice(run: Run, ctx: Ctx):
    segs = run.segs
    for s in segs:  # a redo starts from the translated text, not from an earlier automatic shortening
        if s.get("shortened_from"):
            s["en"] = s.pop("shortened_from")
    ctx.server.unload_all(keep=ctx.settings["tts_model"])  # the TTS model sits on GPU 0, the check on GPU 1
    log(f"   {ctx.settings['tts_model']} on GPU 0, checking each take with Nemotron on "
        f"{ctx.settings['nemo']['check_device']}")
    lang = ctx.profile["target_lang"]
    embedder = vp = None
    if ctx.settings.get("takes_per_line", 1) > 1:
        from .speaker import get_embedder
        embedder = get_embedder(ctx.settings)
        vp = run_voiceprint(run, embedder, segs) if embedder is not None else None
        if vp is not None:
            log(f"   comparing up to {ctx.settings['takes_per_line']} good takes per line by how much they sound like him")
    report = voice_all(segs, run.dir, ctx.server, ctx.settings, ctx.profile, lang, embedder, vp)
    over = [s for s in segs if s.get("take") and s["take"]["ok"] and "OVER" in s["take"]["fit"]]
    if over and ctx.settings["auto_shorten"]:
        # too long in every take: the text itself doesn't fit. Shorten to the length the voice needs, re-voice.
        log(f"   {len(over)} lines are too long in every take: shortening them to their measured length")
        speed = ctx.settings["max_speedup"]
        before = {s["id"]: s["en"] for s in over}
        items = [(s, max(8, int(len(s["en"]) * float(s["slot"]) * speed / s["take"]["spoken"] * 0.92)))
                 for s in over]
        changed = shorten_to(items, ctx.server, ctx.settings, ctx.profile, f"shortening {len(over)} lines to fit")
        for s in changed:
            s["shortened_from"] = before[s["id"]]
        if changed:
            log(f"   re-voicing {len(changed)} shortened lines")
            ctx.server.unload_all(keep=ctx.settings["tts_model"])
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
    log(f"   {first} lines kept the first take, {later} a later one (it passed, fitted or sounded more like him), "
        f"{failed} never passed the check; "
        f"{over} still over their slot")
    sims = sorted(r["sim"] for r in takes if r.get("sim") is not None)
    if sims:
        run.state["voice_similarity"] = {"median": sims[len(sims) // 2], "lowest_10pct": sims[len(sims) // 10]}
        log(f"   sounds like him: median {sims[len(sims) // 2]:.2f}, lowest 10% below {sims[len(sims) // 10]:.2f}")


def write_checklist(path: Path, segs: list[dict], video: Path) -> int:
    lines = [f"# Lines worth a listen in {video.name}", ""]
    count = 0
    for s in segs:
        notes = []
        t = s.get("take")
        if s.get("en", "").strip() and not t:
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
            lines += [f"[{s['id']}] {media.mmss(s['start'])}   " + "   ".join(notes), f"EN: {s.get('en', '')}", ""]
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
    mux(Path(run.state["video"]), run.p("dub-mix.wav"), out, LANG3.get(tgt_l, tgt_l), LANG3.get(src_l, src_l))
    subtitles(segs, run.p(f"{run.name}.{src_l}.srt"), run.p(f"{run.name}.{tgt_l}.srt"))
    n = write_checklist(run.p("check.txt"), segs, out)
    run.state["outputs"] = {"video": str(out), "subtitles": [str(run.p(f"{run.name}.{tgt_l}.srt")),
                                                            str(run.p(f"{run.name}.{src_l}.srt"))],
                            "check": str(run.p("check.txt")), "lines_to_check": n, "levels": levels}
    log(f"   mix at {levels['final_lufs']} LUFS; dub levelled to his original voice ({levels['voice_lufs']} LUFS)")


STAGES = [
    ("prep", "cut and extract the audio", stage_prep),
    ("separate", "split his voice from the music and effects", stage_separate),
    ("transcribe", "transcribe with Nemotron", stage_transcribe),
    ("spelling", "sentences and spelling", stage_spelling),
    ("review1", "your check of the transcript and voice sample", stage_review1),
    ("translate", "translate", stage_translate),
    ("review2", "your check of the translation", stage_review2),
    ("voice", "voice every line in his voice, checking each take", stage_voice),
    ("mix", "mix over the music, write the video and subtitles", stage_mix),
]
NAMES = [s[0] for s in STAGES]
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


def cmd_run(args) -> int:
    settings = load_settings()
    target = Path(args.video).expanduser()
    if not target.exists() and (home() / "runs" / args.video / "state.json").exists():
        run = Run(args.video)  # continue by run name
    else:
        if not target.exists():
            log(f"error: {target} not found (and no run called '{args.video}' in {home() / 'runs'})")
            return 1
        start = media.parse_mmss(args.start) if args.start else None
        length = media.parse_mmss(args.duration) if args.duration else None
        run = Run(args.name or default_name(target.resolve(), start, length))
        if not run.state:
            if not args.channel:
                log("error: a new run needs --channel NAME (the channel whose profile it uses and teaches)")
                return 1
            run.state = {"source": str(target.resolve()), "channel": args.channel, "start": start, "length": length,
                         "created": dt.datetime.now().isoformat(timespec="seconds"), "done": [], "timings": {}}
            run.save()
    st = run.state
    if args.channel and args.channel != st["channel"]:
        log(f"error: run '{run.name}' belongs to channel '{st['channel']}'; use --name for a separate run")
        return 1
    if args.redo:
        if args.redo not in NAMES:
            log(f"error: --redo takes one of: {', '.join(NAMES)}")
            return 1
        for name in NAMES[NAMES.index(args.redo):]:
            if name in st["done"]:
                st["done"].remove(name)
            for f in STAGE_FILES.get(name, []):
                p = run.p(f)
                shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
        run.save()
    profile = Profile(st["channel"])
    ctx = Ctx(settings, profile, Server(settings), args.no_review, f"localdub run {run.name}")
    log(f"localdub: run '{run.name}' (channel '{st['channel']}') in {run.dir}")
    for name, what, fn in STAGES:
        if name in st["done"]:
            continue
        log(f"\n== {name}: {what}")
        t0 = time.monotonic()
        try:
            result = fn(run, ctx)
        except (media.ToolError, LLMError, RuntimeError, OSError) as e:
            run.save()
            log(f"\nerror in stage '{name}': {e}")
            log(f"Fix the cause, then continue with: {ctx.rerun}")
            return 1
        if result == WAIT:
            run.save()
            return 3
        took = round(time.monotonic() - t0, 1)
        st["done"].append(name)
        st["timings"][name] = took
        run.save()
        log(f"   ({took:.0f}s)")
    if args.shorts:
        for lang in ("original", "dub"):
            t0 = time.monotonic()
            rc = make_shorts(run, settings, lang, auto=args.shorts)
            if rc != 0:
                return rc
            st["timings"][f"shorts-{lang}"] = round(time.monotonic() - t0, 1)
            run.save()
    out = st["outputs"]
    machine = sum(v for k, v in st["timings"].items() if not k.startswith("review"))
    log("\n== done")
    log(f"   video:      {out['video']}")
    log(f"   subtitles:  {out['subtitles'][0]}  (and {Path(out['subtitles'][1]).name})")
    log(f"   check list: {out['check']}  ({out['lines_to_check']} lines worth a listen)")
    for lang, files in (st.get("shorts") or {}).items() if args.shorts else []:
        log(f"   Shorts ({lang}): " + ", ".join(Path(f).name for f in files))
    log("   time per stage: " + ", ".join(f"{k} {v:.0f}s" for k, v in st["timings"].items()))
    log(f"   machine time in total: {machine / 60:.1f} min")
    return 0


def cmd_check(args) -> int:
    settings = load_settings()
    ok = True

    def line(good: bool, what: str, hint: str = ""):
        nonlocal ok
        ok &= good
        log(f"  {'ok  ' if good else 'FAIL'}  {what}" + ("" if good or not hint else f"   -> {hint}"))

    cfg = home() / "settings.json"
    log(f"localdub check (settings: {cfg}{'' if cfg.exists() else ', defaults'})")
    for tool in ("ffmpeg", "ffprobe", "hunspell"):
        line(shutil.which(tool) is not None, tool, f"apt-get install {'ffmpeg' if tool != 'hunspell' else 'hunspell hunspell-bg'}")
    try:
        Speller("bg_BG", []).unknown(["къща"])
        line(True, "hunspell bg_BG dictionary")
    except Exception as e:  # noqa: BLE001
        line(False, "hunspell bg_BG dictionary", f"apt-get install hunspell-bg ({e})")
    nemo = settings["nemo"]
    fix = "set the right path in ~/dub/settings.json (see README, Settings)"
    line(Path(nemo["bin"]).exists() or shutil.which(nemo["bin"]) is not None, f"nemo-speech: {nemo['bin']}", fix)
    line(Path(nemo["model"]).exists(), f"Nemotron model: {nemo['model']}", fix)
    line(Path(nemo["config"]).exists(), f"Nemotron config: {nemo['config']}", fix)
    d = settings["demucs"]
    try:
        out = media.run(d["python"], "-c", "import demucs, torch; print(torch.cuda.is_available())").strip()
        line(out == "True", f"Demucs + CUDA torch in {d['python']}", "torch can't see the GPU")
    except (media.ToolError, OSError) as e:
        line(False, f"Demucs in {d['python']}", str(e).splitlines()[0])
    from .llm import api_key
    line(bool(api_key(settings)), f"API key (${settings['api_key_env']} or {settings['api_key_file']})",
         "set it in the environment or in that file")
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
    try:
        models = Server(settings).models()
        line(True, f"llama server at {settings['llm_url']}")
        for m in (settings["text_model"], settings["tts_model"], settings["vision_model"]):
            line(m in models, f"preset {m}", "add it to ~/llama/models.ini (see README)")
    except LLMError as e:
        line(False, f"llama server at {settings['llm_url']}", str(e))
    log("all good" if ok else "fix the FAIL lines above")
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
        server = Server(settings)
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
            vision = sh.look(sheets, vision_json, server, settings["vision_model"])
            cands = sh.select(segs, vision, mot, server, settings, lang, language)
        except (media.ToolError, LLMError, RuntimeError, OSError) as e:
            log(f"\nerror: {e}")
            log(f"Fix the cause, then run again (finished steps are kept): {rerun}")
            return 1
        if not cands:
            log(f"error: no usable candidates came back; try again with: {rerun} --redo select")
            return 1
        write_json(cand_json, cands)
        sh.write_candidates(cand_txt, rerun, cands, segs, lang, auto=min(auto, len(cands)))
        log(f"   {len(cands)} candidates, strongest first: {cand_txt}")
    status, picked = sh.read_candidates(cand_txt)
    if status != "approved" or not picked:
        log(f"\n== Pick your {language} Shorts\n   {cand_txt}")
        log("   Mark the ones to render with [x], set 'status: approved', save, then run:")
        log(f"   {rerun}")
        return 3
    video = Path(run.state["video"] if lang == "original" else run.state["outputs"]["video"])
    log(f"   rendering {len(picked)} {language} Shorts from {video.name}")
    made = []
    for pick in picked:
        out = out_dir / f"short-{pick['n']}-{sh.slug(pick['title'])}.mp4"
        t0 = time.monotonic()
        try:
            sh.render(video, segs, pick, out, out_dir, settings["shorts"]["title"], lang)
        except (media.ToolError, RuntimeError) as e:
            log(f"error: {e}")
            return 1
        made.append(str(out))
        log(f"   {out.name}: {media.duration(out):.0f}s, rendered in {time.monotonic() - t0:.0f}s")
    run.state.setdefault("shorts", {})[lang] = made
    run.save()
    return 0


def cmd_shorts(args) -> int:
    run = Run(args.name)
    if not run.state or "mix" not in run.state.get("done", []):
        log(f"error: run '{args.name}' isn't finished yet (localdub run {args.name})")
        return 1
    rc = make_shorts(run, load_settings(), args.lang, args.auto, args.redo)
    if rc == 0:
        log("\n== done")
        for f in run.state["shorts"][args.lang]:
            log(f"   {f}")
    return rc


def main(argv=None) -> int:
    from . import __version__
    ap = argparse.ArgumentParser(prog="localdub", description="Dub a video into another language in the creator's own voice.")
    ap.add_argument("--version", action="version", version=f"localdub {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start or continue a run")
    r.add_argument("video", help="the video file, or the name of a run to continue")
    r.add_argument("--channel", help="channel profile to use and teach (needed for a new run)")
    r.add_argument("--start", help="only dub an excerpt starting here (mm:ss or seconds)")
    r.add_argument("--duration", help="excerpt length (mm:ss or seconds)")
    r.add_argument("--name", help="run name (default: from the file name)")
    r.add_argument("--no-review", action="store_true", help="skip both reviews (nothing is learned)")
    r.add_argument("--redo", metavar="STAGE", help=f"redo from this stage on: {', '.join(NAMES)}")
    r.add_argument("--shorts", type=int, default=0, metavar="N",
                   help="when the video is done, also make the top N Shorts in both languages, without stopping")
    s = sub.add_parser("shorts", help="propose Shorts from a finished run, then render the ones you pick")
    s.add_argument("name", help="the run's name")
    s.add_argument("--lang", choices=["dub", "original"], default="dub",
                   help="dub: the dubbed video (default); original: the creator's own audio and language")
    s.add_argument("--auto", type=int, default=0, metavar="N", help="render the top N candidates without stopping")
    s.add_argument("--redo", choices=["vision", "select"], help="look at the video again / propose new candidates")
    sub.add_parser("check", help="check that the tools, models and server are in place")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "shorts": cmd_shorts, "check": cmd_check}[args.cmd](args)
