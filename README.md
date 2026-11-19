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
(exit codes: 0 done, 3 waiting for a human, 1 error). There is also a web app on top of the same
commands (a Rust server and a React page, see [The web app](#the-web-app)): start a run, watch its
progress live, watch and download the results.

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

- **A model server: [llama-swap](https://github.com/mostlygeek/llama-swap)** on a GPU machine with
  the models localdub uses: Gemma 4 31B (text and vision) and Qwen3-TTS on llama.cpp, Nemotron 3.5
  Transcribe on `nemo-speech serve`, and Demucs through the small wrapper in `deploy/gpu/`. llama-swap
  starts and stops them as requests need them. See [deploy/gpu/README.md](deploy/gpu/README.md).
- Where localdub runs: Python ≥ 3.10, **ffmpeg** with libass, **hunspell** with the source language's
  dictionary, **DejaVu Sans**; for picking the voice by likeness also `transformers` (`.[voice]`, CPU).

Settings: create `~/dub/settings.json` with just the keys that differ from `src/localdub/settings.py`,
e.g. `{"llm_url": "http://gpu-box:9876", "translate_effort": "low"}`. The API key comes from
`$LLAMA_API_KEY` or the `LLAMA_API_KEY=` line of `/etc/llama.env`. To run Nemotron or Demucs on the
same machine as localdub instead of behind the server, set `"nemo": {"backend": "local"}` or
`"demucs": {"backend": "local"}` (paths and devices are in the same sections); localdub then frees
the GPUs before running them.

## Install

```
git clone https://github.com/ivanwe2/localdub ~/localdub
VENV=~/venvs/demucs-gpu bash ~/localdub/deploy/install.sh     # also: run it again to update
localdub check
```

## The web app

`server/` is a Rust server (axum) that runs `localdub ... --json` for you, one run at a time, and
`web/` is the page it serves. Run it on the machine where localdub runs, so both see the same files:

```
cd web && npm ci && npm run build && cd ..          # the page, into web/dist
cd server && cargo build --release && cd ..
server/target/release/localdub-server               # http://127.0.0.1:8080
```

| Setting | Default | |
|---|---|---|
| `LOCALDUB_HOME` | `~/dub` | channels and runs, the same folder the engine uses |
| `LOCALDUB_ENGINE` | `localdub` | the engine's command |
| `LOCALDUB_LISTEN` | `127.0.0.1:8080` | there is no login yet: open it to the network (`0.0.0.0:8080`) only on a trusted LAN |
| `LOCALDUB_ALLOWED_HOSTS` | | other names the browser uses to reach it, e.g. `192.168.0.71:8080` |
| `LOCALDUB_WEB` | `web/dist` | the built page |

To put it on a Linux machine (no Rust or Node needed there): CI builds the package (one static
server binary plus the page) on every merge to `main` and publishes it on the GitHub release
[`app`](https://github.com/ivanwe2/localdub/releases/tag/app). With the repo cloned to `~/localdub`
(see Install), this updates the engine and installs or updates the web app as a systemd service:

```
bash ~/localdub/deploy/update.sh                     # on the machine, as root
ssh -N -L 8080:127.0.0.1:8080 root@gpu-box           # laptop, then open http://localhost:8080
```

It listens on that machine only, hence the SSH tunnel. It won't restart the service while a run is
going (`--force` does). To build the package yourself: `bash deploy/server/build.sh` in the dev
container makes `dist/localdub-app.tar.gz`; unpack it on the machine and run its `install.sh`.

In the page you upload a video (or give a path inside the home folder), start a run, follow it live,
do the reviews (edit the text, *Approve and continue*), make and pick Shorts (tick them, *Render*),
and watch or download the results.

The API:

| | |
|---|---|
| `GET /api/runs` | every run: its stages, files, and what it waits for (a review, picking Shorts) |
| `POST /api/runs` | start one: `video`, `channel`, `name`, optional `no_review`, `shorts` |
| `GET` / `PUT /api/runs/<run>/review` | the file the run waits for: read it, save it (plain text) |
| `POST /api/runs/<run>/continue` | go on after a review or an error, or render the picked Shorts |
| `POST /api/runs/<run>/shorts` | propose Shorts for a finished run: `{"lang": "dub"}` or `"original"` |
| `GET /api/videos`, `PUT /api/videos/<file>` | the uploaded videos; upload one (the body is the file) |
| `GET /api/job`, `GET /api/job/events` | the current run, and live as Server-Sent Events |
| `GET /files/<run>/<path>` | a run's files |

One run at a time (409 while one is going). The server only answers to its own host names and
takes changes only from its own page (not from other sites open in the same browser).

## Development

```
pip install -e ".[dev]"
python -m pytest -q -m "not slow"     # unit tests, seconds
python -m pytest -q                   # plus two whole runs against fake model servers, ~2 min
cd server && cargo test               # the server, against a fake engine script
cd web && npm test                    # the page's helpers
cd web && npm run dev                 # the page with instant reload, using the server on :8080
```

The end-to-end tests run the real pipeline against stand-ins (`tests/fakes/`): a fake llama-swap
(chat, vision, TTS, transcription; separation goes on to the real `deploy/gpu/demucs_server.py`
running a fake Demucs), plus a fake `nemo-speech` for the local backend, on a generated video with
made-up Bulgarian sentences. One run uses the server for everything, the other the local backends.
They need ffmpeg, hunspell-bg and DejaVu fonts, nothing else.

## License

MIT
