"""Which model does which task, and on which server.

localdub has five tasks for models: text, vision, voice, hearing and separation. Each is done by one
model on one server. A server is a URL with an API key and a kind:

  llama-swap  llama-swap on a GPU box (deploy/gpu/llama-swap.yaml): chat and transcription at /v1/…,
              the voice clone at /upstream/<model>/tts, separation at /upstream/<model>/separate;
              localdub can see what's loaded and unload it.
  openai      any OpenAI-compatible server: chat and transcription at /v1/…; for the voice clone a
              llama.cpp TTS server itself (<url>/tts), for separation deploy/gpu/demucs_server.py
              itself (<url>/separate).

Settings (settings.json), both optional; without them everything is on the server `main`, made from
the older keys (llm_url, api_key_env, api_key_file) and the models from text_model, vision_model,
tts_model, asr_model and separation_model:

  "servers": {"cloud": {"url": "https://…", "kind": "openai"}}
  "tasks": {"text": {"server": "cloud", "model": "…"}}

A server's key: from keys.json in the localdub home (written by `localdub models --server NAME
--key-stdin`, readable only by its owner), else from its `key_env` variable or `key_file`
(KEY=value lines), as llm_url's key always was. Keys are never printed.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import shutil
import struct
import time
import urllib.parse
import wave
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .settings import home

KINDS = ("llama-swap", "openai")
EFFORT_LEVELS = ("default", "none", "low", "medium", "high")  # "default": the server decides (not sent)
# what thinks how hard, by use: the settings key of each (shorts' is inside "shorts")
EFFORTS = {"spelling": "spell_effort", "translate": "translate_effort", "shorten": "shorten_effort",
           "shorts": "select_effort"}


@dataclass(frozen=True)
class Task:
    name: str
    title: str
    setting: str  # the older settings key with its model ("text_model")
    what: str
    used_by: tuple[str, ...]
    needs: str
    examples: tuple[str, ...]
    snippet: str  # the reference box's llama-swap.yaml entry for the default model


TASKS: dict[str, Task] = {t.name: t for t in [
    Task(
        "text", "Text", "text_model",
        "Fixes the transcript's spelling, translates it sentence by sentence within each line's length "
        "budget, shortens lines that don't fit, and chooses the Shorts.",
        ("spelling", "translate", "voice (shortening)", "Shorts (choosing)"),
        "A chat model behind /v1/chat/completions that follows instructions and answers in JSON, strong in "
        "the video's language and the dub's. Bigger is better here: the translation is the dub. Thinking "
        "(reasoning_effort) helps the translation most; each use has its own level.",
        ("Gemma 4 31B on llama.cpp (what localdub is tuned with)", "Qwen3 32B or larger",
         "a cloud model behind an OpenAI-compatible API"),
        "gemma4-31b:\n  name: Gemma 4 31B\n  cmd: |\n    ${llama-server} --no-mmproj\n"
        "    --hf-repo unsloth/gemma-4-31B-it-GGUF:UD-Q4_K_XL --ctx-size 32768\n",
    ),
    Task(
        "vision", "Vision", "vision_model",
        "Watches the video for Shorts: a frame every 4 s, four to an image, rated for how interesting it is "
        "to watch.",
        ("Shorts (watching)",),
        "A chat model that takes images (OpenAI image_url parts) behind /v1/chat/completions.",
        ("Gemma 4 31B with its vision projector (llama.cpp)", "Qwen3-VL", "a cloud vision model"),
        "gemma4-31b-vision:\n  name: Gemma 4 31B vision\n  cmd: |\n    ${llama-server}\n"
        "    --hf-repo unsloth/gemma-4-31B-it-GGUF:UD-Q4_K_XL --ctx-size 8192\n",
    ),
    Task(
        "voice", "Voice", "tts_model",
        "Speaks every line of the dub in the speaker's cloned voice, from a 10-20 s sample of the video.",
        ("voice", "voice samples"),
        "Qwen3-TTS on llama.cpp's TTS server: POST /tts {input, lang, seed, speaker_ref_b64} returns a WAV. "
        "Behind llama-swap it's called at /upstream/<model>/tts; on an openai-kind server at <url>/tts. "
        "What a channel can be dubbed into is the setting tts_languages.",
        ("Qwen3-TTS 1.7B Base (llama.cpp)",),
        "qwen3-tts:\n  name: Qwen3-TTS 1.7B Base\n  cmd: |\n    ${llama-server}\n"
        "    --hf-repo ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF:Q8_0 --ctx-size 4096\n",
    ),
    Task(
        "hearing", "Hearing", "asr_model",
        "Transcribes the video with every word's time, and listens to each voiced take to check it says "
        "the line.",
        ("transcribe", "voice (the take check)"),
        "Speech to text behind /v1/audio/transcriptions with response_format=verbose_json and word "
        "timestamps (timestamp_granularities[]=word), in the video's language and the dub's. (Or run "
        "Nemotron on this machine: settings nemo.backend = local.)",
        ("Nemotron 3.5 Transcribe (nemo-speech serve)", "Whisper large-v3 (Speaches, faster-whisper-server)"),
        "nemotron:\n  name: Nemotron 3.5 Transcribe\n  cmd: |\n    ${nemo-speech} serve --host 127.0.0.1 --port ${PORT}\n"
        "    --asr-model …/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-AD-Q2_0_M.gguf --config …/salm-16gb.yaml\n"
        "  checkEndpoint: /v1/models\n  useModelName: salm\n",
    ),
    Task(
        "separation", "Separation", "separation_model",
        "Splits the speaker's voice from the music and effects, so the dub replaces only the voice.",
        ("separate",),
        "deploy/gpu/demucs_server.py: POST /separate with a WAV returns a ZIP of vocals.wav and "
        "no_vocals.wav. Behind llama-swap it's called at /upstream/<model>/separate; on an openai-kind "
        "server at <url>/separate. (Or run Demucs on this machine: settings demucs.backend = local.)",
        ("Demucs htdemucs_ft (deploy/gpu/demucs_server.py)",),
        "demucs:\n  name: Demucs htdemucs_ft\n  cmd: |\n    ${demucs-python} ${localdub-dir}/deploy/gpu/demucs_server.py --port ${PORT}\n"
        "    --model htdemucs_ft --device cuda:0\n  checkEndpoint: /health\n",
    ),
]}


def servers(settings: dict) -> dict[str, dict]:
    """Every server by name: `main` from the older keys, then settings' `servers` (which may redefine it)."""
    out = {"main": {"url": settings["llm_url"], "kind": "llama-swap",
                    "key_env": settings.get("api_key_env", "LLAMA_API_KEY"),
                    "key_file": settings.get("api_key_file", "/etc/llama.env")}}
    for name, cfg in (settings.get("servers") or {}).items():
        out[name] = {**out.get(name, {"kind": "openai"}), **cfg}
    for cfg in out.values():
        cfg["url"] = str(cfg.get("url", "")).rstrip("/")
    return out


