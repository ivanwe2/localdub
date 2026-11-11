"""Sentences from word timestamps, guarded spelling fixes, and translation.

What the channel profile contributes:
  corrections     word -> word fixes learned from your reviews, applied before the LLM
  names           correct spellings (and their English form for translation)
  accepted_words  words hunspell doesn't know but you confirmed are right
  glossary        source term -> target term, given to the translator when the term appears
  memory.jsonl    approved translations; the most similar ones are shown as examples
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile

SENTENCE_END = (".", "!", "?", "…")
CLAUSE_END = (",", ";", ":", "—", "–")


def core(word: str) -> str:
    return re.sub(r"^\W+|\W+$", "", word)


def text_of(words: list[dict]) -> str:
    return " ".join(w["word"] for w in words)


def ends_sentence(word: str) -> bool:
    return word.rstrip("\"'»)”").endswith(SENTENCE_END)


# --- sentences -------------------------------------------------------------------------------

def _split_long(ws: list[dict], max_s: float) -> list[list[dict]]:
    dur = ws[-1]["end"] - ws[0]["start"]
    if dur <= max_s or len(ws) < 4:
        return [ws]
    best, best_score = None, None
    for i in range(1, len(ws) - 2):
        left = ws[i]["end"] - ws[0]["start"]
        right = ws[-1]["end"] - ws[i + 1]["start"]
        if left < 1.5 or right < 1.5:
            continue
        score = (1.5 if ws[i]["word"].endswith(CLAUSE_END) else 0.0)
        score += min(ws[i + 1]["start"] - ws[i]["end"], 1.0) * 2.0
        score -= abs(left - right) / dur
        if best_score is None or score > best_score:
            best, best_score = i, score
    if best is None:
        return [ws]
    return _split_long(ws[: best + 1], max_s) + _split_long(ws[best + 1:], max_s)


def build_segments(words: list[dict], max_s: float, cps: float) -> list[dict]:
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if ends_sentence(w["word"]):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    segs = []
    for s in sents:
        for part in _split_long(s, max_s):
            segs.append({"words": part})
    for i, s in enumerate(segs, start=1):
        s["id"] = i
        retime(s)
    set_slots(segs, cps)
    return segs


def retime(seg: dict) -> None:
    ws = seg["words"]
    seg["start"], seg["end"] = ws[0]["start"], ws[-1]["end"]
    gaps = [(ws[j + 1]["start"] - ws[j]["end"], ws[j]["word"]) for j in range(len(ws) - 1)]
    seg["flags"] = [f"{g:.1f}s pause after '{w}'" for g, w in gaps if g > 2.0]


def set_slots(segs: list[dict], cps: float) -> None:
    for i, s in enumerate(segs):
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else s["end"] + 1.0
        s["slot"] = round(max(s["end"], nxt) - s["start"], 3)
        s["budget"] = max(12, int(s["slot"] * cps))


def replace_words(seg: dict, new_text: str) -> None:
    """Put edited text back on the segment's words, keeping timestamps.

    Same word count: each word keeps its own times. Different count: the
    segment's time span is shared out by word length.
    """
    new = new_text.split()
    old = seg["words"]
    if not new:
        return
    if len(new) == len(old):
        for w, n in zip(old, new):
            w["word"] = n
    else:
        start, end = old[0]["start"], old[-1]["end"]
        total = sum(len(n) for n in new)
        t, out = start, []
        for n in new:
            d = (end - start) * len(n) / total
            out.append({"word": n, "start": round(t, 3), "end": round(t + d, 3)})
            t += d
        seg["words"] = out
    retime(seg)


# --- spelling ------------------------------------------------------------------------------------

class Speller:
    """hunspell with the channel's accepted words and names as a personal dictionary."""

    def __init__(self, dict_name: str, extra_words: list[str]):
        self.dict_name = dict_name
        self.personal = tempfile.NamedTemporaryFile("w", suffix=".dic", delete=False, encoding="utf-8")
        self.personal.write("\n".join(sorted(set(extra_words))) + "\n")
        self.personal.close()

    def unknown(self, words) -> set[str]:
        words = sorted({w for w in words if w})
        if not words:
            return set()
        p = subprocess.run(["hunspell", "-d", self.dict_name, "-p", self.personal.name, "-i", "utf-8", "-l"],
                           input="\n".join(words), capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"hunspell failed: {p.stderr.strip()} (apt-get install hunspell hunspell-bg?)")
        return set(p.stdout.split())

    @staticmethod
    def is_misspelled(word: str, unknown: set[str]) -> bool:
        # hunspell reports only the failing part of a hyphenated word
        return word in unknown or any(part in unknown for part in word.split("-"))


