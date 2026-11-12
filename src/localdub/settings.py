"""Machine settings (where models and tools are) and per-channel profiles (what was learned).

Settings: ~/dub/settings.json, optional. Every key has a default below, so the file
only needs what differs on your machine.

Profile: ~/dub/channels/<name>/profile.json, created on the first run and updated
from your review edits. Translation memory lives next to it in memory.jsonl.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

def home() -> Path:
    """Where channels, runs and settings.json live: $LOCALDUB_HOME, else ~/dub (read on every call)."""
    return Path(os.environ.get("LOCALDUB_HOME", Path.home() / "dub"))

DEFAULT_SETTINGS = {
    "llm_url": "http://127.0.0.1:9876",  # the model server: llama-swap (deploy/gpu/llama-swap.yaml)
    "api_key_env": "LLAMA_API_KEY",
    "api_key_file": "/etc/llama.env",
    "text_model": "gemma4-31b",
    "tts_model": "qwen3-tts",
    "spell_effort": "none",
    "translate_effort": "medium",
    "shorten_effort": "none",
    "auto_shorten": True,
    "chars_per_second": 15.0,
    "max_segment_seconds": 12.0,
    "max_speedup": 1.15,
    "seeds": [42, 7, 1234],
    "lufs": -14.0,
    "asr_model": "nemotron",        # transcription and the take check, on the model server
    "separation_model": "demucs",   # voice / music separation, on the model server
    # backend "http": the tool runs behind the model server (the model names above);
    # "local": localdub runs it on this machine (the paths and devices below) and frees the GPUs first
    "nemo": {
        "backend": "http",
        "bin": str(Path.home() / ".local/bin/nemo-speech"),
        "model": "/root/models/nemotron/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-AD-Q2_0_M.gguf",
        "config": "/root/models/nemotron/salm-16gb.yaml",
        "device": "cuda:0",
        "check_device": "cuda:1",
        "max_chunk_minutes": 25,
    },
    "demucs": {
        "backend": "http",
        "python": "/root/venvs/demucs-gpu/bin/python",
        "model": "htdemucs_ft",
        "device": "cuda:0",
    },
    "speaker": {  # speaker fingerprints (pip install -e .[voice]); used to pick the voice sample and takes
        "enabled": True,
        "model": "microsoft/wavlm-base-plus-sv",
        "device": "cpu",
    },
    "takes_per_line": 2,  # good takes to compare per line when speaker similarity is on (1 = first good take)
    "vision_model": "gemma4-31b-vision",
    "shorts": {
        "count": 6,
        "min_seconds": 25,
        "max_seconds": 60,
        "frame_every": 4,
        "select_effort": "low",
        "title": True,
    },
}

DEFAULT_PROFILE = {
    "source_lang": "bg",
    "target_lang": "en",
    "source_language_name": "Bulgarian",
    "target_language_name": "English",
    "hunspell_dict": "bg_BG",
    "style": "casual, friendly YouTube video; keep the creator's tone and humour",
    "voice_ref": "",
    "names": {},
    "glossary": {},
    "accepted_words": [],
    "corrections": {},
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    path = home() / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _merge(DEFAULT_SETTINGS, data)


class Profile:
    """A channel's learned knowledge: names, glossary, accepted words, corrections, voice."""

    def __init__(self, name: str):
        self.name = name
        self.dir = home() / "channels" / name
        self.path = self.dir / "profile.json"
        self.memory_path = self.dir / "memory.jsonl"
        saved = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.data = _merge(DEFAULT_PROFILE, saved)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def voice_path(self) -> Path | None:
        ref = self.data.get("voice_ref")
        if not ref:
            return None
        p = Path(ref)
        return p if p.is_absolute() else self.dir / p

    # --- translation memory ---------------------------------------------------------

    def memory(self) -> list[dict]:
        if not self.memory_path.exists():
            return []
        out = []
        for line in self.memory_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def remember(self, entries: list[dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.memory_path.open("a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
