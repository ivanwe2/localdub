#!/bin/sh
# A stand-in for `localdub` in the server's tests: prints the --json events a real run would print,
# without doing any work. The run's name (2nd argument) picks the story. Like the real engine, it
# writes events to stdout and messages for people to stderr. Without --json it prints no events.
# Through the server (POST /runs) the 2nd argument is the request's "video", so the tests make a
# video file named after the story; its file name picks the story.
case " $* " in
  *" --json "*) ;;
  *) echo "fake-localdub: --json is missing" >&2; exit 2 ;;
esac
echo "fake-localdub: messages for people go to stderr" >&2

# `languages`: the engine's language table (two of them here)
if [ "$1" = "languages" ]; then
  echo '{"event": "languages", "languages": [{"code": "en", "name": "English", "dub": true, "dictionary": "en_US", "dictionary_installed": true, "apt": "hunspell-en-us"}, {"code": "bg", "name": "Bulgarian", "dub": false, "dictionary": "bg_BG", "dictionary_installed": true, "apt": "hunspell-bg"}]}'
  echo '{"event": "result", "status": "done", "exit_code": 0}'
  exit 0
fi

# `shorts <run> --lang <lang>`: proposes candidates and stops for you to pick them
if [ "$1" = "shorts" ]; then
  echo "{\"event\": \"start\", \"command\": \"shorts\", \"run\": \"$2\", \"channel\": \"c\", \"dir\": \"/tmp/runs/$2\", \"lang\": \"$4\"}"
  echo "{\"event\": \"result\", \"status\": \"waiting\", \"exit_code\": 3, \"run\": \"$2\", \"waiting_for\": {\"kind\": \"shorts\", \"lang\": \"$4\", \"file\": \"/tmp/runs/$2/shorts/$4/candidates.txt\"}}"
  exit 3
fi

case "$(basename "$2")" in
  done-run)
    echo '{"event": "start", "command": "run", "run": "done-run", "channel": "c", "dir": "/tmp/runs/done-run", "stages": ["prep", "mix"], "done": []}'
    echo '{"event": "stage", "stage": "prep", "status": "started"}'
    echo 'not an event: the server must skip a line like this'
    echo '{"event": "stage", "stage": "prep", "status": "done", "seconds": 0.5}'
    echo '{"event": "stage", "stage": "mix", "status": "started"}'
    echo '{"event": "stage", "stage": "mix", "status": "done", "seconds": 4.0}'
    echo '{"event": "result", "status": "done", "exit_code": 0, "run": "done-run", "outputs": {"video": "/tmp/runs/done-run/done-run.en.mp4"}}'
    exit 0 ;;
  review-run)
    echo '{"event": "start", "command": "run", "run": "review-run", "channel": "c", "dir": "/tmp/runs/review-run", "stages": ["prep", "review1", "mix"], "done": []}'
    echo '{"event": "stage", "stage": "prep", "status": "started"}'
    echo '{"event": "stage", "stage": "prep", "status": "done", "seconds": 0.5}'
    echo '{"event": "stage", "stage": "review1", "status": "started"}'
    echo '{"event": "stage", "stage": "review1", "status": "waiting"}'
    echo '{"event": "result", "status": "waiting", "exit_code": 3, "run": "review-run", "waiting_for": {"kind": "review1", "file": "/tmp/runs/review-run/review-1-transcript.txt"}}'
    exit 3 ;;
  failing-run)
    echo '{"event": "start", "command": "run", "run": "failing-run", "channel": "c", "dir": "/tmp/runs/failing-run", "stages": ["prep", "mix"], "done": []}'
    echo '{"event": "stage", "stage": "prep", "status": "started"}'
    echo '{"event": "result", "status": "error", "exit_code": 1, "run": "failing-run", "error": "in stage '"'"'prep'"'"': the video has no audio track", "stage": "prep"}'
    exit 1 ;;
  slow-run)
    echo '{"event": "start", "command": "run", "run": "slow-run", "channel": "c", "dir": "/tmp/runs/slow-run", "stages": ["prep"], "done": []}'
    echo '{"event": "stage", "stage": "prep", "status": "started"}'
    sleep 1
    echo '{"event": "stage", "stage": "prep", "status": "done", "seconds": 1.0}'
    echo '{"event": "result", "status": "done", "exit_code": 0, "run": "slow-run"}'
    exit 0 ;;
  *)
    echo "fake-localdub: no story for run '$2'" >&2
    exit 9 ;;
esac