SPELL_PROMPT = (
    "You proofread {lang} transcripts made by speech recognition. The recogniser writes words the way "
    "they sound, which causes spelling mistakes (for Bulgarian, e.g. 'ф' for 'в' as in \"фсички\" -> "
    "\"всички\", \"ефтино\" -> \"евтино\", or an extra 'й' as in \"найемите\" -> \"наемите\"). Colloquial "
    "spellings count as mistakes: use the standard spelling.\n"
    "Fix only words that are misspelled. Leave every correctly spelled word exactly as it is, including "
    "count forms after numbers. Do not guess names: change a name only to one from the list of known "
    "names, if one is given. Never add, remove, merge, split or reorder words, and never change grammar, "
    "style or punctuation. Each corrected line must have exactly the same number of words as the original.\n"
    'Reply with JSON only: {{"fixes": [{{"id": <line id>, "text": "<corrected line>"}}]}}, listing only the '
    'lines you changed. If nothing needs fixing, reply {{"fixes": []}}.'
)


def _is_name(words: list[str], i: int) -> bool:
    w = core(words[i])
    return bool(w) and w[0].isupper() and i > 0 and not ends_sentence(words[i - 1])


def spellcheck(segs, server, settings, profile) -> dict:
    """Apply learned corrections, then LLM fixes under the dictionary guard.

    Returns {seg_id: {"learned": [(old, new)], "fixes": [(old, new)], "review": [(old, new)], "unknown": [words]}}:
    learned = corrections from earlier reviews, fixes = new LLM fixes, review = guessed names not applied.
    """
    names = set(profile["names"])
    speller = Speller(profile["hunspell_dict"], list(profile["accepted_words"]) + list(names))
    corrections = profile["corrections"]
    report: dict[int, dict] = {s["id"]: {"learned": [], "fixes": [], "review": [], "unknown": []} for s in segs}

    # 1. corrections learned from earlier reviews (whole words, or parts of hyphenated words; punctuation kept)
    for s in segs:
        for w in s["words"]:
            c = core(w["word"])
            if not c:
                continue
            fixed = corrections.get(c) or "-".join(corrections.get(part, part) for part in c.split("-"))
            if fixed != c:
                new = w["word"].replace(c, fixed)
                report[s["id"]]["learned"].append((w["word"], new))
                w["word"] = new

    # 2. LLM proposals, accepted only for words the dictionary rejects
    unknown = speller.unknown(core(w["word"]) for s in segs for w in s["words"])
    by_id = {s["id"]: s for s in segs}
    lang = profile["source_language_name"]
    for start in range(0, len(segs), 40):
        chunk = segs[start:start + 40]
        user = "\n".join(f"{s['id']}: {text_of(s['words'])}" for s in chunk)
        if names:
            user = "Known names (correct spelling): " + ", ".join(sorted(names)) + "\n\n" + user
        reply = server.chat_json(settings["text_model"], SPELL_PROMPT.format(lang=lang), user,
                                 settings["spell_effort"], 4096, f"spelling {chunk[0]['id']}-{chunk[-1]['id']}")
        for fix in reply.get("fixes", []):
            try:
                s = by_id[int(fix.get("id"))]
            except (KeyError, TypeError, ValueError):
                continue
            old = [w["word"] for w in s["words"]]
            new = str(fix.get("text", "")).split()
            if new == old or len(new) != len(old):
                continue
            good, refused = [], 0
            for i, (a, b) in enumerate(zip(old, new)):
                if a == b:
                    continue
                if core(a) == core(b) or not Speller.is_misspelled(core(a), unknown):
                    refused += 1  # punctuation change, or a real word changed: not allowed
                else:
                    good.append(i)
            if refused > 1:
                continue  # the line was rewritten rather than proofread: trust none of it
            for i in good:
                a, b = old[i], new[i]
                if _is_name(old, i) and core(b) not in names:
                    report[s["id"]]["review"].append((a, b))  # a guessed name: suggest, don't apply
                else:
                    s["words"][i]["word"] = b
                    report[s["id"]]["fixes"].append((a, b))

    # 3. what the dictionary still doesn't know, per line
    final = speller.unknown(core(w["word"]) for s in segs for w in s["words"])
    for s in segs:
        seen = []
        for w in s["words"]:
            c = core(w["word"])
            if c and Speller.is_misspelled(c, final) and c not in seen:
                seen.append(c)
        report[s["id"]]["unknown"] = seen
        s["bg"] = text_of(s["words"])
    return report


