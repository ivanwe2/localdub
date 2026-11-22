"""A stand-in for llama-swap's status routes, for the screenshots: the two voicing models loaded and
two GPUs at work. Usage: python3 fake_llama_swap.py <port>"""
import json
import random
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUNNING = {"running": [
    {"model": "qwen3-tts", "state": "ready", "name": "Qwen3-TTS 1.7B Base", "description": "the cloned voice, on GPU 0"},
    {"model": "nemotron", "state": "ready", "name": "Nemotron 3.5 Transcribe",
     "description": "transcription with word times and the take check, on GPU 1"},
]}
GPU = 'id="{i}",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-{i}{i}{i}{i}"'


def metrics() -> str:
    gpus = [(0, random.uniform(78, 92), 4.6e9, 61, 148.0), (1, random.uniform(35, 55), 14.5e9, 54, 96.0)]
    out = ["llamaswap_memory_total_bytes 31977373696", "llamaswap_memory_used_bytes 9292528640"]
    for i, util, used, temp, watts in gpus:
        g = GPU.format(i=i)
        out += [f"llamaswap_gpu_util_percent{{{g}}} {util:.1f}", f"llamaswap_gpu_memory_used_bytes{{{g}}} {used}",
                f"llamaswap_gpu_memory_total_bytes{{{g}}} 17103323136", f"llamaswap_gpu_temperature_celsius{{{g}}} {temp}",
                f"llamaswap_gpu_power_draw_watts{{{g}}} {watts + random.uniform(-6, 6):.1f}"]
    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/running":
            body, kind = json.dumps(RUNNING).encode(), "application/json"
        elif self.path == "/metrics":
            body, kind = metrics().encode(), "text/plain"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
