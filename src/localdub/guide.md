# localdub: the guide for agents

localdub dubs a video into another language in the creator's own cloned voice (keeping the music and
effects) and cuts vertical Shorts from it. You drive it with shell commands; it runs for minutes to
an hour and stops when a person (or you) has to decide something.

## Rules

- Only clone the voice of someone who has agreed to it, and only process videos the person has the
  rights to. If that's unclear, ask before starting.
- One run at a time: the models share the GPUs. Don't start a second `localdub run` while one is
  going, and don't change models or settings while a run is going (`localdub models` changes apply
  to the next run).
- Never print, log or pass API keys on a command line: `localdub models --server NAME --key-stdin`
  reads the key from stdin.

## The loop

1. `localdub check --json`: every `check` event has `ok`, `what`, `hint`. Fix failures (the hint says
   how) before running.
2. Start: `localdub run VIDEO --channel NAME --json` (a new channel also needs `--from LANG --to LANG`;
   `localdub languages --json` lists codes and which can be dubbed into). Add `--no-review` to go
   straight through, `--shorts N` for the top N Shorts in both languages, `--shorts-only` for Shorts
   without a dub (then only `--from` for a new channel).
3. Read stdout line by line: one JSON event per line, the last is always `result`.
4. Act on the exit code:
   - `0` done: `result.outputs.video`, `result.outputs.subtitles`, `result.shorts`.
   - `3` waiting: `result.waiting_for.kind` and `.file`. Edit that file as below (or hand it to the
     person), set its first line to `status: approved`, then continue with `localdub run NAME --json`
     (for `kind: shorts`: `localdub shorts NAME --lang LANG --json`, `LANG` from `waiting_for.lang`;
     for `kind: voice`: `localdub voice NAME --pick N --json`).
   - `1` error: `result.error` says what (and `result.stage` where). Fix the cause, then
     `localdub run NAME --json` continues; finished stages are kept.

## Events (`--json`)

```
{"event": "start", "command": "run", "run": "talk", "channel": "c", "mode": "dub", "stages": [...], "done": []}
{"event": "stage", "stage": "separate", "status": "started"}
{"event": "stage", "stage": "separate", "status": "done", "seconds": 19.3}
{"event": "progress", "stage": "voice", "label": "voicing, take 1", "done": 31, "total": 58, "line": 31, "at": 72.4}
{"event": "call", "kind": "chat", "model": "gemma4-31b", "server": "main", "label": "translating 1-20", "seconds": 24.1, "tok_per_s": 103.2}
{"event": "result", "status": "waiting", "exit_code": 3, "run": "talk", "waiting_for": {"kind": "review1", "file": ".../review-1-transcript.txt"}}
```

`status` is `done`, `waiting` or `error`. Progress events let you report how far it is (`done` of
`total`); `call` events show which model is working and how fast.

## The files a run waits on

All start with `status: pending`; `#` lines are comments; keep `[id]` lines unchanged.

**Review 1** (`review-1-transcript.txt`, kind `review1`): only doubtful lines of the transcript,
marked with the video's language code (`DE:`). Each `[id]` line notes `fixed: a -> b` (an automatic
fix), `name? a -> b (not applied)` (a guessed name) or `unknown: word` (not in the dictionary). Fix
the text line if the transcript is wrong; leave correct words as they are (they're learned as
correct). On a channel's first video there's a `voice:` line: `auto` (the best-scored sample), a
candidate's number, a stretch `MM:SS-MM:SS` (10-20 s of clear speech) or a WAV path. If unsure about
a name or a voice, ask the person: the voice is the channel's from then on.

**Review 2** (`review-2-translation.txt`, kind `review2`): every line as `[id] time slot Ns n/budget
chars [OVER]`, then the original (`DE:`) and the dub (`EN:`). Edit dub lines only. Keep them within
the budget (`OVER` lines get sped up). An empty dub line stays silent. Terms to always translate one
way go under `glossary:` as `source = dub`.

**Shorts candidates** (`shorts/<lang>/candidates.txt`, kind `shorts`): lines like
`[ ] 1   00:02-00:35 (33s)   sentences 1-4   "Title"` with hook/why/screen notes. Tick with `[x]`
(about 3 is good); `sentences A-B` and the title can be changed.

**Voice** (kind `voice`, after `localdub voice NAME`): `voice-candidates.json` has candidates
(`n`, `start`, `end`, `sim` = how much test clones sound like the speaker, 0-1) and the channel's
`current` voice. Differences under 0.01 are noise: let the person listen if it's close. Pick with
`localdub voice NAME --pick N` (or `current`), then `localdub run NAME --redo voice` to re-dub.

**Fixing a finished dub**: `check.txt` lists the lines worth a listen. Fix one with
`localdub line NAME ID --text-stdin --json` (the words on stdin, so words starting with `-` are safe)
or `--new-take`; ask the person before changing words they approved. Exit 0 = done.

## Commands

| Command | |
|---|---|
| `localdub check` | is everything in place |
| `localdub languages` | language codes; which can be dubbed into; dictionaries |
| `localdub run VIDEO --channel NAME [--from L --to L] [--speaker man\|woman] [--start T --duration T] [--name N] [--no-review] [--shorts N] [--shorts-only]` | start a run |
| `localdub run NAME [--redo STAGE]` | continue (or redo from a stage: prep, separate, transcribe, spelling, review1, translate, review2, voice, mix) |
| `localdub shorts NAME [--lang dub\|original] [--auto N] [--redo select\|vision]` | Shorts from a finished run (a Shorts-only run: original only) |
| `localdub voice NAME [--pick N\|current\|MM:SS-MM:SS\|file.wav]` | compare voice samples, pick one |
| `localdub line NAME ID [--text-stdin \| --text WORDS] [--new-take]` | one line of a finished dub: without options a `line` event (src, tgt, passed, heard, fit); with new words (empty: silent) or `--new-take` it is voiced again and the dub (and its dubbed Shorts) made again |
| `localdub channel NAME [--speaker man\|woman\|none]` | a channel's languages, speaker, voice |
| `localdub models [--set TASK=SERVER/MODEL] [--effort USE=LEVEL] [--test TASK]` | which model does each task (text, vision, voice, hearing, separation), on which server; try one |
| `localdub models --server NAME --url URL --kind llama-swap\|openai [--key-stdin] \| --remove` | add, change or remove a server |
| `localdub guide` | this guide |

Every command takes `--json`. Runs live in `~/dub/runs/NAME/` (the dubbed video `NAME.LANG.mp4`,
subtitles, `check.txt` = lines worth a listen), channels in `~/dub/channels/NAME/`.

## Timing

A 30-minute video takes about 30 minutes of machine time on two 16 GB GPUs; the translation is the
longest stage on talk-heavy videos (`localdub models --effort translate=low` makes it several times
faster). A model's first request can take a minute or two while it loads. Report progress from
`progress` events instead of polling files.
