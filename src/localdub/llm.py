"""The model server over HTTP: llama-swap on the GPU box (deploy/gpu/llama-swap.yaml).

One URL and one API key for every model: chat and vision (/v1/chat/completions), the voice clone
(llama.cpp's /tts behind /upstream/<model>/), transcription (/v1/audio/transcriptions) and voice/music
separation (/upstream/<model>/separate). llama-swap starts each model's server when a request names it
and stops the ones that can't run beside it, so localdub only frees the GPUs itself when it runs a
tool on this machine instead (the "local" backends of Nemotron and Demucs).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

CHUNK = 1 << 20  # copy large bodies 1 MB at a time


class LLMError(RuntimeError):
    pass


def parse_json(text: str):
    """json.loads, then a few repairs for what models get wrong: trailing commas, curly quotes,
    and stray double quotes inside strings. None if it still isn't JSON."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", text.replace("\u201c", '\\"').replace("\u201d", '\\"'))
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    fixed = _escape_inner_quotes(fixed)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # last resort: keys without quotes or in single quotes ({interest: 3}, {'interest': 3})
    try:
        return json.loads(re.sub(r"([{,]\s*)'?([A-Za-z_]\w*)'?\s*:", r'\1"\2":', fixed))
    except json.JSONDecodeError:
        return None


def _escape_inner_quotes(s: str) -> str:
    """Inside a string, a quote that isn't followed by , } ] or : is part of the text: escape it.
    'a sign saying "FOR RENT" on the door' is the usual case."""
    out, in_str, i = [], False, 0
    while i < len(s):
        c = s[i]
        if c == "\\" and in_str:
            out.append(s[i:i + 2])
            i += 2
            continue
        if c == '"':
            if not in_str:
                in_str = True
                out.append(c)
            else:
                j = i + 1
                while j < len(s) and s[j] in " \t\r\n":
                    j += 1
                if j >= len(s) or s[j] in ",}]:":
                    in_str = False
                    out.append(c)
                else:
                    out.append('\\"')
        else:
            out.append(c)
        i += 1
    return "".join(out)


