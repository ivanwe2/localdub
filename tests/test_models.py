"""Models from any server: the settings, `localdub models`, keys, and trying each task against the fakes."""
import io
import json
import os
import stat

import pytest

from localdub import models
from localdub.llm import LLMError, Server
from localdub.run import main
from localdub.settings import load_settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALDUB_HOME", str(tmp_path))
    return tmp_path


def events_of(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def saved(home):
    return json.loads((home / "settings.json").read_text(encoding="utf-8"))


def test_without_new_settings_every_task_is_on_main_as_before(home):
    (home / "settings.json").write_text(json.dumps({"llm_url": "http://gpu-box:9876/", "tts_model": "my-tts"}))
    settings = load_settings()

    main_server = models.servers(settings)["main"]

    assert main_server == {"url": "http://gpu-box:9876", "kind": "llama-swap", "key_env": "LLAMA_API_KEY",
                           "key_file": "/etc/llama.env"}
    assert {t: models.task(settings, t) for t in models.TASKS} == {
        "text": {"server": "main", "model": "gemma4-31b"}, "vision": {"server": "main", "model": "gemma4-31b-vision"},
        "voice": {"server": "main", "model": "my-tts"}, "hearing": {"server": "main", "model": "nemotron"},
        "separation": {"server": "main", "model": "demucs"}}


def test_tasks_move_to_other_servers_and_the_thinking_levels_change(home):
    assert main(["models", "--server", "cloud", "--url", "https://api.example.com/v1/", "--kind", "openai"]) == 0
    assert main(["models", "--set", "text=cloud/org/model-1"]) == 0  # a model name may have slashes
    assert main(["models", "--effort", "translate=low", "shorts=default"]) == 0

    data = saved(home)
    assert data["servers"] == {"cloud": {"url": "https://api.example.com/v1", "kind": "openai"}}
    assert data["tasks"] == {"text": {"server": "cloud", "model": "org/model-1"}}
    settings = load_settings()
    assert settings["translate_effort"] == "low" and settings["shorts"]["select_effort"] == "default"
    assert settings["shorts"]["count"] == 6, "the rest of 'shorts' stays"

    assert main(["models", "--set", "vision=nowhere/x"]) == 1
    assert main(["models", "--set", "speech=main/x"]) == 1
    assert main(["models", "--effort", "translate=max"]) == 1
    assert main(["models", "--server", "cloud", "--remove"]) == 1, "text is on it"
    assert main(["models", "--server", "main", "--remove"]) == 1
    assert main(["models", "--server", "new"]) == 1, "a new server needs its --url"
    assert main(["models", "--server", "bad", "--url", "ftp://x"]) == 1
    assert main(["models", "--set", "text=main/gemma4-31b"]) == 0
    assert main(["models", "--server", "cloud", "--remove"]) == 0
    assert "cloud" not in saved(home)["servers"]


def test_a_key_is_stored_for_its_owner_only_and_never_shown(home, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("sk-secret-123\n"))
    monkeypatch.setattr(Server, "listed", lambda self, timeout=30: (_ for _ in ()).throw(LLMError("offline")))

    assert main(["models", "--server", "cloud", "--url", "https://api.example.com", "--key-stdin"]) == 0
    assert main(["models", "--json"]) == 0

    out = capsys.readouterr()
    assert "sk-secret-123" not in out.out + out.err
    listing = next(json.loads(line) for line in out.out.splitlines() if '"models"' in line)
    cloud = next(s for s in listing["servers"] if s["name"] == "cloud")
    assert cloud["key_set"] and cloud["key_source"] == "stored" and cloud["remote"] and not cloud["reachable"]
    assert models.key("cloud", models.servers(load_settings())["cloud"]) == "sk-secret-123"
    if os.name != "nt":
        assert stat.S_IMODE((home / "keys.json").stat().st_mode) == 0o600


def test_remote_servers_are_told_from_local_ones():
    for url in ["http://localhost:9876", "http://127.0.0.1:8000", "http://192.168.0.71:9876", "http://10.1.2.3",
                "http://gpu-box:9876", "http://gpu.lan", "http://studio.local:1234", "http://[::1]:9876",
                "http://100.101.102.103"]:
        assert not models.is_remote(url), url
    for url in ["https://api.example.com/v1", "https://8.8.8.8", "https://openrouter.ai/api"]:
        assert models.is_remote(url), url


def test_thinking_is_left_to_the_server_by_default_and_tools_are_called_on_their_own_servers():
    assert "reasoning_effort" not in Server._body("m", "s", "u", "default", 10)
    assert Server._body("m", "s", "u", "low", 10)["reasoning_effort"] == "low"
    assert Server("http://x", kind="llama-swap")._upstream("qwen3-tts", "tts") == "/upstream/qwen3-tts/tts"
    assert Server("http://x", kind="openai")._upstream("qwen3-tts", "tts") == "/tts"
    direct = Server("http://127.0.0.1:1", kind="openai")  # nothing listens there: nothing may be asked
    assert direct.running() == [] and direct.unload_all() is None


def test_every_task_can_be_tried(fake_env, capsys):
    fake_env["use"]()
    results = {}
    for task in models.TASKS:
        rc = main(["models", "--test", task, "--json"])
        results[task] = next(e for e in events_of(capsys) if e["event"] == "models_test")
        assert rc == 0, results[task]

    assert all(r["ok"] and r["server"] == "main" for r in results.values())
    assert results["text"]["tok_per_s"] == 100.0 and "market" in results["text"]["detail"]
    assert "orange" in results["vision"]["detail"]
    assert results["voice"]["file"].endswith("voice.wav") and os.path.exists(results["voice"]["file"])
    assert "welcome" in results["hearing"]["detail"]  # it heard the voice test's sentence
    assert (fake_env["home"] / ".models-test" / "vocals.wav").exists()


def test_a_task_on_another_server_goes_there(fake_env, capsys):
    """Voice and separation on an "openai" server: the TTS and Demucs servers themselves (/tts, /separate)."""
    settings = json.loads((fake_env["home"] / "settings.json").read_text())
    settings["servers"] = {"direct": {"url": settings["llm_url"], "kind": "openai"}}
    settings["tasks"] = {"voice": {"server": "direct", "model": "qwen3-tts"},
                         "separation": {"server": "direct", "model": "demucs"}}
    (fake_env["home"] / "settings.json").write_text(json.dumps(settings))
    models.store_key("direct", "test-key")
    try:
        assert main(["models", "--test", "voice", "--json"]) == 0
        voice = events_of(capsys)
        assert main(["models", "--test", "separation", "--json"]) == 0
        separation = events_of(capsys)
        assert main(["models", "--json"]) == 0
        listing = next(e for e in events_of(capsys) if e["event"] == "models")
    finally:
        fake_env["use"]()
        models.store_key("direct", "")

    assert next(e for e in voice if e["event"] == "call")["server"] == "direct"
    assert next(e for e in voice if e["event"] == "models_test")["ok"]
    assert next(e for e in separation if e["event"] == "call")["server"] == "direct"
    by_task = {t["task"]: t for t in listing["tasks"]}
    assert by_task["voice"]["server"] == "direct" and by_task["voice"]["listed"]
    assert by_task["text"]["server"] == "main" and "Gemma 4 31B" in by_task["text"]["snippet"]
    assert {s["name"]: s["reachable"] for s in listing["servers"]} == {"main": True, "direct": True}
    assert listing["effort_levels"][0] == "default" and listing["efforts"]["translate"] == "medium"
