#!/usr/bin/env bash
# Install or update localdub from git into the Python venv that has CUDA torch and Demucs.
# Safe to re-run: it updates the code; channels and runs in ~/dub are never touched.
#   REPO, DIR and VENV can be overridden from the environment.
set -euo pipefail
REPO=${REPO:-https://github.com/ivanwe2/localdub}
DIR=${DIR:-$HOME/localdub}
VENV=${VENV:-$HOME/venvs/demucs-gpu}

need=()
command -v hunspell >/dev/null && [ -f /usr/share/hunspell/bg_BG.dic ] || need+=(hunspell hunspell-bg)
command -v fc-list >/dev/null && fc-list | grep -qi "DejaVu Sans" || need+=(fontconfig fonts-dejavu-core)
command -v git >/dev/null || need+=(git)
if [ ${#need[@]} -gt 0 ]; then apt-get update -q && apt-get install -y -q "${need[@]}"; fi

if [ -d "$DIR" ] && [ ! -d "$DIR/.git" ]; then
  mv "$DIR" "$DIR.old-$(date +%Y%m%d%H%M%S)"   # the copied 0.x install, before the repo existed
fi
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone "$REPO" "$DIR"; fi

"$VENV/bin/pip" install -q -e "$DIR[voice]"
ln -sf "$VENV/bin/localdub" /usr/local/bin/localdub
mkdir -p ~/dub
echo "Installed: $(localdub --version)"
echo "Next: localdub check"