def api_key(settings: dict) -> str:
    """From the environment, else from the llama service's env file (KEY=value lines)."""
    name = settings.get("api_key_env", "LLAMA_API_KEY")
    if os.environ.get(name):
        return os.environ[name]
    try:
        for line in open(settings.get("api_key_file", "/etc/llama.env"), encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            if k.strip() == name:
                return v.strip().strip("'\"")
    except OSError:
        pass
    return ""


def multipart(fields: list[tuple[str, str]], file_field: str, filename: str, content: bytes,
              content_type: str = "audio/wav") -> tuple[bytes, str]:
    """An upload like an HTML form sends it (multipart/form-data): text fields plus one file.
    Returns the request body and the Content-Type header that goes with it.

    The body is a list of parts, each starting with a line `--<boundary>`, then its headers, an empty
    line and its content; a final `--<boundary>--` ends it. Lines end with CRLF (\\r\\n)."""
    boundary = uuid.uuid4().hex  # any string that can't occur in the content
    out = []
    for name, value in fields:
        out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

    out.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode())
    out.append(content)
    out.append(f'\r\n--{boundary}--\r\n'.encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


class Server:
    def __init__(self, settings: dict):
        self.url = settings["llm_url"].rstrip("/")
        self.key = api_key(settings)
        self.stats: list[tuple[str, float, int, float]] = []

    def _open(self, method: str, path: str, data=None, headers: dict | None = None, timeout: float = 1800):
        """Send one request and return the open response; `data` is bytes or an open file."""
        headers = dict(headers or {})
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise LLMError(f"{method} {path}: HTTP {e.code} {e.read()[:300]!r}") from None
        except urllib.error.URLError as e:
            raise LLMError(f"{method} {path}: {e.reason} (is llama-swap running at {self.url}?)") from None

    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 1800, raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        with self._open(method, path, data, {"Content-Type": "application/json"}, timeout) as r:
            payload = r.read()
        return payload if raw else json.loads(payload)

    # --- models -----------------------------------------------------------------------------

    def models(self) -> list[str]:
        """The model names the server offers (whether running or not)."""
        return [m["id"] for m in self._req("GET", "/v1/models", timeout=30).get("data", [])]

    def running(self) -> list[str]:
        """The models whose servers are up, starting or still stopping."""
        return [m["model"] for m in self._req("GET", "/running", timeout=30).get("running", [])
                if m.get("state") not in ("stopped", "shutdown")]

    def unload_all(self, keep: str | None = None) -> None:
        """Free the GPUs for a tool that runs on this machine (local Demucs or Nemotron); `keep` stays."""
        for name in self.running():
            if name != keep:
                print(f"   unloading {name} to free the GPUs", file=sys.stderr)
                self._req("POST", f"/api/models/unload/{urllib.parse.quote(name)}", timeout=120, raw=True)
        deadline = time.monotonic() + 120
        while [n for n in self.running() if n != keep]:
            if time.monotonic() > deadline:
                raise LLMError("models did not unload within 2 minutes")
            time.sleep(1)

    # --- chat ---------------------------------------------------------------------------------

    def _chat(self, body: dict, label: str, quiet: bool) -> str:
        t0 = time.monotonic()
        reply = self._req("POST", "/v1/chat/completions", body)
        took = time.monotonic() - t0
        tokens = (reply.get("usage") or {}).get("completion_tokens", 0)
        speed = (reply.get("timings") or {}).get("predicted_per_second", 0.0)
        self.stats.append((label, took, tokens, speed))
        if not quiet:
            print(f"   {label}: {took:.0f}s, {tokens} tokens, {speed:.0f} tok/s", file=sys.stderr)
        choice = reply["choices"][0]
        content = choice["message"].get("content") or ""
        if choice.get("finish_reason") == "length" and not re.search(r"\{.*\}", content, re.S):
            # thinking used up the token limit before the answer: allow more next time
            body["max_tokens"] = min(body["max_tokens"] * 2, 24576)
            print(f"   {label}: ran out of tokens while thinking, retrying with {body['max_tokens']}",
                  file=sys.stderr)
        return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()

    @staticmethod
    def _body(model, system, user, effort, max_tokens) -> dict:
        return {"model": model, "temperature": 0.3, "max_tokens": max_tokens, "reasoning_effort": effort,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}

    def chat_json(self, model: str, system: str, user, effort: str, max_tokens: int, label: str,
                  quiet: bool = False) -> dict:
        """`user` is text, or a list of content parts (text and images) for a vision model.
        A reply that isn't valid JSON is repaired if it can be, else asked again a little less
        deterministically (temperature 0.3, then 0.6, then 0.9)."""
        body = self._body(model, system, user, effort, max_tokens)
        last = None
        for attempt in range(3):
            body["temperature"] = 0.3 * (attempt + 1)
            content = self._chat(body, label, quiet)
            m = re.search(r"\{.*\}", content, re.S)
            if not m:
                last = content[:200]
                continue
            got = parse_json(m.group(0))
            if got is not None:
                return got
            last = m.group(0)[:200]
        raise LLMError(f"{label}: no valid JSON after 3 tries: {last!r}")

    def chat_text(self, model: str, system: str, user, effort: str, max_tokens: int, label: str,
                  quiet: bool = False) -> str:
        return self._chat(self._body(model, system, user, effort, max_tokens), label, quiet)

    # --- voice, transcription, separation ----------------------------------------------------------

    def tts(self, model: str, text: str, lang: str, ref_b64: str, seed: int) -> bytes:
        """Voice clone: llama.cpp's /tts on that model's own server, through llama-swap's passthrough."""
        body = {"input": text, "lang": lang, "seed": seed, "speaker_ref_b64": ref_b64}
        data = self._req("POST", f"/upstream/{model}/tts", body, timeout=900, raw=True)
        if data[:4] != b"RIFF":
            raise LLMError(f"/tts returned no audio for {text!r}: {data[:300]!r}")
        return data

    def transcribe(self, model: str, wav: Path, lang: str) -> dict:
        """Speech to text with word times: {"text": ..., "words": [{"word", "start", "end"}, ...]}."""
        fields = [("model", model), ("language", lang), ("response_format", "verbose_json"),
                  ("timestamp_granularities[]", "word")]
        body, ctype = multipart(fields, "file", wav.name, wav.read_bytes())
        with self._open("POST", "/v1/audio/transcriptions", body, {"Content-Type": ctype}) as r:
            got = json.loads(r.read())
        if "words" not in got:
            raise LLMError(f"transcription of {wav.name} returned no words: {str(got)[:300]}")
        return got

    def separate(self, model: str, audio: Path, vocals: Path, music: Path) -> None:
        """Split `audio` into his voice and everything else (deploy/gpu/demucs_server.py behind llama-swap).
        The answer is a ZIP with vocals.wav and no_vocals.wav; both directions are streamed to disk."""
        headers = {"Content-Type": "audio/wav", "Content-Length": str(audio.stat().st_size)}
        with tempfile.TemporaryFile() as z:
            # the read timeout must outlast the separation itself: nothing comes back until it's done
            with audio.open("rb") as f, self._open("POST", f"/upstream/{model}/separate", f, headers, 7200) as r:
                shutil.copyfileobj(r, z, CHUNK)
            try:
                with zipfile.ZipFile(z) as stems:
                    for name, dest in (("vocals.wav", vocals), ("no_vocals.wav", music)):
                        with stems.open(name) as src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out, CHUNK)
            except (zipfile.BadZipFile, KeyError) as e:
                raise LLMError(f"separation returned no stems ({e})") from None
