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
    status = switch = {
        0: "done",
        3: "waiting",
    }.get(exit_code, "error")
    emit("result", status=status, exit_code=exit_code, **_result)
