"""Tell a person when a long job needs them or is done: a push message through ntfy (ntfy.sh, or your own
ntfy server), with settings.json

  "notify": {"ntfy": "https://ntfy.sh/<a topic only you know>", "min_seconds": 60,
             "page_url": "http://localhost:8080"}

It's sent after `run`, `shorts`, `voice` and `line` when they ran for at least `min_seconds` (so a quick
"still waiting" doesn't buzz your phone). `page_url` makes the message open the web app on that run.
A token for a protected topic comes from $NTFY_TOKEN. Sending can't fail a command: a problem is logged.

Anyone who knows an ntfy.sh topic's name can read it: pick one nobody would guess, or run your own ntfy.
The message holds the run's name and, after an error, its first line.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

COMMANDS = ("run", "shorts", "voice", "line")

_WAITS = {
    "review1": ("Review 1 is ready: the transcript", "review"),
    "review2": ("Review 2 is ready: the translation", "review"),
    "voice": ("The voice samples are ready to compare", "voices"),
    "shorts": ("The Shorts candidates are ready to pick", "shorts"),
}


def message(command: str, rc: int, result: dict) -> dict:
    """The push message for how a command ended: title, text, tags (ntfy emoji names), the page to open."""
    run = result.get("run") or "localdub"
    if rc == 3:
        kind = (result.get("waiting_for") or {}).get("kind", "")
        text, page = _WAITS.get(kind, ("It waits for you", "studio"))
        return {"title": f"{run}: your turn", "message": text, "tags": ["hourglass_flowing_sand"], "page": page}
    if rc != 0:
        error = str(result.get("error") or "it stopped").splitlines()[0][:300]
        return {"title": f"{run}: stopped", "message": error, "tags": ["warning"], "page": "studio"}
    shorts = sum(len(files) for files in (result.get("shorts") or {}).values())
    if command == "line":
        line = (result.get("line") or {}).get("id", "")
        text = f"Line {line} is fixed and the dub mixed again" + (f", {shorts} Shorts rendered again" if shorts else "")
    elif command == "shorts":
        text = f"{shorts} Short{'' if shorts == 1 else 's'} rendered"
    elif command == "voice":
        text = "The channel's voice is set"
    elif result.get("outputs"):
        text = "The dubbed video is ready" + (f", and {shorts} Shorts" if shorts else "")
    else:
        text = f"{shorts} Short{'' if shorts == 1 else 's'} ready" if shorts else "Done"
    return {"title": f"{run}: done", "message": text, "tags": ["white_check_mark"], "page": "studio"}


def send(cfg: dict, note: dict, run: str | None = None, timeout: float = 10) -> str | None:
    """Publish `note` (from message()) to the ntfy topic in `cfg`; None if it went, else why not."""
    url = (cfg.get("ntfy") or "").rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or "/" not in parts.path:
        return f"notify.ntfy should be a topic's address like https://ntfy.sh/my-topic, not {url!r}"
    base, topic = url.rsplit("/", 1)
    body = {"topic": topic, "title": note["title"], "message": note["message"], "tags": note["tags"]}
    if page_url := (cfg.get("page_url") or "").rstrip("/"):
        route = f"#/{note['page']}/{urllib.parse.quote(run)}" if run and note["page"] != "studio" else "#/"
        body["click"] = f"{page_url}/{route}"
    headers = {"Content-Type": "application/json"}
    if token := os.environ.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + "/", data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return None
    except urllib.error.HTTPError as e:
        return f"ntfy answered HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return f"ntfy can't be reached: {getattr(e, 'reason', e)}"


def after(settings: dict, command: str, rc: int, result: dict, seconds: float) -> None:
    """Called when a command ends: a push message if it's set up and the command ran long enough."""
    cfg = settings.get("notify") or {}
    if command not in COMMANDS or not cfg.get("ntfy") or seconds < float(cfg.get("min_seconds", 60)):
        return
    problem = send(cfg, message(command, rc, result), result.get("run"))
    if problem:
        print(f"   (no notification sent: {problem})", file=sys.stderr)
