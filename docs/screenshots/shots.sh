#!/bin/sh
# The README's screenshots (docs/images/), from a made-up demo: the real server and page, a stand-in
# engine (demo_engine.sh) and llama-swap (fake_llama_swap.py), a generated home (demo_home.py), in
# headless Chromium. No real media, speech or voices. In the dev container, from the repo:
#   sh docs/screenshots/shots.sh
# (the first time also: cd docs/screenshots && npm ci && npx playwright install --with-deps chromium)
set -eu
repo=$(cd "$(dirname "$0")/../.." && pwd)
here=$repo/docs/screenshots
work=$(mktemp -d)
server='' swap=''
trap 'kill $server $swap 2>/dev/null; pkill -f demo_engine.sh 2>/dev/null; rm -rf "$work"' EXIT

python3 "$here/demo_home.py" "$work/home"
(cd "$repo/web" && npm run -s build >/dev/null)
(cd "$repo/server" && cargo build -q)
python3 "$here/fake_llama_swap.py" 8093 &
swap=$!
LOCALDUB_HOME=$work/home LOCALDUB_LISTEN=127.0.0.1:8092 LOCALDUB_MODELS_URL=http://127.0.0.1:8093 \
  LOCALDUB_YTDLP=none LOCALDUB_ENGINE="sh $here/demo_engine.sh" LOCALDUB_WEB=$repo/web/dist \
  "${CARGO_TARGET_DIR:-$repo/server/target}/debug/localdub-server" 2>"$work/server.log" &
server=$!
sleep 1
out=${OUT:-$repo/docs/images}  # OUT=/tmp/x: somewhere else, e.g. to look before replacing them
mkdir -p "$out"
(cd "$here" && node shots.mjs http://127.0.0.1:8092 "$out" "$work")
ls -la "$out"
