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

# spelling dictionaries: every language localdub knows (a few MB each), so any new channel works at
# once; LANGS="de fr" bash install.sh installs only those (plus every channel's video language).
# localdub knows which Debian package has which dictionary. One package at a time: one that this
# system doesn't have can't stop the others.
packages=$(localdub languages --apt ${LANGS:-all})
dicts=()
for pkg in $packages; do dpkg -s "$pkg" >/dev/null 2>&1 || dicts+=("$pkg"); done
missing=()
if [ ${#dicts[@]} -gt 0 ]; then
  apt-get update -q
  for pkg in "${dicts[@]}"; do apt-get install -y -q "$pkg" || missing+=("$pkg"); done
fi
echo "Installed: $(localdub --version)"
packages=$(echo $packages)  # one line
echo "Spelling dictionaries: ${packages:-none}"
if [ ${#missing[@]} -gt 0 ]; then
  echo "note: couldn't install ${missing[*]}; those languages' runs stop until it is (apt-get install ...)" >&2
fi
echo "Next: localdub check"
