#!/usr/bin/env bash
# Builds the web app for a Linux machine: the server as one static binary (musl, so it runs on any
# x86-64 Linux without Rust or matching system libraries) plus the built page, packed as
# dist/localdub-app.tar.gz together with install.sh. Run it in the dev container (CI runs it too,
# .github/workflows/release.yml):
#   bash deploy/server/build.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
# which commit this is, written into the package: update.sh compares it with what's installed
build="$(git rev-parse --short HEAD 2>/dev/null || echo unknown) $(date -u +%Y-%m-%dT%H:%MZ)"

target=x86_64-unknown-linux-musl
(cd web && npm ci --no-audit --no-fund --loglevel=error && npm run build)
(cd server && cargo build --release --locked --target "$target")

out=dist/localdub-app
rm -rf "$out" && mkdir -p "$out"
cp "${CARGO_TARGET_DIR:-server/target}/$target/release/localdub-server" "$out/"
cp -r web/dist "$out/web"
cp deploy/server/install.sh deploy/server/yt-dlp.sh deploy/server/localdub-server.service "$out/"
echo "$build" > "$out/BUILD"
tar -C dist -czf dist/localdub-app.tar.gz localdub-app
echo "built dist/localdub-app.tar.gz ($(du -h dist/localdub-app.tar.gz | cut -f1)), $build"
