import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent
FAKES = HERE / "fakes"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def fake_env(tmp_path_factory):
    """A localdub home pointing at the fake server, fake nemo-speech and fake Demucs, plus a test video."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is needed")
    root = tmp_path_factory.mktemp("localdub")
    state, home = root / "fake-state", root / "home"
    home.mkdir()
    port = _free_port()
    server = subprocess.Popen([sys.executable, str(FAKES / "fake_server.py"), str(port), str(state)])
    from fixture import make
    video, words = make(root / "media")
    settings = {
        "llm_url": f"http://127.0.0.1:{port}",
        "nemo": {"bin": str(FAKES / "nemo-speech"), "model": str(words), "config": str(words), "max_chunk_minutes": 0.5},
        "demucs": {"python": str(FAKES / "demucs-python")},
        "shorts": {"min_seconds": 10, "max_seconds": 40},
        "speaker": {"model": "test-spectral"},
    }
    (home / "settings.json").write_text(json.dumps(settings))
    env = {"LOCALDUB_HOME": str(home), "LLAMA_API_KEY": "test-key", "LOCALDUB_FAKE_STATE": str(state),
           "LOCALDUB_FAKE_WORDS": str(words)}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield {"home": home, "state": state, "video": video, "words": words}
    server.terminate()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
