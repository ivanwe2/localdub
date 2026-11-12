"""Fake llama-swap for the tests: the routes localdub uses, with the models answering deterministically.

  GET  /v1/models, /running            POST /api/models/unload[/<model>]
  POST /v1/chat/completions            text and vision
  POST /upstream/qwen3-tts/tts         voice clone
  POST /v1/audio/transcriptions        Nemotron (multipart upload; what it hears is in fake_asr.py)
  POST /upstream/demucs/<path>         forwarded to the real deploy/gpu/demucs_server.py (argv[4]),
                                       which runs the fake Demucs

Deterministic behaviour the tests rely on:
- spelling: a few typical spelling-by-ear errors get fixed; one "fix" changes a valid word (the guard must refuse it);
  "Кирчу" is "fixed" to a name that isn't known yet (must become a suggestion)
- translation: lines with id % 3 == 0 come back over their character budget
- TTS: the clip lasts len(text) / rate seconds; the rate depends on the seed
- files in the state dir switch on failures: FAIL_TRANSLATE, SHORTEN_ALWAYS_FAIL, BAD_VISION
argv: port, state dir, the fixture's words.json, the Demucs wrapper's URL.
State dir: requests.log (every chat request), tts.log (every text voiced), asr-http.log (every file transcribed).
"""
import base64
import hashlib
import io
import json
import math
import re
import struct
import sys
import urllib.request
import wave
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fake_asr import part, take, wav_seconds

STATE = Path(sys.argv[2])
STATE.mkdir(parents=True, exist_ok=True)
WORDS = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
DEMUCS = sys.argv[4]
KEY = "Bearer test-key"
MODELS = ["gemma4-31b", "gemma4-31b-vision", "qwen3-tts", "nemotron", "demucs", "other"]
VOICING = {"qwen3-tts", "nemotron"}  # the group that runs side by side; everything else swaps
running = {"other"}
voiced = {}  # take hash -> the text voiced, recorded by /tts so a transcribed take can be "heard" back
part_offset = [0.0]  # where the next partNN.wav starts in the video (parts arrive in order)
SPELL = {"найемите": "наемите", "ефтината": "евтината", "ефтини": "евтини", "фсички": "всички", "Кирчу": "Кирчо",
         "сирене": "сиренето"}  # the last one changes a valid word: refused by the guard
FILL = "we went to the market to see what the prices are like this week and what we can buy".split()


def load(model):
    global running
    assert model in MODELS, model
    running = ({m for m in running if m in VOICING} if model in VOICING else set()) | {model}


def form(content_type: str, body: bytes) -> tuple[dict, dict]:
    """A multipart/form-data body -> ({field: text}, {field: (filename, bytes)})."""
    msg = BytesParser(policy=HTTP).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    assert msg.is_multipart(), "not a multipart body"
    fields, files = {}, {}
    for p in msg.iter_parts():
        name = p.get_param("name", header="content-disposition")
        if p.get_filename():
            files[name] = (p.get_filename(), p.get_payload(decode=True))
        else:
            fields[name] = p.get_payload(decode=True).decode()
    return fields, files


def english(i, budget, over):
    words = ["line", ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][i % 9]]
    target = int(budget * (1.3 if over else 0.75))
    k = 0
    while len(" ".join(words)) < target:
        words.append(FILL[(i + k) % len(FILL)])
        k += 1
    return " ".join(words).capitalize() + "."


def chat_reply(content, tokens=100):
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": tokens}, "timings": {"predicted_per_second": 100.0}}


