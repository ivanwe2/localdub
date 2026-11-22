"""Push messages through ntfy when a long job waits for you or ends (notify.py), against a stand-in ntfy."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from localdub import notify
from localdub.run import main


@pytest.fixture
def ntfy():
    """A stand-in ntfy server: every published message, with its path and headers."""
    got = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            got.append({"path": self.path, "auth": self.headers.get("Authorization"), **body})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", got
    server.shutdown()


def test_the_message_says_what_happened():
    waits = notify.message("run", 3, {"run": "market", "waiting_for": {"kind": "review2", "file": "f"}})
    done = notify.message("run", 0, {"run": "market", "outputs": {"video": "v"}, "shorts": {"dub": ["a", "b"]}})
    fixed = notify.message("line", 0, {"run": "market", "line": {"id": 31}})
    failed = notify.message("run", 1, {"run": "market", "error": "in stage 'voice': no voice sample\nmore"})

    assert (waits["title"], waits["message"], waits["page"]) == ("market: your turn", "Review 2 is ready: the translation", "review")
    assert done["message"] == "The dubbed video is ready, and 2 Shorts"
    assert fixed["message"] == "Line 31 is fixed and the dub mixed again"
    assert (failed["title"], failed["message"]) == ("market: stopped", "in stage 'voice': no voice sample")


def test_it_is_published_to_the_topic_and_opens_the_run(ntfy, monkeypatch):
    base, got = ntfy
    monkeypatch.setenv("NTFY_TOKEN", "tk_secret")
    cfg = {"ntfy": f"{base}/my-topic", "min_seconds": 0, "page_url": "http://localhost:8080/"}

    notify.after({"notify": cfg}, "run", 3, {"run": "market", "waiting_for": {"kind": "review1"}}, seconds=5)
    notify.after({"notify": cfg}, "models", 0, {}, seconds=500)  # not a long job
    notify.after({"notify": {**cfg, "min_seconds": 60}}, "run", 3, {"run": "market"}, seconds=5)  # too quick

    assert len(got) == 1
    assert got[0]["path"] == "/" and got[0]["topic"] == "my-topic" and got[0]["auth"] == "Bearer tk_secret"
    assert got[0]["click"] == "http://localhost:8080/#/review/market" and got[0]["tags"] == ["hourglass_flowing_sand"]


def test_a_command_that_ends_sends_one_and_a_bad_address_only_logs(ntfy, tmp_path, monkeypatch, capsys):
    base, got = ntfy
    monkeypatch.setenv("LOCALDUB_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text(json.dumps({"notify": {"ntfy": f"{base}/t", "min_seconds": 0}}))

    assert main(["shorts", "nothing-here"]) == 1
    assert got[-1]["title"] == "nothing-here: stopped" and "isn't finished" in got[-1]["message"]

    assert notify.send({"ntfy": "not a url"}, notify.message("run", 0, {})) is not None
    (tmp_path / "settings.json").write_text(json.dumps({"notify": {"ntfy": "http://127.0.0.1:1/t", "min_seconds": 0}}))
    assert main(["shorts", "nothing-here"]) == 1  # ntfy unreachable: the command's own result stands
    assert "no notification sent" in capsys.readouterr().err
