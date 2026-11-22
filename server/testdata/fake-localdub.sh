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

# `models ...`: the Models screen's command. A key must come on stdin ("sk-test"), never in the
# arguments; the server `main` can't be removed.
if [ "$1" = "models" ]; then
  done='{"event": "result", "status": "done", "exit_code": 0}'
  case " $* " in
    *" --test "*)
      echo "{\"event\": \"models_test\", \"task\": \"$3\", \"server\": \"main\", \"model\": \"m\", \"ok\": true, \"seconds\": 0.1, \"detail\": \"fine\", \"tok_per_s\": null, \"file\": \"/elsewhere/.models-test/voice.wav\"}"
      echo "$done"; exit 0 ;;
    *" --remove "*)
      if [ "$3" = "main" ]; then
        echo '{"event": "result", "status": "error", "exit_code": 1, "error": "the server main stays"}'; exit 1
      fi
      echo "$done"; exit 0 ;;
    *" --key-stdin "*)
      case " $* " in *sk-*) echo '{"event": "result", "status": "error", "exit_code": 1, "error": "a key in the arguments"}'; exit 1 ;; esac
      read -r key
      if [ "$key" != "sk-test" ]; then
        echo '{"event": "result", "status": "error", "exit_code": 1, "error": "no key on stdin"}'; exit 1
      fi
      echo "$done"; exit 0 ;;
    *" --set "*|*" --effort "*|*" --server "*|*" --preset "*)
      echo "$done"; exit 0 ;;
  esac
  echo '{"event": "models", "servers": [{"name": "main", "url": "http://127.0.0.1:9876", "kind": "llama-swap", "key_set": true, "remote": false, "reachable": true, "models": [{"id": "gemma4-31b"}]}], "tasks": [{"task": "text", "server": "main", "model": "gemma4-31b"}], "efforts": {"translate": "medium"}}'
  echo "$done"; exit 0
fi

# `voice <run>`: voice samples to compare, then it waits for a pick; `voice <run> --pick N`: picked
if [ "$1" = "voice" ]; then
  echo "{\"event\": \"start\", \"command\": \"voice\", \"run\": \"$2\", \"channel\": \"c\", \"dir\": \"/tmp/runs/$2\"}"
  if [ "$3" = "--pick" ]; then
    echo "{\"event\": \"result\", \"status\": \"done\", \"exit_code\": 0, \"run\": \"$2\", \"voice\": \"$2, candidate $4\"}"
    exit 0
  fi
  echo "{\"event\": \"result\", \"status\": \"waiting\", \"exit_code\": 3, \"run\": \"$2\", \"waiting_for\": {\"kind\": \"voice\", \"file\": \"/tmp/runs/$2/voice-candidates.json\"}}"
  exit 3
fi

# `line <run> <id> [--text-stdin] [--new-take]`: a line fixed after the dub. The words must come on
# stdin ("Hello there."), never in the arguments.
if [ "$1" = "line" ]; then
  echo "{\"event\": \"start\", \"command\": \"line\", \"run\": \"$2\", \"channel\": \"c\", \"dir\": \"/tmp/runs/$2\", \"stages\": [\"voice\", \"mix\"], \"done\": []}"
  case " $* " in *Hello*) echo '{"event": "result", "status": "error", "exit_code": 1, "error": "words in the arguments"}'; exit 1 ;; esac
  case " $* " in
    *" --text-stdin "*)
      read -r words
      if [ "$words" != "Hello there." ]; then
        echo '{"event": "result", "status": "error", "exit_code": 1, "error": "no words on stdin"}'; exit 1
      fi ;;
  esac
  echo '{"event": "stage", "stage": "voice", "status": "started"}'
  echo '{"event": "stage", "stage": "voice", "status": "done", "seconds": 3.0}'
  echo '{"event": "stage", "stage": "mix", "status": "started"}'
  echo '{"event": "stage", "stage": "mix", "status": "done", "seconds": 9.0}'
  echo "{\"event\": \"result\", \"status\": \"done\", \"exit_code\": 0, \"run\": \"$2\"}"
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
