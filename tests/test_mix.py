import subprocess

from localdub.mix import place


def tone(path, seconds):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=220:duration={seconds}", "-ar", "24000", "-ac", "1", str(path)], check=True)


def test_a_line_that_runs_over_pushes_the_next_one(tmp_path):
    a, b, c = tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "c.wav"
    tone(a, 2.5)
    tone(b, 1.0)
    tone(c, 1.0)
    segs = [{"id": 1, "start": 0.0, "end": 2.0, "take": {"seconds": 2.5, "file": str(a)}},
            {"id": 2, "start": 2.0, "end": 3.0, "take": {"seconds": 1.0, "file": str(b)}},
            {"id": 3, "start": 3.2, "end": 4.0, "take": {"seconds": 1.0, "file": str(c)}}]
    result = place(segs, 6.0, tmp_path / "out.wav")
    assert segs[1]["placed"] == 2.58          # after line 1 and a short gap, not on top of it
    assert segs[2]["placed"] == 3.66          # the delay is passed on, still within 1 s
    assert result == {"delayed": 2, "overlapping": 0}
