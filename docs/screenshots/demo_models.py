"""The demo engine's `localdub models` answer, for the screenshots: the real task table (what each
task needs) with two made-up servers. Usage: python3 demo_models.py models [--test TASK] --json"""
import json
import sys

from localdub import models

GPU_BOX = [("gemma4-31b", "Gemma 4 31B", "text for localdub (spelling, translation, Shorts selection)"),
           ("gemma4-31b-vision", "Gemma 4 31B vision", "looks at video frames for Shorts"),
           ("qwen3-tts", "Qwen3-TTS 1.7B Base", "voice clone on GPU 0"),
           ("nemotron", "Nemotron 3.5 Transcribe", "transcription with word times and the take check"),
           ("demucs", "Demucs htdemucs_ft", "splits the voice from music and effects"),
           ("qwen38-fast", "Qwen3.8 fast", "everyday chat and agents")]
STUDIO_PC = [("qwen3-32b", None, None), ("gemma-3-27b-it", None, None)]


def server(name, url, kind, listed, key_source):
    return {"name": name, "url": url, "kind": kind, "key_set": key_source is not None, "key_source": key_source,
            "remote": False, "reachable": True, "error": None,
            "models": [{"id": i, "name": n, "description": d} for i, n, d in listed]}


if "--test" in sys.argv:
    task = sys.argv[sys.argv.index("--test") + 1]
    print(json.dumps({"event": "models_test", "task": task, "server": "main", "model": "gemma4-31b", "ok": True,
                      "seconds": 1.8, "detail": 'answered {"ok": true, "word": "market"}', "tok_per_s": 104.2, "file": None}))
    print(json.dumps({"event": "result", "status": "done", "exit_code": 0}))
    sys.exit(0)

picks = {"text": ("main", "gemma4-31b"), "vision": ("main", "gemma4-31b-vision"), "voice": ("main", "qwen3-tts"),
         "hearing": ("main", "nemotron"), "separation": ("main", "demucs")}
tasks = [{"task": name, "title": t.title, "what": t.what, "used_by": list(t.used_by), "needs": t.needs,
          "examples": list(t.examples), "snippet": t.snippet, "server": picks[name][0], "model": picks[name][1],
          "listed": True} for name, t in models.TASKS.items()]
print(json.dumps({"event": "models",
                  "servers": [server("main", "http://gpu-box:9876", "llama-swap", GPU_BOX, "LLAMA_API_KEY"),
                              server("studio-pc", "http://studio-pc.lan:1234", "openai", STUDIO_PC, None)],
                  "tasks": tasks,
                  "efforts": {"spelling": "none", "translate": "medium", "shorten": "none", "shorts": "low"},
                  "effort_levels": list(models.EFFORT_LEVELS), "kinds": list(models.KINDS)}))
print(json.dumps({"event": "result", "status": "done", "exit_code": 0}))
