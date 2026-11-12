"""Nemotron transcription of a folder of WAVs, by one of two backends (settings["nemo"]["backend"]):

  http   through the model server (llama-swap -> nemo-speech serve), one request per file
  local  nemo-speech on this machine; the model loads once for all files
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .media import ToolError


def transcribe_folder(settings: dict, server, src: Path, out: Path, lang: str, device: str) -> dict[str, dict]:
    """{file stem: {"text": ..., "words": [{word, start, end}]}} for each WAV in `src`. Each result is also
    kept in `out` as <stem>.json, whichever backend made it. `device` matters only to the local backend."""
    nemo = settings["nemo"]
    if out.exists():
        shutil.rmtree(out)
    if nemo["backend"] == "http":
        out.mkdir(parents=True)
        got = {}
        for f in sorted(src.glob("*.wav")):
            got[f.stem] = server.transcribe(settings["asr_model"], f, lang)
            (out / f"{f.stem}.json").write_text(json.dumps(got[f.stem], ensure_ascii=False), encoding="utf-8")
        return got
    if nemo["backend"] != "local":
        raise ToolError(f"nemo.backend is {nemo['backend']!r}; use \"http\" or \"local\"")
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
