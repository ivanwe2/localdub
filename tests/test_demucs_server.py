"""The GPU box's Demucs wrapper, run as a real HTTP server against the fake Demucs."""
import io
import math
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "deploy" / "gpu" / "demucs_server.py"
FAKE_DEMUCS = ROOT / "tests" / "fakes" / "demucs-python"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tone_wav() -> bytes:
    """One second of a 440 Hz tone, stereo 44.1 kHz, like the audio localdub sends."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"".join(struct.pack("<hh", v, v) for v in
                               (int(8000 * math.sin(2 * math.pi * 440 * i / 44100)) for i in range(44100))))
    return buf.getvalue()


@pytest.fixture
def server():
    port = free_port()
    proc = subprocess.Popen([sys.executable, str(SERVER), "--port", str(port), "--python", str(FAKE_DEMUCS)],
                            stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(url + "/health", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    yield url
    proc.terminate()
    proc.wait(timeout=5)


def test_health(server):
    assert urllib.request.urlopen(server + "/health").read() == b"ok\n"


def test_separate_returns_both_stems(server):
    req = urllib.request.Request(server + "/separate", data=tone_wav(), headers={"Content-Type": "audio/wav"})
    with urllib.request.urlopen(req, timeout=60) as r:
        assert r.headers["Content-Type"] == "application/zip"
        stems = zipfile.ZipFile(io.BytesIO(r.read()))
    assert sorted(stems.namelist()) == ["no_vocals.wav", "vocals.wav"]
    with wave.open(io.BytesIO(stems.read("vocals.wav"))) as w:
        assert w.getnframes() > 40000  # about one second came back


def test_empty_body_is_refused(server):
    req = urllib.request.Request(server + "/separate", data=b"", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 400
