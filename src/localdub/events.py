"""Machine-readable events for the server and for AI agents (`--json`).

Two streams, two audiences:
  stderr  messages for people (log() everywhere), unchanged by --json;
  stdout  with --json: one JSON object per line ("JSON Lines") and nothing else.

A reader (the Rust server, a script, an agent) reads stdout line by line. Every line is one
complete event, and the last line is always the result:

  {"event": "start", "command": "run", "run": "excerpt", "channel": "friend", "dir": "...",
   "stages": ["prep", ...], "done": []}
  {"event": "stage", "stage": "separate", "status": "started"}
  {"event": "stage", "stage": "separate", "status": "done", "seconds": 19.3}
  ...
  {"event": "result", "status": "waiting", "exit_code": 3, "run": "excerpt",
   "waiting_for": {"kind": "review1", "file": ".../review-1-transcript.txt"}}

The result's status is "done" (exit code 0), "waiting" for a person (3) or "error" (anything
else, with an "error" message). Facts for the result are collected on the way with note().

Every request to a model (llm.py) also reports a "call" event as it finishes, so a reader can show
what the AI is doing and how fast:
  {"event": "call", "kind": "chat", "model": "gemma4-31b", "label": "translating 1-20",
   "seconds": 24.1, "tokens": 2463, "tok_per_s": 103.2, "prompt_tokens": 1890, "prompt_per_s": 910.4}
  {"event": "call", "kind": "tts", "model": "qwen3-tts", "label": "line 31, take 1", "seconds": 0.9, "chars": 64,
   "audio_seconds": 3.1}
  {"event": "call", "kind": "asr", ...}  {"event": "call", "kind": "separation", ...}
Each call also names its `server` (models.py: which server does which task).

`localdub models --json` sends one "models" event: the servers (url, kind, key_set, remote,
reachable, the models they list), the tasks (server, model, what they need) and the thinking levels;
`localdub models --test TASK --json` one "models_test" event:
  {"event": "models_test", "task": "voice", "server": "main", "model": "qwen3-tts", "ok": true,
   "seconds": 2.1, "detail": "said \"…\" (4.2 s)", "tok_per_s": null, "file": ".../.models-test/voice.wav"}

`localdub line RUN ID --json` without changes sends one "line" event (id, start, end, src, tgt, placed,
seconds, passed, why, heard, fit, shortened_from, fixed); fixing it is a job like a run, with
stages voice, mix (and shorts-dub when dubbed Shorts have the line) and its progress labelled
"re-voicing, take N".

Stages that work line by line also report how far they are, with the line's place in the video
(`at`, seconds), so a reader can move a playhead:
  {"event": "progress", "stage": "translate", "label": "translating", "done": 20, "total": 58, "line": 20, "at": 61.2}
  {"event": "progress", "stage": "voice", "label": "voicing, take 1", "done": 31, "total": 58, "line": 31, "at": 72.4}
  {"event": "progress", "stage": "voice", "label": "checking take 1", "done": 0, "total": 58}
"""

from __future__ import annotations

import json

_enabled = False
_result: dict = {}


def configure(enabled: bool) -> None:
    """Start a command: events on or off, nothing noted yet."""
    global _enabled
    _enabled = enabled
    _result.clear()


def emit(event: str, **fields) -> None:
    """Write one event to stdout as a single line of JSON, and send it on immediately."""
    if not _enabled:
        return
    data = {"event": event, **fields}
    json_line = json.dumps(data, ensure_ascii=False)
    print(json_line, flush=True)


def note(**fields) -> None:
    """Remember facts for the result event: the run, what it waits for, outputs, an error."""
    _result.update(fields)


def finish(exit_code: int) -> None:
    """The last event: how the command ended, plus everything noted on the way."""
    status = {0: "done", 3: "waiting"}.get(exit_code, "error")
    emit("result", status=status, exit_code=exit_code, **_result)
