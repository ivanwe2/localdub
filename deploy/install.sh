#!/usr/bin/env bash
# Install or update localdub from git into the Python venv that has CUDA torch and Demucs.
# Safe to re-run: it updates the code; channels and runs in ~/dub are never touched.
#   REPO, DIR and VENV can be overridden from the environment.
set -euo pipefail
REPO=${REPO:-https://github.com/ivanwe2/localdub}
DIR=${DIR:-$HOME/localdub}
VENV=${VENV:-$HOME/venvs/demucs-gpu}

need=()
command -v hunspell >/dev/null || need+=(hunspell)
# (grep reads all of fc-list's output: with -q it would stop at the first match, fc-list would get
# a broken pipe, and pipefail would count that as "not installed")
command -v fc-list >/dev/null && fc-list | grep -i "DejaVu Sans" >/dev/null || need+=(fontconfig fonts-dejavu-core)
command -v git >/dev/null || need+=(git)
if [ ${#need[@]} -gt 0 ]; then apt-get update -q && apt-get install -y -q "${need[@]}"; fi

if [ -d "$DIR" ] && [ ! -d "$DIR/.git" ]; then
  mv "$DIR" "$DIR.old-$(date +%Y%m%d%H%M%S)"   # the copied 0.x install, before the repo existed
fi
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone "$REPO" "$DIR"; fi

"$VENV/bin/pip" install -q -e "$DIR[voice]"
ln -sf "$VENV/bin/localdub" /usr/local/bin/localdub
mkdir -p ~/dub

# spelling dictionaries: the source language of every channel here, plus LANGS for channels to come
# (e.g. LANGS="de fr" bash install.sh); localdub knows which Debian package has which dictionary
packages=$(localdub languages --apt ${LANGS:-})
dicts=()
for pkg in $packages; do dpkg -s "$pkg" >/dev/null 2>&1 || dicts+=("$pkg"); done
if [ ${#dicts[@]} -gt 0 ]; then apt-get update -q && apt-get install -y -q "${dicts[@]}"; fi
echo "Installed: $(localdub --version)"
packages=$(echo $packages)  # one line
echo "Spelling dictionaries: ${packages:-none yet (no channels)}"
echo "Next: localdub check"
