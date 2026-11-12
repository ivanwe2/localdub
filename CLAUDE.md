# localdub: notes for Claude Code

## Working with Ivan (read this first)

- Ivan wants to take part in the development, not be handed finished work. Work in small steps:
  propose the next step, explain what it does and why, and let him write the code where he wants to
  learn (the Rust server above all, also the React UI). Review his code; write code yourself only
  when he asks, and keep it to the step at hand.
- Ivan makes the commits. Don't commit or push unless he asks.
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
| `src/localdub/run.py` | CLI, the stages, reviews and learning, `check`, `shorts` command |
| `src/localdub/llm.py` | llama.cpp router client: chat (JSON repair, retries), `/tts`, unload |
| `src/localdub/text.py` | sentences, spelling guard, translation, shortening |
| `src/localdub/voice.py` | voice-sample candidates, takes, the take check (`judge`), fitting |
| `src/localdub/speaker.py` | speaker fingerprints (WavLM x-vectors), voiceprint, similarity |
| `src/localdub/mix.py` | placement (late starts), mixing, muxing, subtitles |
| `src/localdub/shorts.py` | motion, vision, candidates, captions, rendering |
| `src/localdub/review.py`, `media.py`, `asr.py`, `settings.py` | review files, ffmpeg, nemo-speech, defaults + channel profiles |
| `tests/` | unit tests; `test_e2e.py` runs whole videos against `tests/fakes/` |
| `deploy/install.sh` | install/update on CT 103 from git into the Demucs venv |
| `deploy/gpu/` | the GPU box: `llama-swap.yaml` (models + groups), `demucs_server.py` (Demucs over HTTP), `llama-swap.service` |

## Conventions

- Python ≥ 3.10; the core needs only numpy (+ ffmpeg, hunspell). Optional extra `voice` = transformers.
- CLI exit codes: 0 done, 3 waiting for a human, 1 error. Runs live in `~/dub/runs/NAME/`
  (`state.json` = stages done), channels in `~/dub/channels/NAME/`.
- Tests: `python -m pytest -q -m "not slow"` (seconds); `python -m pytest -q` also runs two
  end-to-end videos against the fakes (~2 min). Keep them green; add a test with each fix.
  CI (GitHub Actions) runs everything on push.
- Public repo: never commit media, transcripts or voice samples of real people. Tests use the
  generated fixture in `tests/fixture.py` with made-up sentences.
- Settings: defaults in `settings.py`; `~/dub/settings.json` overrides only what differs.
- Language-agnostic: nothing may assume Bulgarian → English. Languages, dictionaries and prompts come
  from the channel profile and settings.

## Workflow

- Develop inside the dev container (`.devcontainer/`, VS Code "Reopen in Container"); keep the laptop
  clean. From outside VS Code: `docker exec -w /workspaces/localdub localdub-dev <command>`.
- One branch per step (`feat/…`, `fix/…`, `chore/…`, `docs/…`) → pull request → CI green → merge.
  `main` only takes changes through PRs. Conventional commit messages (`feat:`, `fix:`, `ci:`…).
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
  (runs `localdub ... --json` with `tokio::process`), live progress to the browser, single user
  with an access token on LAN/Tailscale, serves the UI and video files. **Ivan writes it**; guide
  and review.
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
3. Engine talks to llama-swap (drop router-only calls: `GET /models`, `/models/unload`, `/tts` by
   body model): on-demand loading, TTS via `/upstream`, transcription and separation over HTTP; then
   switch CT 103 to llama-swap (disable `llama.service`) and do a real excerpt run.
4. Server, one PR at a time: `/health` → list runs (serde) → job queue running the engine →
   progress streaming → reviews and Shorts endpoints → router model management → token auth → serve the UI.
5. Web UI on top of the server.
6. App image (multi-stage) + docker compose; GPU-side services on CT 103.

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
- Language assumptions left in the engine: the spelling prompt's examples are Bulgarian; the take
  check spells out numbers in English only; voice test sentences (`speaker.CALIBRATION`) are English only.
- Voice candidates: the test clones aren't checked like real takes (TTS sometimes fluffs words); score
  differences ≲ 0.01 are noise with 4 sentences × 1 seed (Nov 12: candidates 0.966 / 0.957 / 0.930,
  Ivan's ref.wav 0.956; ranking matched his ear). The score shortlists, the ear decides close calls.
- Log noise from the speaker model (torch `key_padding_mask` warning, HF Hub token notice).
