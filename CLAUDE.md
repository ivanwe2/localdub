# localdub: notes for Claude Code

## Working with Ivan (read this first)

- Since Nov 19, 2026 Claude writes the code (Ivan: the learning pace was too slow; he wants the
  project finished so he can start a new one). Explain briefly what each part does and how Rust does
  it (Ivan knows .NET; label C# comparisons as C#), but don't leave gaps for him to fill. Until Nov 19
  he wrote the server's first steps himself (health, runs list, events, runner, job).
- Since Nov 19 Claude also commits, pushes, opens the PRs and merges them once CI is green, without
  waiting for Ivan's review (his call: "no need to wait for my reviews"). Still: never merge red CI,
  never force-push `main`, and tell him what was merged.
- When he has to run something, say exactly which command and where: laptop PowerShell, CT 103
  (`root@llama-cpp`), or the Proxmox host. One step at a time; wait for the output.
- Explain as the work goes, briefly: what each part does and how it fits. No big lectures up front.
- He values directness and precise, concise answers. Don't build things that aren't needed yet.

## What localdub is

Dubs a video into another language in the creator's own cloned voice, keeps the original music and
effects, and cuts Shorts in the dubbed or the original language. Everything runs on local GPUs.
See README.md for the user-facing description.

Pipeline (`src/localdub/run.py`, one resumable command, two review stops):
prep → separate (Demucs) → transcribe (Nemotron via nemo-speech) → spelling (Gemma, guarded by
hunspell) → review 1 → translate (Gemma, sentence budgets, channel glossary/memory) → review 2 →
voice (Qwen3-TTS takes, checked by Nemotron, compared by speaker similarity) → mix (placement,
levels, mux, subtitles). Shorts: `shorts.py` (motion, Gemma vision, ranked candidates, render).

## Layout

| Path | What |
|---|---|
| `src/localdub/run.py` | CLI, the stages, reviews and learning, `check`, `shorts`, `channel`, `languages` commands |
| `src/localdub/languages.py` | the language table (name, ISO 639-2, hunspell dict + apt package, num2words code, separators, abbreviations, calibration sentences, spelling hints); what can be dubbed into is the setting `tts_languages` |
| `src/localdub/llm.py` | llama-swap client: chat (JSON repair, retries), TTS, transcription, separation, unload |
| `src/localdub/text.py` | sentences, spelling guard, translation, shortening |
| `src/localdub/segments.py` | a segment's text keys are `src`/`tgt`/`model_tgt`; `upgrade()` renames the pre-0.9.0 `bg`/`en`/`model_en` when old runs are read. Review files mark lines with the channel's codes (`review.label`: `BG:`, `EN:`, `DE:`…) |
| `src/localdub/voice.py` | voice-sample candidates, takes, the take check (`judge`), fitting |
| `src/localdub/speaker.py` | speaker fingerprints (WavLM x-vectors), voiceprint, similarity |
| `src/localdub/mix.py` | placement (late starts), mixing, muxing, subtitles |
| `src/localdub/shorts.py` | motion, vision, candidates, captions, rendering |
| `src/localdub/review.py`, `media.py`, `asr.py`, `settings.py` | review files, ffmpeg, Nemotron (http or local backend), defaults + channel profiles |
| `tests/` | unit tests; `test_e2e.py` runs whole videos against `tests/fakes/` (fake llama-swap + the real Demucs wrapper; one run http, one local) |
| `deploy/install.sh` | install/update on CT 103 from git into the Demucs venv, plus the channels' hunspell dictionaries (`LANGS="de fr"` for new ones) |
| `deploy/gpu/` | the GPU box: `llama-swap.yaml` (models + groups), `demucs_server.py` (Demucs over HTTP), `llama-swap.service` |
| `server/src/` | Rust server: `lib.rs` (routes: `/api/…`, `/files/…`, the page), `runs.rs` (runs on disk), `events.rs` (engine `--json` lines), `runner.rs` (runs the engine), `job.rs` (the current run), `start.rs` (POST /api/runs: validation, engine args), `channels.rs` (channels on disk), `voice.rs` (a run's voice samples, picks), `videos.rs` (the videos folder, safe names), `download.rs` (yt-dlp downloads from links: args, its printed lines, finding the file; own slot beside the job), `main.rs` (env settings); `testdata/fake-localdub.sh`, `testdata/fake-yt-dlp.sh` |
| `deploy/server/` | the web app on a Linux box: `build.sh` (dev container or CI → `dist/localdub-app.tar.gz`: static musl server + page + `BUILD`), `install.sh` (→ `/opt/localdub`, systemd `localdub-server`, 127.0.0.1:8080; refuses while a run or download is going unless `--force`; laptop uses `ssh -N -L 8080:127.0.0.1:8080`), `yt-dlp.sh` (yt-dlp + Deno into /usr/local/bin, checksums checked; `yt-dlp -U` once installed) |
| `deploy/update.sh` | on CT 103: engine (git pull + venv install), yt-dlp (install/update) and the latest web app from the GitHub release `app` (CI `release.yml` rebuilds it on every merge to main touching server/web/deploy-server) |
| `web/` | React + TypeScript page (Vite): `src/api.ts` (API types/client), `src/App.tsx` (job panel, new-run form with upload and download from a link, runs list, reviews), `src/VoicePicker.tsx` (listen, compare, pick), `src/format.ts` (+ tests) |

## Conventions

- Python ≥ 3.10; the core needs only numpy and num2words (+ ffmpeg, hunspell). Optional extra `voice` = transformers.
- CLI exit codes: 0 done, 3 waiting for a human, 1 error. Runs live in `~/dub/runs/NAME/`
  (`state.json` = stages done), channels in `~/dub/channels/NAME/`.
- Tests: `python -m pytest -q -m "not slow"` (seconds); `python -m pytest -q` also runs two
  end-to-end videos against the fakes (~2 min). Keep them green; add a test with each fix.
  CI (GitHub Actions) runs everything on push.
- Public repo: never commit media, transcripts or voice samples of real people. Tests use the
  generated fixture in `tests/fixture.py` with made-up sentences.
- Settings: defaults in `settings.py`; `~/dub/settings.json` overrides only what differs.
- Language-agnostic: nothing may assume Bulgarian → English or the speaker's gender ("the speaker").
  Languages come from `languages.py` and the channel profile (set on the channel's first run with
  `--from/--to`, optional `--speaker man|woman` used only in the translation prompt); profiles from
  before 0.9.0 without `source_lang` are read as bg → en.

## Workflow

- Develop inside the dev container (`.devcontainer/`, VS Code "Reopen in Container"); keep the laptop
  clean. From outside VS Code: `docker exec -u dev -w /workspaces/localdub localdub-dev <command>`.
  Always `-u dev`: without it the command runs as root and leaves root-owned files in the cargo
  build volume, which then breaks `cargo` for Ivan (happened Nov 16; fix: `chown -R dev:dev
  /home/dev/.cargo-target`).
- One branch per meaningful change (`feat/…`, `fix/…`, `chore/…`, `docs/…`), with a commit per
  piece → pull request when the change is useful on its own (e.g. "start a run over HTTP"), not after
  every piece (Ivan, Nov 18: fewer CI waits) → CI green → merge. `main` only takes changes through
  PRs. Conventional commit messages (`feat:`, `fix:`, `ci:`…).
- Rust toolchain pinned in `rust-toolchain.toml`; Rust CI runs `cargo fmt --check`, `cargo clippy`
  (warnings are errors) and `cargo test`.

## Architecture (decided Nov 11–12, 2026)

- **Deployment target**: one CPU-only localdub container (server + UI + engine + ffmpeg + dictionaries,
  speaker model on CPU) configured with one URL and key: **llama-swap** on the GPU box (decided Nov 12,
  replaces the llama.cpp router). llama-swap starts model servers on demand and its `routing` groups
  decide what runs together: `big` (31B models, Demucs: one at a time) and `voicing` (Qwen3-TTS on
  GPU 0 + Nemotron on GPU 1, side by side). Routes: chat `/v1/chat/completions`; TTS
  `/upstream/qwen3-tts/tts`; transcription `/v1/audio/transcriptions` (nemo-speech serve,
  `useModelName: salm`, verbose_json with word times); separation `/upstream/demucs/separate`
  (`deploy/gpu/demucs_server.py`, returns a ZIP of vocals.wav + no_vocals.wav). Status `/running`,
  free GPUs `POST /api/models/unload`. Verified on CT 103 Nov 12 (PoC: upload routing, passthrough,
  both voicing models loaded: 4.4 GB GPU 0, 14.5 GB GPU 1). Model servers inherit llama-swap's
  environment (incl. LLAMA_API_KEY: llama-server then checks the key too). The engine keeps a
  "local" backend (run the programs directly) for single-machine setups.

- **engine**: this Python package. Python only where the AI libraries need it.
- **server**: Rust, axum + tokio, in `server/`. REST API over the runs folder, one GPU job at a time
  (runs `localdub ... --json` with `tokio::process`), live progress to the browser, serves the UI
  and video files. Listens on 127.0.0.1; reached through an SSH tunnel (token auth only if LAN use
  is wanted).
- **web**: React + TypeScript (Vite) in `web/`. Runs list, new run, live progress, both reviews in the
  browser, voice-sample picker (listen and compare), Shorts timeline (AI candidates as markers,
  start/end handles snapped to sentence edges).
- The CLI stays first-class so AI agents can drive localdub too.

## Next steps

1. Engine `--json` (0.6.0): JSON Lines on stdout (`start`, `stage`, `check`, final `result`), human
   messages on stderr; contract in `src/localdub/events.py` and README. Possible later: per-stage
   progress events (lines voiced, images seen) for progress bars; until then the server can show stderr.
2. GPU box (`feat/gpu-box`): `deploy/gpu/` config, Demucs wrapper, service. Ivan's full CT 103 config
   (+ Qwen3.8 presets for Hermes/OpenCode) is `notes/ct103-llama-swap.yaml`. Not deployed until 3.
3. Engine 0.7.0: talks to llama-swap only (router support dropped); `nemo.backend` /
   `demucs.backend` = `http` (default) | `local`. CT 103 switched to llama-swap Nov 12; first full
   run with Shorts worked (8.3 min, Ivan: Shorts "perfect").
4. Server + page (Nov 19–20): runs list (files, what each waits for, worked out from disk), start
   (validated, videos inside the home folder only), uploads (streamed, `<home>/videos`), reviews
   (GET/PUT the waited-for file; *Approve and continue* sets `status: approved`), continue (after
   a review/error, or `shorts --lang` for picked Shorts), make Shorts, job + SSE, /files, the page,
   host + origin guard. Deploy package in `deploy/server/`. Tested for real on CT 103 Nov 20
   (whole run with reviews in the page: Ivan "looking great"). Since then: release builds +
   `deploy/update.sh`; AI panel (engine 0.8.0 `call` events → job.calls; engine stderr → job.log;
   `/api/status` from llama-swap `/running` + `/metrics`, live every 2 s while watched).
   Language-agnostic (Nov 20, engine 0.9.0, plan part A, PRs #18–#22): language table, channels
   choose `--from/--to` (+ speaker), neutral keys and review labels, take check/prompts/voice
   scoring per language, dictionaries per channel, channels + languages in the server and page.
   Voice picker (Nov 20, engine 0.10.0, part B): `localdub voice <run> [--pick]`, voice-candidates.json
   v2 (stable `n`, listed test clones, the current voice too, `picked`), routes …/voice, …/voice/pick,
   …/revoice, `VoicePicker.tsx` (also inside review 1, writing its `voice:` line).
   Downloads (Nov 20, part C): POST /api/downloads runs yt-dlp into `<home>/videos` beside a dub
   (`-f bv+ba/b -S res:1080,vcodec:h264,acodec:aac`: YouTube 403s its single-file format 18;
   title made safe with `--replace-in-metadata`; `--print` lines `[info]`/`[file]` +
   `--progress-template`; checked against real yt-dlp 2026.10.26 + Deno 2.9 in the dev container).
   The CT 103 checkpoint after A+B was postponed by Ivan (Nov 20: "lets not test yet").
   **Next, in this order**: CT 103 checkpoint for A, B and C together (ask first), UI overhaul
   (mock-up first: dark studio look, animated sound-wave line background that "speaks" while a run
   works, runs as editing-timeline tracks, timecode numbers, VU-meter tok/s, warm "on air" accent;
   Ivan liked this concept). Token auth only if LAN use is wanted (until then: SSH tunnel). CT 103
   is sometimes used for gaming: ask before running things there.
5. App image (multi-stage) + docker compose; GPU-side services on CT 103.

## Backlog

- Voice: dubbed voice can sound accented ("a bit Indian") in English. Ideas: the speaker-similarity
  choice (built, verify on CT 103), a second TTS engine compared automatically (Qwen3-TTS through
  its own package with the sample's transcript; llama.cpp `/tts` takes audio only), other models.
- Dub captions in Shorts are timed by character share; use the word times Nemotron already returns
  for each take.
- Distant/quiet speech is missed: try transcribing the levelled vocals stem; flag "voice but no words".
- Review 1 lists too much on full videos (~12% unknown words); fix one line without approving all
  of review 2; `--fast` mode (translation thinking off).
- Clipped endings inside a word ("vide") still pass the take check.
- Voice candidates: the test clones aren't checked like real takes (TTS sometimes fluffs words); score
  differences ≲ 0.01 are noise with 4 sentences × 1 seed (Nov 12: candidates 0.966 / 0.957 / 0.930,
  Ivan's ref.wav 0.956; ranking matched his ear). The score shortlists, the ear decides close calls.
- Log noise from the speaker model (torch `key_padding_mask` warning, HF Hub token notice).
- Separation uploads `audio44.wav` as WAV (~300 MB for 30 min) and gets two WAVs back; FLAC would
  halve that once localdub and the GPU box are on different machines.
- Shorts selection takes ~2 min per language even on a 3-minute excerpt (~10K tokens at effort "low").
- llama-servers behind llama-swap inherit `LLAMA_API_KEY` and check it again; llama-swap's own
  unauthenticated call after a health check logs `unauthorized: Invalid API Key` (harmless).
- The voice stage didn't get faster with Nemotron kept loaded (108 s → 105 s): the per-round reload
  wasn't the bottleneck; TTS (~30 s per round) and the checks are.
- Server security (Nov 19): POST /runs validates name/channel (safe folder names, no leading `-`)
  and requires `video` to be an existing file; the server listens on 127.0.0.1 unless
  LOCALDUB_LISTEN says otherwise, answers only to its own Host names (DNS-rebinding guard; more via
  LOCALDUB_ALLOWED_HOSTS) and refuses state changes whose Origin is another site or whose
  Sec-Fetch-Site is cross-site (CSRF; the Vite dev proxy rewrites Host and Origin). Videos must be
  inside the home folder. Download links must be one http(s) piece and go after `--`; yt-dlp can
  still fetch any address the box reaches (LAN included), fine while only the tunnel reaches the
  page. Before opening it to the LAN: token auth.
