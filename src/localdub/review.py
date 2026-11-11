"""Review files: plain text you edit, then mark 'status: approved' and rerun.

Review 1 (transcript) lists only lines with automatic fixes, name suggestions or words
the dictionary doesn't know, plus the voice sample. Review 2 (translation) lists every
line, plus a glossary section.
"""

from __future__ import annotations

import re
from pathlib import Path

from .media import mmss

_BLOCK = re.compile(r"^\[(\d+)\]")


def _read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").split("\n")


def status(path: Path) -> str:
    for line in _read(path):
        if line.strip().lower().startswith("status:"):
            return line.split(":", 1)[1].strip().lower()
    return "pending"


def write_transcript_review(path: Path, segs: list[dict], report: dict, voice: dict | None) -> int:
    lines = [
        "status: pending",
        "# Review 1 of 2: the transcript.",
        "# Fix spelling in the BG lines below; keep the [id] lines as they are. Only lines with automatic",
        "# fixes, name suggestions or words the dictionary doesn't know are listed; the rest looked clean",
        "# (fixes this channel already learned from you are applied without asking).",
        "# Unknown words you leave unchanged are remembered as correct for this channel.",
        "# When done: change the first line to 'status: approved', save, and rerun the same command.",
        "",
    ]
    if voice and voice.get("candidates"):
        scored = any(c.get("sim") is not None for c in voice["candidates"])
        lines.append("# Voice sample: candidates from this video, " + (
            "ranked by how much clones made from each sound like him (1.00 = identical):" if scored else
            "cleanest first (install the speaker model to rank them by likeness):"))
        for k, c in enumerate(voice["candidates"], start=1):
            sim = f"   sounds like him: {c['sim']:.2f}" if c.get("sim") is not None else ""
            lines.append(f"#   {k}. {mmss(c['start'])}-{mmss(c['end'])}{sim}   {c['path']}")
        lines += [
            "#   Listen to them. 'voice: auto' takes number 1; or write a number (voice: 2), a stretch of this",
            "#   video (voice: 01:20-01:38, 10-20 s of clear speech) or a WAV file's path.",
            "voice: auto",
            "",
        ]
    elif voice and voice.get("path"):
        lines += [
            f"# Voice sample: {voice['path']}",
            f"#   {mmss(voice['start'])}-{mmss(voice['end'])}, sentences {', '.join(map(str, voice['ids']))}.",
            "#   Listen to it. To use another stretch of this video instead, change the next line, e.g.",
            "#   'voice: 01:20-01:38' (10-20 s of clear speech, whole sentences), or give a WAV file's path.",
            "voice: auto",
            "",
        ]
    elif voice:
        lines += [
            "# Voice sample: no clean stretch was found automatically. Give 10-20 s of this video where he",
            "#   speaks clearly in whole sentences, e.g. 'voice: 01:20-01:38', or a WAV file's path.",
            "voice: ",
            "",
        ]
    count = 0
    for s in segs:
        r = report.get(s["id"], {})
        notes = []
        if r.get("learned"):
            notes.append("learned: " + ", ".join(f"{a} -> {b}" for a, b in r["learned"]))
        if r.get("fixes"):
            notes.append("fixed: " + ", ".join(f"{a} -> {b}" for a, b in r["fixes"]))
        if r.get("review"):
            notes.append("name? " + ", ".join(f"{a} -> {b} (not applied)" for a, b in r["review"]))
        if r.get("unknown"):
            notes.append("unknown: " + ", ".join(r["unknown"]))
        if s.get("flags"):
            notes.append("; ".join(s["flags"]))
        if not (r.get("fixes") or r.get("review") or r.get("unknown")):
            continue
        count += 1
        lines += [f"[{s['id']}] {mmss(s['start'])}   " + "   ".join(notes), f"BG: {s['bg']}", ""]
    if count == 0:
        lines.insert(8, "# Nothing to check: every word is known and nothing was changed automatically.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


def write_translation_review(path: Path, segs: list[dict]) -> None:
    lines = [
        "status: pending",
        "# Review 2 of 2: the translation.",
        "# Edit the EN lines; keep the [id] and BG lines. Every approved line is remembered for this channel and",
        "# shown to the translator as an example in future videos, so your fixes carry over.",
        "# Terms the translator should always use go under 'glossary:', one per line, e.g. хлебарки = cockroaches",
        "# When done: change the first line to 'status: approved', save, and rerun the same command.",
        "",
        "glossary:",
        "",
    ]
    for s in segs:
        n = len(s["en"])
        mark = "   OVER" if n > s["budget"] else ""
        flags = f"   [{'; '.join(s['flags'])}]" if s.get("flags") else ""
        lines += [f"[{s['id']}] {mmss(s['start'])}   slot {s['slot']:.1f}s   {n}/{s['budget']} chars{mark}{flags}",
                  f"BG: {s['bg']}", f"EN: {s['en']}", ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse(path: Path) -> dict:
    """{'status', 'voice', 'glossary': {src: tgt}, 'bg': {id: text}, 'en': {id: text}}"""
    out = {"status": "pending", "voice": None, "glossary": {}, "bg": {}, "en": {}}
    current = None
    in_glossary = False
    for raw in _read(path):
        line = raw.strip()
        low = line.lower()
        if low.startswith("status:"):
            out["status"] = line.split(":", 1)[1].strip().lower()
            continue
        if low.startswith("voice:"):
            out["voice"] = line.split(":", 1)[1].strip()
            continue
        if low.startswith("glossary:"):
            in_glossary = True
            continue
        m = _BLOCK.match(line)
        if m:
            current = int(m.group(1))
            in_glossary = False
            continue
        if not line or line.startswith("#"):
            continue
        if in_glossary and "=" in line:
            a, b = line.split("=", 1)
            if a.strip() and b.strip():
                out["glossary"][a.strip()] = b.strip()
            continue
        if current is not None and line.upper().startswith("BG:"):
            out["bg"][current] = line[3:].strip()
        elif current is not None and line.upper().startswith("EN:"):
            out["en"][current] = line[3:].strip()
    return out
