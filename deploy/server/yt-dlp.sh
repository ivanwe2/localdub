#!/usr/bin/env bash
# Installs or updates yt-dlp, which the web app runs to download videos from links. As root:
#   bash yt-dlp.sh
# - yt-dlp: the official single-file Linux build, checked against the release's checksums, into
#   /usr/local/bin. Once there it updates itself (yt-dlp -U): sites change often and an old yt-dlp
#   stops working, so deploy/update.sh runs this script every time.
# - Deno: the JavaScript runtime yt-dlp needs for YouTube (since yt-dlp 2025.11), also checked,
#   into /usr/local/bin, if there's none yet.
# Extra yt-dlp options, e.g. cookies or a proxy, can go into /etc/yt-dlp.conf.
set -euo pipefail

bin=/usr/local/bin
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT # at the top level, so $tmp still exists when the trap runs

# Downloads the file $2 from $1 and checks it against the checksum list $3 published beside it.
fetch() {
  local base=$1 file=$2 sums=$3 want got
  curl -fsSL "$base/$file" -o "$tmp/$file"
  curl -fsSL "$base/$sums" -o "$tmp/$sums"
  want=$(awk -v f="$file" '$2 == f || $2 == "*" f { print $1 }' "$tmp/$sums")
  got=$(sha256sum "$tmp/$file" | cut -d' ' -f1)
  if [ -z "$want" ] || [ "$want" != "$got" ]; then
    echo "$file doesn't match its published checksum; nothing was installed" >&2
    exit 1
  fi
}

if [ -x "$bin/yt-dlp" ]; then
  "$bin/yt-dlp" -U || echo "note: yt-dlp couldn't update itself just now; the installed one still works" >&2
else
  fetch https://github.com/yt-dlp/yt-dlp/releases/latest/download yt-dlp_linux SHA2-256SUMS
  install -m 755 "$tmp/yt-dlp_linux" "$bin/yt-dlp"
  echo "installed yt-dlp $("$bin/yt-dlp" --version)"
fi

if ! command -v deno >/dev/null; then
  zip=deno-x86_64-unknown-linux-gnu.zip
  fetch https://github.com/denoland/deno/releases/latest/download "$zip" "$zip.sha256sum"
  if command -v unzip >/dev/null; then
    unzip -q "$tmp/$zip" -d "$tmp/deno"
  else
    python3 -m zipfile -e "$tmp/$zip" "$tmp/deno"
  fi
  install -m 755 "$tmp/deno/deno" "$bin/deno"
  echo "installed $("$bin/deno" --version | head -1)"
fi
