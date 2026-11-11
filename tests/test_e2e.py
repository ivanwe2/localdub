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


def test_automatic_run_with_shorts(fake_env):
    rc = main(["run", str(fake_env["video"]), "--channel", "auto", "--name", "auto", "--no-review", "--shorts", "1"])
    assert rc == 0
    run = fake_env["home"] / "runs" / "auto"
    assert streams(run / "auto.en.mp4") == ["video", "audio", "audio"]
    assert (run / "auto.en.srt").read_text(encoding="utf-8").startswith("1\n")
    state = json.loads((run / "state.json").read_text())
    assert len(state["shorts"]["original"]) == 1 and len(state["shorts"]["dub"]) == 1
    profile = json.loads((fake_env["home"] / "channels" / "auto" / "profile.json").read_text())
    assert profile["voice_ref"] == "voice.wav" and profile["corrections"] == {}  # --no-review learns nothing


def test_reviewed_run_teaches_the_channel(fake_env):
    video = str(fake_env["video"])
    assert main(["run", video, "--channel", "taught", "--name", "taught"]) == 3
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
