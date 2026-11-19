#!/usr/bin/env bash
# Updates everything localdub on this machine to the latest on GitHub, as root:
#   bash ~/localdub/deploy/update.sh            (add --force to update even while a run is going)
# 1. the engine: git pull of ~/localdub and reinstall into its venv (deploy/install.sh)
# 2. the web app: the latest build from the GitHub release "app" (made by CI on every merge to main),
#    installed with its install.sh, unless that build is installed already
#
# Everything is inside main(), so bash has read the whole script before step 1's git pull can
# change this file.
set -euo pipefail

main() {
  local here repo url tmp new old
  here="$(cd "$(dirname "$0")" && pwd)"
  repo=${REPO:-https://github.com/ivanwe2/localdub}
  url=${LOCALDUB_APP_URL:-$repo/releases/download/app/localdub-app.tar.gz}

  echo "== the engine"
  bash "$here/install.sh"

  echo "== the web app"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$url" -o "$tmp/app.tar.gz"
  tar -xzf "$tmp/app.tar.gz" -C "$tmp"
  new=$(cat "$tmp/localdub-app/BUILD" 2>/dev/null || echo unknown)
  old=$(cat /opt/localdub/BUILD 2>/dev/null || echo "not installed")
  if [ "$new" = "$old" ] && [ "${1:-}" != "--force" ]; then
    echo "the web app is up to date ($old)"
    return
  fi
  bash "$tmp/localdub-app/install.sh" "$@"
  echo "the web app: $old -> $new"
}

main "$@"
exit
