"""Whole runs against the fake servers: the automatic path, and the reviewed path that teaches the channel."""
import json
import subprocess

import pytest

from localdub.run import main

pytestmark = pytest.mark.slow


def streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.split()
    return out


def events_of(capsys):
    """The --json events printed since the last call (stdout carries nothing else)."""
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_automatic_run_with_shorts(fake_env, capsys):
    """The default setup: every model behind the model server (llama-swap), nothing run locally."""
    fake_env["use"]()
    rc = main(["run", str(fake_env["video"]), "--channel", "auto", "--from", "bg", "--to", "en", "--name", "auto",
               "--no-review", "--shorts", "1",
               "--json"])
    assert rc == 0
    ev = events_of(capsys)
    assert ev[0]["event"] == "start" and ev[0]["command"] == "run" and ev[0]["done"] == []
    done = [e["stage"] for e in ev if e["event"] == "stage" and e["status"] == "done"]
    assert done == ev[0]["stages"]  # every announced stage finished, in order
    result = ev[-1]
    assert result["event"] == "result" and result["status"] == "done" and result["exit_code"] == 0
    calls = [e for e in ev if e["event"] == "call"]
    assert {c["kind"] for c in calls} == {"chat", "tts", "asr", "separation"}  # every kind of model call
    chat = next(c for c in calls if c["kind"] == "chat")
    assert chat["model"] == "gemma4-31b" and chat["tok_per_s"] == 100.0 and chat["tokens"] == 100
    tts = next(c for c in calls if c["kind"] == "tts" and c["label"].startswith("line "))
    assert tts["label"].endswith("take 1") and tts["audio_seconds"] > 0  # which line, and how long it plays
    progress = [e for e in ev if e["event"] == "progress"]
    voicing = [e for e in progress if e["stage"] == "voice" and e["label"] == "voicing, take 1"]
    assert voicing and voicing[-1]["done"] == voicing[-1]["total"] and voicing[0]["at"] >= 0  # line by line, with its place
    translating = [e for e in progress if e["stage"] == "translate"]
    assert translating and translating[-1]["done"] == translating[-1]["total"]
    assert result["outputs"]["video"].endswith("auto.en.mp4") and set(result["shorts"]) == {"original", "dub"}
    run = fake_env["home"] / "runs" / "auto"
    assert streams(run / "auto.en.mp4") == ["video", "audio", "audio"]
    assert (run / "auto.en.srt").read_text(encoding="utf-8").startswith("1\n")
    state = json.loads((run / "state.json").read_text())
    assert len(state["shorts"]["original"]) == 1 and len(state["shorts"]["dub"]) == 1
    profile = json.loads((fake_env["home"] / "channels" / "auto" / "profile.json").read_text())
    assert profile["voice_ref"] == "voice.wav" and profile["corrections"] == {}  # --no-review learns nothing
    heard = (fake_env["state"] / "asr-http.log").read_text(encoding="utf-8").split()
    assert "part00.wav" in heard and any("-s42.wav" in f for f in heard)  # transcription and take checks, over HTTP
    assert (run / "vocals.wav").exists() and (run / "music.wav").exists()  # separated through the Demucs wrapper

    # later: compare voice samples from this run with the channel's voice now, and pick another one
    assert main(["voice", "auto", "--json"]) == 3
    assert events_of(capsys)[-1]["waiting_for"]["kind"] == "voice"
    found = json.loads((run / "voice-candidates.json").read_text())
    assert found["version"] == 2 and found["picked"] is None and found["current"]["from"].startswith("auto, ")
    for sample in found["candidates"] + [found["current"]]:
        assert len(sample["tests"]) == 4 and all((run / t).exists() for t in sample["tests"])  # 4 test sentences
    other = found["candidates"][-1]["n"]
    assert main(["voice", "auto", "--pick", str(other)]) == 0
    profile = json.loads((fake_env["home"] / "channels" / "auto" / "profile.json").read_text())
    assert f"auto, candidate {other} " in profile["voice_from"]
    assert json.loads((run / "voice-candidates.json").read_text())["picked"] == other


def test_reviewed_run_teaches_the_channel(fake_env, capsys):
    """Nemotron and Demucs run on this machine (the local backends), only the LLMs and TTS on the server."""
    fake_env["use"](nemo="local", demucs="local")
    local_log = fake_env["state"] / "asr-local.log"
    before = local_log.read_text(encoding="utf-8") if local_log.exists() else ""
    video = str(fake_env["video"])
    assert main(["run", video, "--channel", "taught", "--from", "bg", "--to", "en", "--name", "taught", "--json"]) == 3
    result = events_of(capsys)[-1]
    assert result["status"] == "waiting" and result["exit_code"] == 3
    assert result["waiting_for"]["kind"] == "review1" and result["waiting_for"]["file"].endswith("review-1-transcript.txt")
    run = fake_env["home"] / "runs" / "taught"
    r1 = run / "review-1-transcript.txt"
    text = r1.read_text(encoding="utf-8").replace("status: pending", "status: approved", 1)
    text = text.replace("Кирчу", "Кирчо")  # the guessed name is right
    r1.write_text(text, encoding="utf-8")
    assert main(["run", "taught"]) == 3  # now waiting at review 2
    r2 = run / "review-2-translation.txt"
    text = r2.read_text(encoding="utf-8").replace("status: pending", "status: approved", 1)
    r2.write_text(text.replace("glossary:\n", "glossary:\nсирене = cheese\n", 1), encoding="utf-8")
    assert main(["run", "taught"]) == 0
    profile = json.loads((fake_env["home"] / "channels" / "taught" / "profile.json").read_text())
    assert profile["corrections"].get("найемите") == "наемите"
    assert profile["corrections"].get("Кирчу") == "Кирчо" and "Кирчо" in profile["names"]
    assert "ютубърите" in [w.lower() for w in profile["accepted_words"]]
    assert profile["glossary"] == {"сирене": "cheese"}
    memory = (fake_env["home"] / "channels" / "taught" / "memory.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(memory) > 10

    # the next video of the same channel: what was learned is applied without asking again
    assert main(["run", video, "--channel", "taught", "--name", "taught-2", "--no-review"]) == 0
    spelled = json.loads((fake_env["home"] / "runs" / "taught-2" / "spelling.json").read_text(encoding="utf-8"))
    learned = [b for r in spelled.values() for a, b in r["learned"]]
    assert "наемите" in learned and "Кирчо" in learned
    assert len(local_log.read_text(encoding="utf-8")) > len(before)  # the local nemo-speech did the transcribing
