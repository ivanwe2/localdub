"""The voice picker: candidates keep their numbers, picks are recorded, old candidate files still work."""
import json

import pytest

from localdub.run import Run, load_candidates, main, set_voice, write_json
from localdub.settings import Profile


@pytest.fixture
def run(tmp_path, monkeypatch):
    """A run with two candidate samples; candidate 2 sounds more like the speaker, so it's listed first."""
    monkeypatch.setenv("LOCALDUB_HOME", str(tmp_path))
    r = Run("market")
    (r.dir / "voice-candidates").mkdir(parents=True)
    for n in (1, 2):
        r.p(f"voice-candidates/candidate-{n}.wav").write_bytes(f"sample {n}".encode())
    write_json(r.p("voice-candidates.json"), {
        "version": 2, "lang": "en", "lines": [], "scored": True, "current": None, "picked": None,
        "candidates": [
            {"n": 2, "start": 40.0, "end": 52.0, "path": "voice-candidates/candidate-2.wav", "sim": 0.97, "tests": []},
            {"n": 1, "start": 10.0, "end": 22.5, "path": "voice-candidates/candidate-1.wav", "sim": 0.91, "tests": []},
        ]})
    return r


def picked(run):
    return json.loads(run.p("voice-candidates.json").read_text())["picked"]


def test_a_candidate_keeps_its_number_whatever_the_order(run):
    profile = Profile("friend")

    source = set_voice(run, profile, "1")

    assert (profile.dir / "voice.wav").read_bytes() == b"sample 1"
    assert source == "market, candidate 1 (00:10.0-00:22.5, sounds like the speaker 0.91)"
    assert picked(run) == 1


def test_auto_takes_the_best_and_an_unknown_number_is_refused(run):
    profile = Profile("friend")

    set_voice(run, profile, "auto")

    assert (profile.dir / "voice.wav").read_bytes() == b"sample 2" and picked(run) == 2
    with pytest.raises(RuntimeError, match=r"no voice candidate 5 \(there are: 2, 1\)"):
        set_voice(run, profile, "5")


def test_a_version_1_file_is_numbered_as_review_1_numbered_it(run):
    old = [{"start": 40.0, "end": 52.0, "path": str(run.p("voice-candidates/candidate-2.wav")), "sim": 0.97},
           {"start": 10.0, "end": 22.5, "path": str(run.p("voice-candidates/candidate-1.wav")), "sim": 0.91}]
    write_json(run.p("voice-candidates.json"), old)  # sorted by score, numbered by position

    assert [c["n"] for c in load_candidates(run)["candidates"]] == [1, 2]
    set_voice(run, Profile("friend"), "2")
    assert (Profile("friend").dir / "voice.wav").read_bytes() == b"sample 1"


def test_the_voice_command_needs_a_transcribed_run_and_current_needs_a_voice(run, capsys):
    run.state = {"channel": "friend", "done": ["prep"]}
    run.save()

    assert main(["voice", "nothing-here"]) == 1
    assert main(["voice", "market"]) == 1
    assert "isn't transcribed yet" in capsys.readouterr().err
    (run.p("spelled.json")).write_text("[]")
    (run.p("vocals.wav")).write_bytes(b"")
    assert main(["voice", "market", "--pick", "current"]) == 1
    assert main(["voice", "market", "--pick", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["voice"].startswith("market, candidate 2")
    assert main(["voice", "market", "--pick", "current"]) == 0 and picked(run) == "current"