# --- translation -----------------------------------------------------------------------------------

TRANSLATE_PROMPT = (
    "You translate a {src} YouTube video into {tgt} for a voice dub. The {tgt} will be spoken in the "
    "creator's own cloned voice, so write natural spoken {tgt} that sounds like him. Channel style: {style}. "
    "Do not translate word for word, and do not add or drop meaning.\n"
    "Each line is a sentence with a time slot and a character budget, roughly what fits when spoken. Stay "
    "within the budget: prefer shorter phrasing over long literal renderings. Write numbers out as words "
    "(\"two hundred thousand\", not \"200,000\"), so the length is honest. Translate each line on its own; do "
    "not move content between lines.\n"
    "Always use the glossary translations and the given name spellings. The examples are lines from this "
    "channel's earlier videos, approved by a human: follow their wording and tone where they fit. Lines "
    "marked as context are already translated; do not return them.\n"
    'Reply with JSON only: {{"lines": [{{"id": <line id>, "text": "<translation>"}}]}}, one entry for every '
    "line to translate, in order."
)

SHORTEN_PROMPT = (
    "Shorten each {tgt} dub line to fit its character budget when spoken, keeping its meaning, tone and any "
    "names. Write numbers as words. "
    'Reply with JSON only: {{"lines": [{{"id": <line id>, "text": "<shorter line>"}}]}}.'
)


def _stems(text: str) -> set[str]:
    """Crude stems for inflected languages: lowercase words of 3+ letters, cut to 5 characters."""
    return {w[:5] for w in re.findall(r"\w{3,}", text.lower())}


def glossary_hits(text: str, glossary: dict[str, str]) -> dict[str, str]:
    low = text.lower()
    words = re.findall(r"\w+", low)
    hits = {}
    for src, tgt in glossary.items():
        key = src.lower()
        stem = key[: max(4, len(key) - 2)]
        if " " in key:
            if key in low:
                hits[src] = tgt
        elif any(w.startswith(stem) for w in words):
            hits[src] = tgt
    return hits


def memory_examples(texts: list[str], memory: list[dict], k: int = 6) -> list[dict]:
    if not memory:
        return []
    want = set().union(*(_stems(t) for t in texts)) if texts else set()
    scored = []
    for e in memory:
        st = _stems(e["src"])
        if not st:
            continue
        overlap = len(st & want) / len(st)
        if overlap >= 0.5:
            scored.append((overlap + (0.2 if e.get("edited") else 0.0), e))
    scored.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for _, e in scored:
        if e["src"] not in seen:
            out.append(e)
            seen.add(e["src"])
        if len(out) >= k:
            break
    return out


