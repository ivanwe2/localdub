# localdub on the command line

Everything the web app does, localdub does from a terminal, and a script or an AI agent can do it the
same way. This guide walks through the common jobs, then lists every command, the files you edit,
and what to do when something goes wrong. (For agents there's a shorter version: `localdub guide`.)

- [The idea in four words: runs, channels, reviews, exit codes](#the-idea)
- [Jobs, step by step](#jobs-step-by-step)
- [Every command](#every-command)
- [The files you edit](#the-files-you-edit)
- [What a run leaves on disk](#what-a-run-leaves-on-disk)
- [Settings](#settings)
- [When something goes wrong](#when-something-goes-wrong)

## The idea

- **A run** is one video's journey: `~/dub/runs/NAME/`. It has stages (prep, separate, transcribe,
  spelling, review 1, translate, review 2, voice, mix) and remembers which are done, so
  `localdub run NAME` always goes on where it stopped: after a review, after an error, after a
  reboot.
- **A channel** is a creator: its languages (the video's and the dub's), its voice sample, and what
  it has learned from your reviews (spellings, names, a glossary, approved lines):
  `~/dub/channels/NAME/`. Every run belongs to one.
- **Reviews** are plain text files the run stops for. You edit one, change its first line to
  `status: approved`, and run `localdub run NAME` again. `--no-review` skips them (and learns
  nothing).
- **Exit codes**: `0` done, `3` waiting for you (a review, a voice pick, Shorts to pick), `1` error.
  Messages for people go to stderr; with `--json`, stdout carries one JSON event per line.

## Jobs, step by step

### Check the setup

```
localdub check
```

Every line says `ok` or `FAIL` with what to do: ffmpeg, hunspell and each channel's dictionary, the
model servers and every task's model, fonts, the speaker model. Fix the FAIL lines first.

### A channel's first video

```
localdub run talk.mp4 --channel mychannel --from de --to en --speaker woman
```

`--from` / `--to` are the video's language and the dub's (`localdub languages` lists them); they're
fixed for the channel from now on. `--speaker man|woman` is optional and only helps gendered
languages get "I was tired" right. The run name comes from the file name (`--name` to choose one).

It stops at **review 1** (the transcript, and on a channel's first video the voice sample):

```
== Review 1 of 2 is ready (the transcript)
   /root/dub/runs/talk/review-1-transcript.txt
```

Edit it ([format](#review-1-the-transcript)), set `status: approved`, then:

```
localdub run talk
```

It translates and stops at **review 2** (the translation, [format](#review-2-the-translation)).
Approve it the same way and run `localdub run talk` again: the voice and the mix follow, and you get
`talk.en.mp4` (the dub as the first audio track, the original as the second), subtitles in both
languages, and `check.txt`, the lines worth a listen.

### The next videos of the channel

```
localdub run next.mp4 --channel mychannel
localdub run next.mp4 --channel mychannel --no-review --shorts 3
```

No languages needed now. What you fixed before is applied without asking. The second line goes
straight through and also renders the top 3 Shorts in both languages.

### Only an excerpt

```
localdub run talk.mp4 --channel mychannel --start 1:20 --duration 2:30 --name talk-test
```

Good for trying a channel's voice and translation before a whole video.

### Shorts only (no dub)

```
localdub run talk.mp4 --channel clips --from de --shorts-only
localdub run talk.mp4 --channel clips --from de --shorts-only --no-review --shorts 3
```

Separate, transcribe, spelling, one review (the transcript, which becomes the captions), then Shorts
in the video's language. Without `--shorts N` it stops for you to pick them
([format](#shorts-candidates)); with it, the top N are rendered. A new channel needs only `--from`; it
gets its dub language once, on its first dub (`--to`).

### Shorts afterwards

```
localdub shorts talk                    # the dubbed video's Shorts (for a Shorts-only run: the original's)
localdub shorts talk --lang original    # cut from the original audio, captions in its language
localdub shorts talk --auto 3           # render the top 3 without stopping
localdub shorts talk --redo select      # propose new candidates; --redo vision looks at the video again
```

It proposes up to 6 candidates and waits for you to tick some in
`runs/talk/shorts/<lang>/candidates.txt`; run the same command again to render them into
`runs/talk/shorts/<lang>/`.

### Another voice, and dubbing again

```
localdub voice talk                     # 3 voice samples from this run, plus the channel's voice now
localdub voice talk --pick 2            # make candidate 2 the channel's voice (or current, or 01:20-01:38)
localdub run talk --redo voice          # dub the run again with it
```

`localdub voice` clones four test sentences from each sample and scores how much they sound like
the speaker (`runs/talk/voice-candidates/`): listen, then pick. Differences under 0.01 are noise;
trust your ear. A pick changes the channel's voice for every future run.

### Fix one line after the dub

```
localdub line talk 31                                     # show line 31: both texts, what the check heard, how it fits
localdub line talk 31 --text "The market opens at six."   # new words: voiced, checked, the dub mixed again
localdub line talk 31 --new-take                          # the same words, voiced again with new seeds
localdub line talk 31 --text ""                           # leave the line silent
```

`check.txt` lists the lines worth a listen (failed checks, lines that run long). A fix voices only that
line, mixes the dub again (a minute or two) and renders again the dubbed Shorts the line is in. New
words are remembered by the channel, as review 2 edits are. In the web app: *Fix a line* under the
timeline of a finished dub, or click a line on the timeline.

### After an error

```
localdub run talk                       # go on: finished stages are kept
localdub run talk --redo translate      # throw away translate and everything after it, then go on
```

The error says which stage stopped and why. Fix the cause and continue.

### Models: which one does what

```
localdub models                                             # servers, the model doing each task, thinking levels
localdub models --test voice                                # try a task with one small real request
localdub models --effort translate=low                      # translation thinks less (much faster)
localdub models --server studio --url http://studio-pc.lan:1234 --kind openai
localdub models --set text=studio/qwen3-32b                 # the text task on that server
localdub models --server cloud --url https://api.example.com --kind openai --key-stdin < key.txt
localdub models --server cloud --remove                     # (once no task uses it)
```

The five tasks are text, vision, voice, hearing and separation (the README's "Models" table says what
each needs). A server is `llama-swap` or `openai` (any OpenAI-compatible server). Keys are read from
stdin and kept in `~/dub/keys.json`, readable only by you; they're never printed. Moving a server to
another address forgets its saved key. Changes apply to the next run, not one already going.

## Every command

All of them take `--json`.

| Command | What it does |
|---|---|
| `localdub check` | is everything in place? (exit 1 if a line fails) |
| `localdub languages [--apt LANG… \| all]` | the languages localdub knows, which can be dubbed into, which dictionaries are installed; `--apt` prints the Debian packages for install scripts |
| `localdub run VIDEO --channel NAME [--from L --to L] [--speaker man\|woman] [--start T --duration T] [--name NAME] [--no-review] [--shorts N] [--shorts-only]` | start a run |
| `localdub run NAME [--redo STAGE]` | continue a run (or redo from a stage on) |
| `localdub shorts NAME [--lang dub\|original] [--auto N] [--redo select\|vision]` | propose Shorts from a finished run, then render the ticked ones |
| `localdub voice NAME [--pick N\|current\|MM:SS-MM:SS\|file.wav]` | compare voice samples from a run, pick one for the channel |
| `localdub line NAME ID [--text WORDS \| --text-stdin] [--new-take]` | show one line of a finished dub, or fix it: new words or another take; the dub and its dubbed Shorts are made again |
| `localdub channel NAME [--speaker man\|woman\|none]` | a channel's languages, speaker and voice; change the speaker |
| `localdub models [--server NAME --url URL --kind K --key-stdin \| --remove] [--set TASK=SERVER/MODEL…] [--effort USE=LEVEL…] [--test TASK]` | which model does each task, on which server; change them; try one |
| `localdub guide` | the guide for AI agents |

Times are `mm:ss` or seconds. Stages for `--redo`: prep, separate, transcribe, spelling, review1,
translate, review2, voice, mix (a Shorts-only run: up to review1). Thinking uses for `--effort`:
spelling, translate, shorten, shorts; levels: default (not sent), none, low, medium, high.

## The files you edit

Each starts with `status: pending`. Change it to `status: approved` when you're done; lines starting
with `#` are comments. Keep the `[id]` lines as they are.

### Review 1: the transcript

Only the doubtful lines: automatic fixes, guessed names, words the dictionary doesn't know.

```
status: pending
# Review 1 of 2: the transcript.
voice: auto

[9] 01:14.6   name? Кирчу -> Кирчо (not applied)   unknown: ютубъри
BG: Хлебарят Кирчу ни дава да опитаме от питката.

[12] 01:46.3   fixed: найемите -> наемите
BG: После минаваме покрай наемите сергии на пазара.
```

- Fix the text after `BG:` (the video's language code). A fix you make is learned for the channel; an
  unknown word you leave as it is is accepted from now on.
- `voice:` (on a channel's first video): `auto` takes the best-scored sample listed above it; or write
  its number (`voice: 2`), a stretch of the video (`voice: 01:20-01:38`, 10-20 s of clear speech) or
  a WAV file's path.

### Review 2: the translation

Every line, with its time slot and a character budget from how long the speaker took to say it.

```
status: pending
glossary:
сирене = cheese

[3] 00:18.0   slot 8.0s   41/120 chars
BG: Тази жена идва тук от четиридесет години.
EN: This woman has come here for forty years.
```

- Edit the `EN:` lines (the dub's code). `OVER` marks a line over its budget: shorter is better, a
  long line gets sped up a little. An empty dub line stays silent.
- Terms the translator should always use go under `glossary:` as `source term = dub term`. Every
  approved line becomes an example for this channel's future translations.

### Shorts candidates

```
status: pending
[x] 1   00:02-00:35 (33s)   sentences 1-4   "The secret is in the milk"
    hook:   Good morning from the Saturday market.
    why:    A whole little story with a strong first line.
[ ] 2   01:04-01:33 (29s)   sentences 8-10   "Mountain cheese and linden honey"
```

Tick the ones to render with `[x]`. To cut one differently, change its `sentences A-B` (the line
numbers in `check.txt`); you can change its title too.

## What a run leaves on disk

`~/dub/runs/NAME/`:

| File | What |
|---|---|
| `state.json` | the stages done, their timings, the outputs |
| `NAME.LANG.mp4`, `NAME.*.srt` | the dubbed video and the subtitles in both languages |
| `check.txt` | lines worth a listen (failed checks, lines that run into the next) |
| `review-1-transcript.txt`, `review-2-translation.txt` | the reviews |
| `segments.json` | every line: times, the original, the translation |
| `vocals.wav`, `music.wav` | the speaker's voice and everything else |
| `voice-candidates/`, `voice-candidates.json` | voice samples and their test clones |
| `voice/` | every take of every line (reused when you redo the voice) |
| `shorts/<lang>/` | the candidates file and the rendered Shorts |

`~/dub/channels/NAME/`: `profile.json` (languages, speaker, corrections, names, glossary),
`voice.wav` (the voice sample), `memory.jsonl` (approved lines).

## Settings

`~/dub/settings.json` holds only what differs from `src/localdub/settings.py`; `localdub models`
writes its part for you. The ones you're most likely to touch:

| Key | Default | |
|---|---|---|
| `llm_url` | `http://127.0.0.1:9876` | the main server (llama-swap) |
| `translate_effort` | `medium` | how hard the translation thinks (`localdub models --effort`) |
| `tts_languages` | en, de, fr, it, pt, es, ru | what channels can be dubbed into |
| `nemo.backend`, `demucs.backend` | `http` | `local`: run Nemotron or Demucs on this machine instead |
| `shorts.count`, `shorts.min_seconds`, `shorts.max_seconds` | 6, 25, 60 | how many Shorts are proposed, and how long |

## When something goes wrong

| What you see | What to do |
|---|---|
| `the German spelling dictionary (de_DE) isn't installed` | `apt-get install hunspell-de-de` (the message names the package), or `bash deploy/install.sh` for all of them |
| `is llama-swap running at …?` | start the model server; `localdub check` and `localdub models` show which server a task is on |
| `HTTP 401` | the server's key: `$LLAMA_API_KEY` / `/etc/llama.env`, or `localdub models --server NAME --key-stdin` |
| a model isn't listed (`check` FAIL) | add it to the server (the Models screen's *What it needs* has a llama-swap entry), or `--set` another |
| `no valid JSON after 3 tries` | the text model struggles with the format: try `--effort translate=low` or a bigger model |
| `/tts returned no audio` | the voice server isn't Qwen3-TTS on llama.cpp's TTS server; `localdub models --test voice` |
| `Nemotron heard no speech` | the video has no speech in the separated voice, or the wrong video language |
| out of GPU memory | two big models at once: on llama-swap put them in one swapping group (deploy/gpu/llama-swap.yaml) |
| a run waits but the page shows nothing to do | `localdub run NAME` says what it waits for and where the file is |
