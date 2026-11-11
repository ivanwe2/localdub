"""NeMo-Speech (Nemotron Transcribe) on a folder of WAVs: the model loads once for all files."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .media import ToolError


def transcribe_folder(nemo: dict, src: Path, out: Path, lang: str, device: str) -> dict[str, dict]:
    """{file stem: nemo-speech JSON (text, words[{word, start, end}])}"""
    if out.exists():
        shutil.rmtree(out)
    cmd = [nemo["bin"], "transcribe", str(src), "--model", nemo["model"], "--config", nemo["config"],
           "-l", lang, "--device", device, "--format", "json", "--output-dir", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise ToolError(f"nemo-speech failed:\n{p.stderr[-1500:]}")
    got = {}
    for f in out.rglob("*.json"):
        stem = f.name[: -len(".json")]
        stem = stem[: -len(".wav")] if stem.endswith(".wav") else stem  # 'x.json' or 'x.wav.json'
        try:
            got[stem] = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
    return got