def translate(segs, server, settings, profile, progress=None) -> None:
    """Fill s["en"] for every line. Lines that already have one (from an interrupted run) are kept;
    `progress()` is called after each request so the caller can save what's done."""
    src, tgt = profile["source_language_name"], profile["target_language_name"]
    system = TRANSLATE_PROMPT.format(src=src, tgt=tgt, style=profile["style"])
    memory = profile.memory()
    names = {k: v for k, v in profile["names"].items() if v}
    for start in range(0, len(segs), 20):
        chunk = segs[start:start + 20]
        context = segs[max(0, start - 3):start]
        todo = [s for s in chunk if not s.get("en")]
        for _ in range(3):
            if not todo:
                break
            texts = [s["bg"] for s in todo]
            gloss = {}
            for t in texts:
                gloss.update(glossary_hits(t, profile["glossary"]))
            parts = []
            if names:
                parts.append("Names: " + ", ".join(f"{a} = {b}" for a, b in names.items()))
            if gloss:
                parts.append("Glossary: " + ", ".join(f"{a} = {b}" for a, b in gloss.items()))
            ex = memory_examples(texts, memory)
            if ex:
                parts.append("Examples:\n" + "\n".join(f"{e['src']}\n   -> {e['tgt']}" for e in ex))
            if context:
                parts.append("Context (already translated):\n" + "\n".join(
                    f"{c['id']}: {c['bg']}\n   -> {c.get('en', '')}" for c in context))
            parts.append("Translate:\n" + "\n".join(
                f"{s['id']} [{s['slot']:.1f}s, max {s['budget']} chars]: {s['bg']}" for s in todo))
            reply = server.chat_json(settings["text_model"], system, "\n\n".join(parts),
                                     settings["translate_effort"], 16384,
                                     f"translating {todo[0]['id']}-{todo[-1]['id']}")
            got = {}
            for x in reply.get("lines", []):
                try:
                    got[int(x["id"])] = str(x.get("text") or x.get("en") or "").strip()
                except (KeyError, TypeError, ValueError):
                    continue
            for s in todo:
                if got.get(s["id"]):
                    s["en"] = got[s["id"]]
            todo = [s for s in chunk if not s.get("en")]
        for s in todo:
            s["en"] = "[MISSING]"
        if progress:
            progress()

    # one round of shortening for lines over budget; if it fails, the longer lines stay (and get sped up)
    over = [s for s in segs if len(s["en"]) > s["budget"] and s["en"] != "[MISSING]"]
    if over:
        shorten_to([(s, s["budget"]) for s in over], server, settings, profile, f"shortening {len(over)} lines")
    for s in segs:
        s["model_en"] = s["en"]


def shorten_to(items: list[tuple[dict, int]], server, settings, profile, label: str) -> list[dict]:
    """Ask for shorter versions of s["en"] within max_chars; keep only replies that are shorter.
    Returns the lines that changed. A failed request changes nothing."""
    from .llm import LLMError
    items = [(s, n) for s, n in items if len(s["en"]) >= 25 and len(s["en"].split()) >= 4]  # too short to cut
    if not items:
        return []
    user = "\n".join(f"{s['id']} [max {n} chars, now {len(s['en'])}]: {s['en']}" for s, n in items)
    try:
        reply = server.chat_json(settings["text_model"], SHORTEN_PROMPT.format(tgt=profile["target_language_name"]),
                                 user, settings["shorten_effort"], 8192, label)
    except LLMError as e:
        print(f"   shortening failed, keeping the longer lines ({e})", file=sys.stderr)
        return []
    by_id = {s["id"]: s for s, _ in items}
    changed = []
    for x in reply.get("lines", []):
        try:
            s = by_id[int(x["id"])]
        except (KeyError, TypeError, ValueError):
            continue
        new = str(x.get("text", "")).strip()
        if re.search(r"\d", new) and not re.search(r"\d", s["en"]):
            continue  # "Seven hundred!" -> "Seven 00!": digits are read out wrong, keep the words
        if new and len(new) < len(s["en"]):
            s["en"] = new
            changed.append(s)
    return changed
