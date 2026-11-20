#!/usr/bin/env bash
# Installs or updates the localdub web app on this machine. Run it as root from the unpacked
# localdub-app folder (made by deploy/server/build.sh):
#   tar xzf localdub-app.tar.gz && bash localdub-app/install.sh
# The server and page go to /opt/localdub, the service listens on 127.0.0.1:8080.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

if ! command -v localdub >/dev/null; then
  echo "note: the engine (localdub) isn't installed here yet: see deploy/install.sh" >&2
fi
# restarting the service stops a run or a download that is going; don't do that unless asked to
if [ "${1:-}" != "--force" ]; then
  job=$(curl -sf http://127.0.0.1:8080/api/job 2>/dev/null || true)
  download=$(curl -sf http://127.0.0.1:8080/api/download 2>/dev/null || true)
  case "$job$download" in
    *'"state":"running"'* | *'"state":"downloading"'*)
      echo "a run or a download is going in the web app; try again when it's done (or add --force to stop it)" >&2
      exit 1 ;;
  esac
fi

install -d /opt/localdub
install -m 755 "$here/localdub-server" /opt/localdub/localdub-server.new
mv /opt/localdub/localdub-server.new /opt/localdub/localdub-server   # replace it in one step
rm -rf /opt/localdub/web.new && cp -r "$here/web" /opt/localdub/web.new
rm -rf /opt/localdub/web && mv /opt/localdub/web.new /opt/localdub/web
install -m 644 "$here/localdub-server.service" /etc/systemd/system/localdub-server.service
[ -f "$here/BUILD" ] && install -m 644 "$here/BUILD" /opt/localdub/BUILD

# yt-dlp and Deno, for downloads from links, if they aren't here yet (deploy/update.sh keeps yt-dlp
# up to date). Without them the app still works, only downloads fail.
if [ ! -x /usr/local/bin/yt-dlp ] || ! command -v deno >/dev/null; then
  bash "$here/yt-dlp.sh" || echo "note: downloads from links won't work until this works: bash $here/yt-dlp.sh" >&2
fi

systemctl daemon-reload
systemctl enable --quiet localdub-server
systemctl restart localdub-server

for _ in $(seq 20); do
  curl -sf http://127.0.0.1:8080/api/health >/dev/null && break
  sleep 0.5
done
if curl -sf http://127.0.0.1:8080/api/health; then
  echo
  ip=$(hostname -I 2>/dev/null | cut -d' ' -f1)
  echo "localdub web app $(cat /opt/localdub/BUILD 2>/dev/null) is running. On your laptop:"
  echo "  ssh -N -L 8080:127.0.0.1:8080 root@${ip:-<this machine>}"
  echo "then open http://localhost:8080"
else
  echo "the service didn't answer; see: journalctl -u localdub-server -n 30" >&2
  exit 1
fi
