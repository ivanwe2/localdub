#!/bin/sh
# A stand-in engine for the screenshots: `languages` answers at once; anything else voices
# market-day-ep12's 16 made-up lines, one every 0.7 s, with the model calls and progress events the
# real engine sends, then waits (the screenshot script stops it).
if [ "$1" = "languages" ]; then
  echo '{"event": "languages", "languages": [{"code": "bg", "name": "Bulgarian", "dub": false, "dictionary": "bg_BG", "dictionary_installed": true, "apt": "hunspell-bg"}, {"code": "en", "name": "English", "dub": true, "dictionary": "en_US", "dictionary_installed": true, "apt": "hunspell-en-us"}, {"code": "de", "name": "German", "dub": true, "dictionary": "de_DE", "dictionary_installed": true, "apt": "hunspell-de-de"}, {"code": "es", "name": "Spanish", "dub": true, "dictionary": "es_ES", "dictionary_installed": true, "apt": "hunspell-es"}]}'
  echo '{"event": "result", "status": "done", "exit_code": 0}'
  exit 0
fi
echo '{"event": "start", "command": "run", "run": "market-day-ep12", "channel": "market-tales", "dir": "/tmp", "stages": ["prep", "separate", "transcribe", "spelling", "review1", "translate", "review2", "voice", "mix"], "done": ["prep", "separate", "transcribe", "spelling", "review1", "translate", "review2"]}'
echo '{"event": "stage", "stage": "voice", "status": "started"}'
echo '{"event": "call", "kind": "chat", "model": "gemma4-31b", "label": "translating 1-16", "seconds": 21.4, "tokens": 2163, "tok_per_s": 101.2}'
echo '{"event": "call", "kind": "chat", "model": "gemma4-31b", "label": "shortening line 6 to fit 4.2 s", "seconds": 2.9, "tokens": 302, "tok_per_s": 104.1}'
echo "   take 1 (seed 42): 16 lines" >&2
n=0
for at in 2 10 18 28 36 46 57 64 74 85 95 106 120 133 144 157; do
  n=$((n + 1))
  echo "{\"event\": \"call\", \"kind\": \"tts\", \"model\": \"qwen3-tts\", \"label\": \"line $n, take 1\", \"seconds\": 1.4, \"chars\": 40, \"audio_seconds\": 4.2}"
  echo "{\"event\": \"progress\", \"stage\": \"voice\", \"label\": \"voicing, take 1\", \"done\": $((n - 1)), \"total\": 16, \"line\": $n, \"at\": $at}"
  if [ $((n % 4)) -eq 0 ]; then
    echo "{\"event\": \"call\", \"kind\": \"asr\", \"model\": \"nemotron\", \"label\": \"transcribe check-$n\", \"seconds\": 0.6, \"audio_seconds\": 24.6}"
    echo "   line $n take 1: heard it as written, fits its slot: passed, sounds 0.97 like the speaker" >&2
  fi
  sleep 0.7
done
sleep 600
