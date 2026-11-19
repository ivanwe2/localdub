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
# restarting the service stops a run that is going; don't do that unless asked to
if [ "${1:-}" != "--force" ] && curl -sf http://127.0.0.1:8080/api/job 2>/dev/null | grep -q '"state":"running"'; then
  echo "a run is going in the web app; try again when it's done (or add --force to stop it)" >&2
  exit 1
fi

install -d /opt/localdub
install -m 755 "$here/localdub-server" /opt/localdub/localdub-server.new
mv /opt/localdub/localdub-server.new /opt/localdub/localdub-server   # replace it in one step
rm -rf /opt/localdub/web.new && cp -r "$here/web" /opt/localdub/web.new
rm -rf /opt/localdub/web && mv /opt/localdub/web.new /opt/localdub/web
install -m 644 "$here/localdub-server.service" /etc/systemd/system/localdub-server.service
[ -f "$here/BUILD" ] && install -m 644 "$here/BUILD" /opt/localdub/BUILD

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