class Handler(BaseHTTPRequestHandler):
    def send(self, obj, ctype="application/json", code=200):
        out = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def authorized(self):
        if self.headers.get("Authorization") != KEY:
            self.send({"error": "unauthorized"}, code=401)
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        if self.path == "/v1/models":
            return self.send({"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]})
        if self.path == "/running":
            return self.send({"running": [{"model": m, "state": "ready"} for m in sorted(running)]})
        self.send({"error": f"no route {self.path}"}, code=404)

    def do_POST(self):
        if not self.authorized():
            return
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/api/models/unload":
            running.clear()
            return self.send(b"OK", "text/plain")
        if self.path.startswith("/api/models/unload/"):
            running.discard(self.path.rsplit("/", 1)[1])
            return self.send(b"OK", "text/plain")
        if self.path == "/upstream/qwen3-tts/tts":
            return self.tts(json.loads(raw))
        if self.path.startswith("/upstream/demucs/"):
            return self.demucs(raw)
        if self.path == "/v1/audio/transcriptions":
            return self.transcribe(*form(self.headers["Content-Type"], raw))
        if self.path != "/v1/chat/completions":
            return self.send({"error": f"no route {self.path}"}, code=404)
        b = json.loads(raw)
        load(b["model"])
        system, user = b["messages"][0]["content"], b["messages"][1]["content"]
        with open(STATE / "requests.log", "a", encoding="utf-8") as f:
            f.write(json.dumps({"model": b["model"], "system": system[:60], "effort": b["reasoning_effort"],
                                "user": user if isinstance(user, str) else "[image]"}, ensure_ascii=False) + "\n")
        if system.startswith("You describe video frames"):
            return self.vision(b, user)
        if system.startswith("You pick moments"):
            return self.pick(user)
        if system.startswith("You translate") and (STATE / "FAIL_TRANSLATE").exists():
            n = int((STATE / "FAIL_TRANSLATE").read_text() or 0) + 1
            (STATE / "FAIL_TRANSLATE").write_text(str(n))
            if n == 2:
                (STATE / "FAIL_TRANSLATE").unlink()
                return self.send({"error": "boom"}, code=500)
        if system.startswith("Shorten") and (STATE / "SHORTEN_ALWAYS_FAIL").exists():
            return self.send({"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                              "usage": {"completion_tokens": b["max_tokens"]}, "timings": {}})
        if system.startswith("You proofread"):
            fixes = []
            for line in user.splitlines():
                m = re.match(r"(\d+): (.*)", line)
                if not m:
                    continue
                words = m.group(2).split()
                new = [re.sub(r"\w+", lambda x: SPELL.get(x.group(0), x.group(0)), w) for w in words]
                if new != words:
                    fixes.append({"id": int(m.group(1)), "text": " ".join(new)})
            return self.send(chat_reply(json.dumps({"fixes": fixes}, ensure_ascii=False)))
        if system.startswith("You translate"):
            lines = []
            for line in user.split("Translate:\n", 1)[1].splitlines():
                m = re.match(r"(\d+) \[[\d.]+s, max (\d+) chars\]: (.*)", line)
                if m:
                    i = int(m.group(1))
                    lines.append({"id": i, "text": english(i, int(m.group(2)), i % 3 == 0)})
            return self.send(chat_reply("<think>plan</think>" + json.dumps({"lines": lines})))
        if system.startswith("Shorten"):
            lines = []
            for line in user.splitlines():
                m = re.match(r"(\d+) \[max (\d+) chars, now \d+\]: (.*)", line)
                if m:
                    lines.append({"id": int(m.group(1)), "text": english(int(m.group(1)), int(m.group(2)), False)})
            return self.send(chat_reply(json.dumps({"lines": lines})))
        raise AssertionError(system)

    def tts(self, b):
        load("qwen3-tts")
        assert b["lang"] and len(b["speaker_ref_b64"]) > 1000
        with open(STATE / "tts.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(b["input"], ensure_ascii=False) + "\n")
        ref = hashlib.md5(base64.b64decode(b["speaker_ref_b64"])).hexdigest()  # as voice.take_name does
        voiced[hashlib.md5((ref + b["input"]).encode()).hexdigest()[:8]] = b["input"]
        rate = {42: 11.0, 7: 16.0, 1234: 13.0}.get(b["seed"], 14.0)
        n = int(len(b["input"]) / rate * 24000)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            pad = b"\x00\x00" * 4800
            w.writeframes(pad + b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * (150 + b["seed"] % 50) * i / 24000)))
                                         for i in range(n)) + pad)
        return self.send(buf.getvalue(), "audio/wav")

    def transcribe(self, fields, files):
        assert fields["model"] == "nemotron" and fields["response_format"] == "verbose_json"
        assert fields["timestamp_granularities[]"] == "word" and fields["language"]
        load("nemotron")
        filename, audio = files["file"]
        stem, seconds = filename.rsplit(".", 1)[0], wav_seconds(io.BytesIO(audio))
        with open(STATE / "asr-http.log", "a", encoding="utf-8") as f:
            f.write(filename + "\n")
        if stem.startswith("part"):
            if stem == "part00":
                part_offset[0] = 0.0
            got = part(WORDS, part_offset[0], seconds)
            part_offset[0] += seconds
        else:
            got = take(stem, voiced[stem.split("-")[1]], seconds)
        return self.send({"task": "transcribe", "language": fields["language"], "duration": seconds, **got})

    def demucs(self, raw):
        load("demucs")
        rest = self.path[len("/upstream/demucs"):]
        req = urllib.request.Request(DEMUCS + rest, data=raw, method="POST",
                                     headers={"Content-Type": self.headers.get("Content-Type", "audio/wav")})
        with urllib.request.urlopen(req, timeout=120) as r:
            return self.send(r.read(), r.headers["Content-Type"])

    def vision(self, b, user):
        assert user[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        n = int(re.search(r"These are (\d+) frames", user[0]["text"]).group(1))
        content = json.dumps({"frames": [f"a market stall with vegetables, frame {i}" for i in range(n)], "interest": 3})
        if (STATE / "BAD_VISION").exists() and "00:16" in user[0]["text"]:
            content = '{"frames": [broken'
        return self.send(chat_reply(content))

    def pick(self, user):
        rows = [re.match(r"(\d+) (\d\d):(\d\d) (\d+)s", line) for line in user.splitlines()]
        rows = [(int(m.group(1)), int(m.group(2)) * 60 + int(m.group(3))) for m in rows if m]
        cands, i = [], 0
        while i < len(rows) and len(cands) < 6:
            j = i
            while j + 1 < len(rows) and rows[j + 1][1] - rows[i][1] < 35:
                j += 1
            cands.append({"start": rows[i][0], "end": rows[j][0], "title": f"Moment {len(cands) + 1}",
                          "hook": "a surprising price", "why": "stands alone", "visual": "market stalls"})
            i = j + 2
        return self.send(chat_reply(json.dumps({"candidates": cands})))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
