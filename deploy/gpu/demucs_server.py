#!/usr/bin/env python3
"""Demucs over HTTP, for the GPU box: llama-swap starts it on demand like any other model server.

  GET  /health    200 when the server is up (llama-swap's health check)
  POST /separate  body: an audio file (WAV from localdub). Answer: a ZIP with vocals.wav and
                  no_vocals.wav (the voice, and everything else: music and effects).

Only the standard library: run it with the Python of the venv that has Demucs and CUDA torch, e.g.
  /root/venvs/demucs-gpu/bin/python demucs_server.py --port 5810 --model htdemucs_ft --device cuda:0
It listens on 127.0.0.1 only; llama-swap in front checks the API key. One separation runs at a time.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CHUNK = 1 << 20  # copy request and response bodies 1 MB at a time instead of holding them in memory


def separate(args, audio: Path, work: Path) -> Path:
    """Run Demucs on one file; returns the ZIP with both stems."""
    cmd = [args.python, "-m", "demucs", "-n", args.model, "--two-stems=vocals", "-d", args.device,
           "-o", str(work / "out"), str(audio)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"demucs failed (exit {p.returncode}):\n{p.stderr[-1500:]}")
    stems = work / "out" / args.model / audio.stem
    result = work / "stems.zip"
    with zipfile.ZipFile(result, "w", zipfile.ZIP_STORED) as z:  # WAV barely compresses; storing is fast
        for name in ("vocals.wav", "no_vocals.wav"):
            z.write(stems / name, name)
    return result


def make_handler(args, lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                self.reply(200, b"ok\n", "text/plain")
            else:
                self.reply(404, b"not found\n", "text/plain")

        def do_POST(self):
            if self.path.split("?")[0] != "/separate":
                return self.reply(404, b"not found\n", "text/plain")
            size = int(self.headers.get("Content-Length") or 0)
            if size <= 0:
                return self.reply(400, b"send the audio file as the request body\n", "text/plain")
            with tempfile.TemporaryDirectory(prefix="demucs-") as tmp:
                work = Path(tmp)
                audio = work / "audio.wav"
                with audio.open("wb") as f:
                    left = size
                    while left > 0:
                        chunk = self.rfile.read(min(CHUNK, left))
                        if not chunk:
                            return self.reply(400, b"request body ended early\n", "text/plain")
                        f.write(chunk)
                        left -= len(chunk)
                try:
                    with lock:  # one separation at a time: they all want the same GPU
                        result = separate(args, audio, work)
                except (RuntimeError, OSError) as e:
                    return self.reply(500, f"{e}\n".encode(), "text/plain")
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(result.stat().st_size))
                self.end_headers()
                with result.open("rb") as f:
                    shutil.copyfileobj(f, self.wfile, CHUNK)

        def reply(self, code: int, body: bytes, kind: str):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *a):  # to stderr, which llama-swap collects in its log
            sys.stderr.write(f"demucs_server: {self.address_string()} {fmt % a}\n")

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Demucs voice/music separation over HTTP")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--model", default="htdemucs_ft")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--python", default=sys.executable, help="the Python that has Demucs (default: this one)")
    args = ap.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args, threading.Lock()))
    print(f"demucs_server: {args.model} on {args.device}, listening on http://{args.host}:{args.port}",
          file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
