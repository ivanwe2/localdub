"""llama-server (router mode) over HTTP: chat, TTS, and loading/unloading presets."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


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


class Server:
    def __init__(self, settings: dict):
        self.url = settings["llm_url"].rstrip("/")
        self.key = api_key(settings)
        self.stats: list[tuple[str, float, int, float]] = []

    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 1800, raw: bool = False):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
        except urllib.error.HTTPError as e:
            raise LLMError(f"{method} {path}: HTTP {e.code} {e.read()[:300]!r}") from None
        except urllib.error.URLError as e:
            raise LLMError(f"{method} {path}: {e.reason} (is the llama service running? llama-start)") from None
        return payload if raw else json.loads(payload)

    # --- presets ------------------------------------------------------------------------

    def models(self) -> dict[str, str]:
        out = {}
        for m in self._req("GET", "/models", timeout=30).get("data", []):
            st = m.get("status")
            out[m["id"]] = st.get("value") if isinstance(st, dict) else str(st)
        return out

    def unload_all(self, keep: str | None = None) -> None:
        """Free the GPUs for tools outside the router (Demucs, Nemotron); `keep` stays loaded."""
        for name, status in self.models().items():
            if name != keep and status in ("loaded", "loading", "sleeping"):
                print(f"   unloading {name} to free the GPUs", file=sys.stderr)
                try:
                    self._req("POST", "/models/unload", {"model": name}, timeout=60)
                except LLMError as e:
                    if "not running" not in str(e):
                        raise
        deadline = time.monotonic() + 120
        while any(s in ("loaded", "loading") for n, s in self.models().items() if n != keep):
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

    # --- TTS --------------------------------------------------------------------------------------

    def tts(self, model: str, text: str, lang: str, ref_b64: str, seed: int) -> bytes:
        body = {"model": model, "input": text, "lang": lang, "seed": seed, "speaker_ref_b64": ref_b64}
        data = self._req("POST", "/tts", body, timeout=900, raw=True)
        if data[:4] != b"RIFF":
            raise LLMError(f"/tts returned no audio for {text!r}: {data[:300]!r}")
        return data