def task(settings: dict, name: str) -> dict:
    """{"server": …, "model": …} for a task: settings' `tasks`, else `main` and the older model key."""
    t = TASKS[name]
    return {"server": "main", "model": settings.get(t.setting, ""), **((settings.get("tasks") or {}).get(name) or {})}


def keys_path() -> Path:
    return home() / "keys.json"


def stored_keys() -> dict[str, str]:
    try:
        return json.loads(keys_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def key(name: str, cfg: dict) -> str:
    """A server's API key: stored by localdub, else its environment variable, else its key file."""
    if stored := stored_keys().get(name):
        return stored
    env = cfg.get("key_env")
    if env and os.environ.get(env):
        return os.environ[env]
    if env and cfg.get("key_file"):
        try:
            for line in open(cfg["key_file"], encoding="utf-8"):
                k, _, v = line.strip().partition("=")
                if k.strip() == env:
                    return v.strip().strip("'\"")
        except OSError:
            pass
    return ""


def store_key(name: str, value: str) -> None:
    """Saves (or with "" removes) a server's key in keys.json, readable only by its owner."""
    keys = stored_keys()
    if value:
        keys[name] = value
    else:
        keys.pop(name, None)
    path = keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=1)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localdomain")


def is_remote(url: str) -> bool:
    """Is this server off this machine and its local network? (Then frames, transcripts and voice
    samples leave the house.) Names without a dot and local suffixes count as local."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host or host == "localhost" or ("." not in host and ":" not in host) or host.endswith(LOCAL_SUFFIXES):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # a public name
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10"))


# --- changing the settings ---------------------------------------------------------------------------

def _settings_file() -> Path:
    return home() / "settings.json"


def _read_saved() -> dict:
    path = _settings_file()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_saved(data: dict) -> None:
    path = _settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or "").lower(), parts.port


def save_server(name: str, url: str | None, kind: str | None, settings: dict, new_key: bool = False) -> bool:
    """Adds or changes a server in settings.json (only what's given; the rest of the file stays).
    A server that moves to another address (scheme, host or port) forgets its saved key, unless a
    new one comes with the change: a key is never sent to a place it wasn't given for. Returns
    whether a saved key was forgotten."""
    before = servers(settings).get(name, {}).get("url")
    data = _read_saved()
    cfg = data.setdefault("servers", {}).setdefault(name, {})
    if url is not None:
        cfg["url"] = url.rstrip("/")
    if kind is not None:
        cfg["kind"] = kind
    _write_saved(data)
    moved = url is not None and before is not None and _origin(before) != _origin(url)
    if moved and not new_key and name in stored_keys():
        store_key(name, "")
        return True
    return False


def remove_server(name: str, settings: dict) -> str | None:
    """Removes a server and its key; refused (the reason) while a task uses it, or for `main`."""
    if name == "main":
        return "the server 'main' stays (it's llm_url); point it somewhere else instead"
    users = [t for t in TASKS if task(settings, t)["server"] == name]
    if users:
        return f"server '{name}' does {', '.join(users)}: give those tasks another server first"
    data = _read_saved()
    (data.get("servers") or {}).pop(name, None)
    _write_saved(data)
    store_key(name, "")
    return None


def save_task(name: str, server: str, model: str) -> None:
    data = _read_saved()
    data.setdefault("tasks", {})[name] = {"server": server, "model": model}
    _write_saved(data)


def save_effort(use: str, level: str) -> None:
    data = _read_saved()
    if use == "shorts":
        data.setdefault("shorts", {})["select_effort"] = level
    else:
        data[EFFORTS[use]] = level
    _write_saved(data)


def effort(settings: dict, use: str) -> str:
    return settings["shorts"]["select_effort"] if use == "shorts" else settings[EFFORTS[use]]


# --- ready-made setups ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Preset:
    name: str  # also the server's name it makes
    title: str
    what: str
    url: str | None  # None: the server main, with the models from the older settings keys
    kind: str
    keys_at: str  # where to get a key ("" for none)
    tasks: dict  # task → the model the setup gives it
    suggested: dict  # task → [(model, why)]: good choices to try, for comparing


# OpenRouter's model names, as https://openrouter.ai/api/v1/models listed them in November 2026
PRESETS: dict[str, Preset] = {p.name: p for p in [
    Preset(
        "openrouter", "OpenRouter",
        "One key for hundreds of hosted models, paid per use: frontier models to compare with your own. Text and "
        "vision only: the voice clone, hearing and separation stay on your GPUs. The transcript, the translation "
        "and frames of the video go to OpenRouter and the model's provider.",
        "https://openrouter.ai/api", "openai", "https://openrouter.ai/keys",
        {"text": "anthropic/claude-sonnet-5.5", "vision": "google/gemini-4-hydrogen"},
        {"text": [("anthropic/claude-opus-5.5", "the strongest translations, at the highest price"),
                  ("anthropic/claude-sonnet-5.5", "strong translations at half Opus's price (this setup's choice)"),
                  ("openai/gpt-6.2-sol", "OpenAI's current all-rounder"),
                  ("google/gemini-4-hydrogen", "quick, and cheaper"),
                  ("anthropic/claude-haiku-5.5", "cheap and fast: a few cents per video"),
                  ("deepseek/deepseek-v4.1-flash", "the cheapest: about a cent per video")],
         "vision": [("google/gemini-4-hydrogen", "sharp eyes at a low price (this setup's choice)"),
                    ("anthropic/claude-sonnet-5.5", "careful descriptions"),
                    ("openai/gpt-6-luna", "cheap"),
                    ("qwen/qwen3.8-flash", "the cheapest")]},
    ),
    Preset(
        "local", "Your own models",
        "Text and vision back on your own server (main), with the models localdub is tuned with.",
        None, "llama-swap", "", {}, {},
    ),
]}


def preset_tasks(p: Preset, settings: dict) -> dict:
    """The models a setup gives its tasks; "local" takes them from the settings' older keys."""
    if p.url is None:
        return {t: settings[TASKS[t].setting] for t in ("text", "vision")}
    return dict(p.tasks)


def apply_preset(name: str, settings: dict, new_key: str | None = None) -> str | None:
    """Sets up a ready-made setup: its server (with the key, if one is given) and its tasks' models.
    None when done, else why not (an unknown setup, or a hosted one without a key)."""
    p = PRESETS.get(name)
    if p is None:
        return f"no ready-made setup {name!r}; there are: {', '.join(PRESETS)}"
    server = "main"
    if p.url is not None:
        server = p.name
        save_server(server, p.url, p.kind, settings, new_key=new_key is not None)
        if new_key is not None:
            store_key(server, new_key)
        if not key(server, servers(settings).get(server, {})):
            return f"{p.title} needs a key: get one at {p.keys_at}, then give it with --key-stdin"
    for t, model in preset_tasks(p, settings).items():
        save_task(t, server, model)
    return None


def _price(m: dict) -> dict | None:
    """A listed model's price in USD per million tokens, where the server says (OpenRouter's "pricing")."""
    try:
        p = m["pricing"]
        return {"in": round(float(p["prompt"]) * 1e6, 3), "out": round(float(p["completion"]) * 1e6, 3)}
    except (KeyError, TypeError, ValueError):
        return None


# --- what the servers offer, and trying a task ----------------------------------------------------------

def overview(settings: dict) -> dict:
    """Everything the Models screen and `localdub models` show: the servers (reachable? what they
    list), the tasks (their model, what they need), the thinking levels. Keys only as "set"."""
    from .llm import LLMError, Server
    configs = servers(settings)
    rows = []
    for name, cfg in configs.items():
        server = Server(cfg["url"], key(name, cfg), cfg.get("kind", "openai"), name)
        row = {"name": name, "url": server.url, "kind": server.kind, "key_set": bool(server.key),
               "key_source": "stored" if name in stored_keys() else (cfg.get("key_env") or None),
               "remote": is_remote(server.url), "reachable": False, "error": None, "models": []}
        try:
            row["models"] = [{"id": m["id"], "name": m.get("name") or None, "description": m.get("description") or None,
                              "price": _price(m)} for m in server.listed(timeout=8)]
            row["reachable"] = True
        except LLMError as e:
            row["reachable"] = ": HTTP " in str(e)  # it answered, but lists no models (a TTS or Demucs server)
            row["error"] = str(e)[:300]
        rows.append(row)
    tasks = []
    for name, t in TASKS.items():
        cfg = task(settings, name)
        listed = next((r["models"] for r in rows if r["name"] == cfg["server"]), [])
        tasks.append({"task": name, "title": t.title, "what": t.what, "used_by": list(t.used_by), "needs": t.needs,
                      "examples": list(t.examples), "snippet": t.snippet, "server": cfg["server"], "model": cfg["model"],
                      "listed": any(m["id"] == cfg["model"] for m in listed)})
    presets = []
    for p in PRESETS.values():
        server = p.name if p.url is not None else "main"
        row = next((r for r in rows if r["name"] == server), None)
        listed = {m["id"]: m for m in (row or {}).get("models", [])}
        wanted = preset_tasks(p, settings)
        presets.append({
            "name": p.name, "title": p.title, "what": p.what, "url": p.url, "keys_at": p.keys_at, "server": server,
            "set_up": row is not None, "key_set": bool(row and row["key_set"]), "tasks": wanted,
            "active": all(task(settings, t) == {"server": server, "model": m} for t, m in wanted.items()),
            "suggested": {t: [{"id": m, "why": why, "listed": m in listed, "price": (listed.get(m) or {}).get("price")}
                              for m, why in options] for t, options in p.suggested.items()},
        })
    return {"servers": rows, "tasks": tasks, "efforts": {use: effort(settings, use) for use in EFFORTS},
            "effort_levels": list(EFFORT_LEVELS), "kinds": list(KINDS), "presets": presets}


def trial_dir() -> Path:
    """Where `--test` keeps what it made (the voice clip the page plays)."""
    d = home() / ".models-test"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    """A one-colour PNG, written by hand (no image library needed)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def _wav(path: Path, seconds: float, rate: int = 16000, channels: int = 1, loud: float = 0.0) -> Path:
    """Silence, or with `loud` noise: for the tasks that take audio but are only being tried."""
    n = int(seconds * rate) * channels
    samples = (np.random.default_rng(0).normal(0, loud, n) if loud else np.zeros(n)).clip(-1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((samples * 32767).astype("<i2").tobytes())
    return path


def _reference_voice(settings: dict) -> bytes:
    """A voice to clone for the test: the first channel's voice sample, else 4 s of generated sound."""
    from .settings import Profile
    root = home() / "channels"
    for profile_file in sorted(root.glob("*/profile.json")) if root.exists() else []:
        voice = Profile(profile_file.parent.name).voice_path
        if voice and voice.exists():
            return voice.read_bytes()
    return _wav(trial_dir() / "reference.wav", 4, 24000, loud=0.05).read_bytes()


def try_task(name: str, settings: dict) -> dict:
    """Tries a task with one small real request; what came back, how long it took, and whether it worked."""
    from . import languages
    from .llm import LLMError, Models, wav_seconds
    ai = Models(settings)
    out = {"task": name, "server": ai.tasks[name]["server"], "model": ai.model(name), "ok": False,
           "seconds": 0.0, "detail": "", "tok_per_s": None, "file": None, "cost": None}
    t0 = time.monotonic()
    try:
        if name == "text":
            reply = ai.chat_json("text", "You answer in JSON only.", 'Reply with {"ok": true, "word": "<any word>"}.',
                                 effort(settings, "spelling"), 400, "trying the text model", quiet=True)
            out.update(ok=True, detail=f"answered {json.dumps(reply, ensure_ascii=False)[:160]}",
                       tok_per_s=round(ai.server("text").stats[-1][3], 1))
        elif name == "vision":
            image = base64.b64encode(_png((255, 91, 46))).decode()
            user = [{"type": "text", "text": 'Which colour fills this image? Reply as JSON: {"colour": "..."}'},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}}]
            reply = ai.chat_json("vision", "You describe images briefly.", user, "none", 400,
                                 "trying the vision model", quiet=True)
            out.update(ok=True, detail=f"saw {json.dumps(reply, ensure_ascii=False)[:160]} (it was orange)",
                       tok_per_s=round(ai.server("vision").stats[-1][3], 1))
        elif name == "voice":
            lang = (languages.dub_languages(settings) or ["en"])[0]
            sentence = (languages.get(lang).calibration or ("Hello, this is a test of the cloned voice.",))[0]
            data = ai.tts(sentence, lang, base64.b64encode(_reference_voice(settings)).decode(), 42,
                          label="trying the voice model")
            clip = trial_dir() / "voice.wav"
            clip.write_bytes(data)
            (trial_dir() / "voice.json").write_text(json.dumps({"lang": lang, "text": sentence}), encoding="utf-8")
            out.update(ok=True, detail=f'said "{sentence}" ({wav_seconds(data) or 0:.1f} s)', file=str(clip))
        elif name == "hearing":
            said = trial_dir() / "voice.json"  # the voice test's clip, if there is one: hear what it said
            meta = json.loads(said.read_text(encoding="utf-8")) if said.exists() and (trial_dir() / "voice.wav").exists() else None
            clip = trial_dir() / "hearing.wav"
            if meta:
                shutil.copyfile(trial_dir() / "voice.wav", clip)
            else:
                _wav(clip, 2)
            got = ai.transcribe(clip, meta["lang"] if meta else "en")
            heard = " ".join(str(w.get("word", "")) for w in got["words"]).strip()
            timed = all("start" in w and "end" in w for w in got["words"])
            out.update(ok=timed, detail=(f'heard "{heard[:160]}"' if heard else "heard silence (as it should)")
                       + ("" if timed else ", but without word times"))
        elif name == "separation":
            src = _wav(trial_dir() / "separation-in.wav", 3, 44100, 2, loud=0.1)
            ai.separate(src, trial_dir() / "vocals.wav", trial_dir() / "music.wav")
            out.update(ok=True, detail="split 3 s of sound into voice and music")
        else:
            raise ValueError(f"no task {name!r}; there are: {', '.join(TASKS)}")
    except (LLMError, OSError, ValueError, KeyError) as e:
        out["detail"] = str(e)[:300]
    out["seconds"] = round(time.monotonic() - t0, 1)
    out["cost"] = ai.spent() or None  # in USD, where the server says what a request cost
    return out
