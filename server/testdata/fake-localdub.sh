#!/bin/sh
# A stand-in for `localdub` in the server's tests: prints the --json events a real run would print,
# without doing any work. The run's name (2nd argument) picks the story. Like the real engine, it
# writes events to stdout and messages for people to stderr. Without --json it prints no events.
case " $* " in
  *" --json "*) ;;
  *) echo "fake-localdub: --json is missing" >&2; exit 2 ;;
esac
echo "fake-localdub: messages for people go to stderr" >&2

case "$2" in
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
  *)
    echo "fake-localdub: no story for run '$2'" >&2
    exit 9 ;;
esac
