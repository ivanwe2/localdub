# localdub

Dub a video into another language in the creator's own cloned voice, keep the original music and
effects, and cut Shorts from it, in the dubbed language or the original. Everything runs on your
own GPUs: no cloud APIs.

```
localdub run video.mp4 --channel mychannel                 dub it, stopping twice for your review
localdub run video.mp4 --channel mychannel --no-review --shorts 3
                                                           everything in one go, plus the top 3 Shorts
                                                           in each language
```

It is a command-line tool on purpose: a person, a script or an AI agent can drive it the same way
(exit codes: 0 done, 3 waiting for a human, 1 error). A web UI (Rust server + React) is being built
on top of the same commands.

> **Consent.** Only clone the voice of someone who has agreed to it, and only process videos you
> have the rights to. YouTube lists "cloning one's own voice to create voice overs or dubs" among the
> uses that don't need an AI disclosure label, which covers creators dubbing their own videos.

## How it works

| Stage | What happens | Model / tool |
|---|---|---|
| prep | cut an excerpt if asked, extract the audio | ffmpeg |
| separate | split the voice from the music and effects, so the dub can replace the voice and keep the rest | Demucs `htdemucs_ft` |
| transcribe | every word with its time; long videos in parts of up to 25 min, cut at pauses | Nemotron 3.5 Transcribe via NeMo-Speech.cpp |
| spelling | words → sentences by their times; spelling fixes, allowed **only** for words hunspell says are misspelled, so real words are never "corrected" | Gemma 4 31B (llama.cpp) |
| **review 1** | you check the doubtful lines and choose the voice sample | a text file |
| translate | whole sentences, 20 at a time with context; each gets a length budget from how long he took to say it; the channel's names, glossary and past approved lines are given as examples | Gemma 4 31B |
| **review 2** | you check the translation | a text file |
| voice | every line in his cloned voice; each take is transcribed back and checked; takes that fail, don't fit, or sound less like him are replaced by another seed; lines too long in every take are shortened and voiced again | Qwen3-TTS (llama.cpp), Nemotron, WavLM |
| mix | lines placed where he spoke (a line may start up to 1 s late so it never talks over the one before), the voice levelled like his original, laid over the music; video with the dub as the default track and the original as the second; subtitles | ffmpeg, numpy |

**Choosing the voice.** A speaker-verification model (WavLM x-vectors) turns a few seconds of speech
into a fingerprint; recordings of the same person point in nearly the same direction, whatever the
language. localdub builds his voiceprint from his real speech in the video, then
- picks the voice sample: up to three clean stretches are found, a few test sentences are cloned
  from each, and the sample whose clones sound most like him wins;
- picks the takes: each line gets two good takes (passing the check, fitting the slot) and keeps the
  one that sounds more like him.

**Learning per channel.** Approved reviews teach the channel (`~/dub/channels/NAME/`): spelling
corrections you made or kept are applied next time without asking, unknown words you left are
accepted, names and the glossary go to the translator, and every approved line becomes an example
for future translations (`memory.jsonl`). `--no-review` runs learn nothing.

## Commands

```
localdub check                                   is everything in place?
localdub run VIDEO --channel NAME                dub a whole video
          [--start 1:00 --duration 2:30]         only an excerpt
          [--name NAME] [--no-review] [--shorts N]
localdub run NAME                                continue a run (after a review or an error)
localdub run NAME --redo STAGE                   redo from a stage on
localdub shorts NAME [--lang dub|original] [--auto N] [--redo select|vision]
```

### The two reviews

A run stops at each review and prints the file to edit; change its first line to `status: approved`
and run `localdub run NAME` again.

- **Review 1, the transcript**: only doubtful lines (new automatic fixes, guessed names, unknown
  words). On a channel's first video it lists the candidate voice samples with their "sounds like
  him" scores: keep `voice: auto` (the best), write a number, a stretch (`voice: 01:20-01:38`) or a
  WAV path. Nothing doubtful and a known voice: no stop.
- **Review 2, the translation**: every line with its time slot and character budget; an empty `EN:`
  line stays silent; terms go under `glossary:` as `хлебарки = cockroaches`.

### Shorts

From a finished run: ffmpeg measures shot cuts and movement; Gemma looks at the video (a frame every
4 s, four per image) and rates how interesting each stretch is to watch; then Gemma reads the speech
with what's on screen and proposes 6 candidates of 25-60 s that stand alone, hook early and end on a
payoff, strongest first. Mark the ones you want in `shorts/<lang>/candidates.txt` (or `--auto N`).
Each Short is 1080x1920: the full frame over a blurred copy of itself, the title on top and big
burned-in captions. `--lang original` cuts from the original audio with captions timed by the real
word timestamps, in whatever language the channel speaks.

### For scripts, servers and agents: `--json`

Every command takes `--json`. Messages for people stay on stderr; stdout then carries one JSON object
per line, and the last line is always the result:

```
{"event": "start", "command": "run", "run": "market", "channel": "mychannel", "dir": "...", "stages": [...], "done": []}
{"event": "stage", "stage": "separate", "status": "started"}
{"event": "stage", "stage": "separate", "status": "done", "seconds": 19.3}
{"event": "result", "status": "waiting", "exit_code": 3, "run": "market",
 "waiting_for": {"kind": "review1", "file": ".../review-1-transcript.txt"}}
```

`status` is `done`, `waiting` (`waiting_for.kind`: `review1`, `review2` or `shorts`) or `error` (with
`error` and, if a stage failed, `stage`). A finished run's result has `outputs`, `shorts` and `timings`;
`check --json` sends one `check` event per line it checks. See `src/localdub/events.py`.

## Requirements

Built and run on a Proxmox LXC with 2× RTX 5060 Ti 16 GB; a 30-minute video takes about 30 minutes.

- **llama.cpp** server in router mode (`--models-preset`) with these presets: a text model
  (`gemma4-31b`), the same model with its vision projector (`gemma4-31b-vision`, `no-mmproj = false`,
  8K context) and Qwen3-TTS (`qwen3-tts`, `ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF`). localdub unloads
  models itself when other tools need the GPUs.
- **NeMo-Speech.cpp** (nightly) with Nemotron 3.5 Transcribe and a config that fits your VRAM.
- A **Python venv with CUDA torch and Demucs** (`pip install demucs numpy`); localdub installs into it.
- **ffmpeg** with libass, **hunspell** with the source language's dictionary, **DejaVu Sans**.

Paths and model names are settings: create `~/dub/settings.json` with just the keys that differ
from `src/localdub/settings.py` (e.g. `{"translate_effort": "low", "demucs": {"device": "cuda:1"}}`).
The llama API key comes from `$LLAMA_API_KEY` or the `LLAMA_API_KEY=` line of `/etc/llama.env`.

## Install

```
git clone https://github.com/ivanwe2/localdub ~/localdub
VENV=~/venvs/demucs-gpu bash ~/localdub/deploy/install.sh     # also: run it again to update
localdub check
```

## Development

```
pip install -e ".[dev]"
python -m pytest -q -m "not slow"     # unit tests, seconds
python -m pytest -q                   # plus two whole runs against fake model servers, ~2 min
```

The end-to-end tests run the real pipeline against stand-ins (`tests/fakes/`): a fake llama.cpp
router (chat, vision, TTS), a fake `nemo-speech` and a fake Demucs, on a generated video with made-up
Bulgarian sentences. They need ffmpeg, hunspell-bg and DejaVu fonts, nothing else.

## License

MIT
