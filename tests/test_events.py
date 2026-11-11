"""The --json contract: one JSON object per line on stdout, flushed at once, result last."""
import json
import sys

import pytest

from localdub import events


class Pipe:
    """Stands in for stdout when it's a pipe: remembers what was written and what was flushed."""

    def __init__(self):
        self.written = ""
        self.flushed = ""

    def write(self, s):
        self.written += s
        return len(s)

    def flush(self):
        self.flushed = self.written


def capture(monkeypatch):
    pipe = Pipe()
    monkeypatch.setattr(sys, "stdout", pipe)
    return pipe


def test_one_event_is_one_line_of_json(monkeypatch):
    pipe = capture(monkeypatch)
    events.configure(True)
    events.emit("stage", stage="voice", status="started", note="line 1\nline 2")
    events.emit("start", run="пазар", dir="/root/dub/runs/пазар")
    lines = pipe.written.splitlines()
    assert len(lines) == 2, "each event must be exactly one line, even when a value contains a newline"
    assert json.loads(lines[0]) == {"event": "stage", "stage": "voice", "status": "started", "note": "line 1\nline 2"}
    assert json.loads(lines[1])["run"] == "пазар"
    assert "пазар" in lines[1], "Cyrillic should stay readable (ensure_ascii=False)"


def test_events_leave_immediately(monkeypatch):
    pipe = capture(monkeypatch)
    events.configure(True)
    events.emit("stage", stage="prep", status="started")
    assert pipe.flushed == pipe.written, "the event is still sitting in a buffer: flush it"


def test_nothing_without_json(monkeypatch):
    pipe = capture(monkeypatch)
    events.configure(False)
    events.emit("stage", stage="prep", status="started")
    events.finish(0)
    assert pipe.written == ""


def test_an_error_ends_with_an_error_result(tmp_path, monkeypatch, capsys):
    pytest.importorskip("numpy")  # the CLI needs numpy; this test file otherwise doesn't
    from localdub.run import main
    monkeypatch.setenv("LOCALDUB_HOME", str(tmp_path))
    assert main(["run", str(tmp_path / "missing.mp4"), "--json"]) == 1
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["status"] == "error" and "not found" in result["error"]


def test_result_status_from_exit_code(monkeypatch):
    for code, status in ((0, "done"), (3, "waiting"), (1, "error"), (2, "error")):
        pipe = capture(monkeypatch)
        events.configure(True)
        events.note(run="market", waiting_for={"kind": "review1", "file": "r1.txt"})
        events.finish(code)
        result = json.loads(pipe.written.splitlines()[-1])
        assert result["event"] == "result" and result["status"] == status and result["exit_code"] == code
        assert result["run"] == "market" and result["waiting_for"]["kind"] == "review1"
