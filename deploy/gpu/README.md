# The GPU box

localdub itself needs no GPU. The models run on a GPU machine behind
[llama-swap](https://github.com/mostlygeek/llama-swap), which gives them one URL and one API key and
starts or stops each model's server as requests need it. On a box with two 16 GB GPUs the models take
turns: the 31B models and Demucs need the whole box, while the voice clone (GPU 0) and its check
(GPU 1) run side by side. The `routing` groups in `llama-swap.yaml` encode that.

| Model name | Server | Used for | Called on |
|---|---|---|---|
| `gemma4-31b` | llama.cpp | spelling, translation, Shorts selection | `/v1/chat/completions` |
| `gemma4-31b-vision` | llama.cpp | looking at video frames | `/v1/chat/completions` |
| `qwen3-tts` | llama.cpp | voice clone | `/upstream/qwen3-tts/tts` |
| `nemotron` | `nemo-speech serve` | transcription with word times, take check | `/v1/audio/transcriptions` |
| `demucs` | `demucs_server.py` (this folder) | voice / music separation | `/upstream/demucs/separate` |

## Install

Needs llama.cpp (`llama-server`), NeMo-Speech (`nemo-speech`) with the Nemotron model, and a Python
venv with Demucs and CUDA torch. Paths are set as macros at the top of `llama-swap.yaml`.

```
# llama-swap: download a release, check it against the published checksums
cd ~/llama-swap
curl -fsSLO https://github.com/mostlygeek/llama-swap/releases/download/v272/llama-swap_272_linux_amd64.tar.gz
curl -fsSLO https://github.com/mostlygeek/llama-swap/releases/download/v272/llama-swap_272_checksums.txt
sha256sum --ignore-missing -c llama-swap_272_checksums.txt && tar xzf llama-swap_272_linux_amd64.tar.gz

# config and service (the config refers to demucs_server.py in a clone of this repo)
cp ~/localdub/deploy/gpu/llama-swap.yaml ~/llama-swap/config.yaml
cp ~/localdub/deploy/gpu/llama-swap.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now llama-swap
curl -s http://127.0.0.1:9876/v1/models -H "Authorization: Bearer $LLAMA_API_KEY" | jq -r '.data[].id'
```

`/etc/llama.env` (mode 600) sets `LLAMA_API_KEY` (clients must send it) and `LLAMA_CACHE` (where
llama.cpp keeps downloaded models). `GET /running` lists the servers that are up,
`POST /api/models/unload` stops them all.
