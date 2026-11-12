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
DEMUCS_SERVER = HERE.parent / "deploy" / "gpu" / "demucs_server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(port: int) -> None:
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)


@pytest.fixture(scope="session")
def fake_env(tmp_path_factory):
    """A localdub home pointing at a fake llama-swap (which forwards separation to the real Demucs
    wrapper running the fake Demucs), plus a fake nemo-speech and fake Demucs for the local backends
    and a test video. Tests pick the backends with fake_env["use"](...)."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is needed")
    root = tmp_path_factory.mktemp("localdub")
    state, home = root / "fake-state", root / "home"
    home.mkdir()
    from fixture import make
    video, words = make(root / "media")
    demucs_port, port = _free_port(), _free_port()
    procs = [
        subprocess.Popen([sys.executable, str(DEMUCS_SERVER), "--port", str(demucs_port),
                          "--python", str(FAKES / "demucs-python"), "--device", "cpu"], stderr=subprocess.DEVNULL),
        subprocess.Popen([sys.executable, str(FAKES / "fake_server.py"), str(port), str(state), str(words),
                          f"http://127.0.0.1:{demucs_port}"]),
    ]
    base = {
        "llm_url": f"http://127.0.0.1:{port}",
        "nemo": {"bin": str(FAKES / "nemo-speech"), "model": str(words), "config": str(words), "max_chunk_minutes": 0.5},
        "demucs": {"python": str(FAKES / "demucs-python")},
        "shorts": {"min_seconds": 10, "max_seconds": 40},
        "speaker": {"model": "test-spectral"},
    }

    def use(nemo="http", demucs="http"):
        """Write settings.json with these backends ("http": through the fake llama-swap; "local")."""
        settings = json.loads(json.dumps(base))
        settings["nemo"]["backend"], settings["demucs"]["backend"] = nemo, demucs
        (home / "settings.json").write_text(json.dumps(settings))

    use()
    env = {"LOCALDUB_HOME": str(home), "LLAMA_API_KEY": "test-key", "LOCALDUB_FAKE_STATE": str(state),
           "LOCALDUB_FAKE_WORDS": str(words)}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    _wait_for(demucs_port)
    _wait_for(port)
    yield {"home": home, "state": state, "video": video, "words": words, "use": use}
    for p in procs:
        p.terminate()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
