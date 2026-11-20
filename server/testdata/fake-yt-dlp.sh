#!/bin/sh
# A stand-in for yt-dlp in the server's tests: prints the lines the server asks the real one for
# (--print and --progress-template, see src/download.rs) and makes a small file, without any
# network. The link's last part picks the story: ok, slow, odd-name, fails.
dir='' url=''
while [ $# -gt 0 ]; do
  case "$1" in
    -P) case "$2" in temp:*) ;; *) dir=$2 ;; esac; shift 2 ;;
    --) url=$2; break ;;
    *) shift ;;
  esac
done
if [ -z "$dir" ] || [ -z "$url" ]; then
  echo "ERROR: fake-yt-dlp: no -P folder, or no link after --" >&2
  exit 2
fi

progress() {
  echo "[progress] {\"status\": \"downloading\", \"downloaded_bytes\": $1, \"total_bytes\": 2000, \"speed\": 1000.0, \"eta\": 1, \"filename\": \"$2\"}"
}

case "$url" in
  */ok|*/slow)
    echo "[info] abc123 137+140 A test video"
    mkdir -p "$dir/.partial" && echo half > "$dir/.partial/yt-abc123.f137.mp4.part"
    progress 1000 "$dir/yt-abc123-A-test-video.f137.mp4"
    [ "${url##*/}" = slow ] && sleep 1
    progress 2000 "$dir/yt-abc123-A-test-video.f137.mp4"
    progress 2000 "$dir/yt-abc123-A-test-video.f140.m4a"
    echo "not really a video" > "$dir/yt-abc123-A-test-video.mp4"
    echo "[file] $dir/yt-abc123-A-test-video.mp4"
    exit 0 ;;
  */odd-name)
    # a name the server doesn't accept, and no [file] line: found by its id, then renamed
    echo "[info] odd1 18 Odd: name?"
    echo "video" > "$dir/yt-odd1-Odd: name?.mp4"
    exit 0 ;;
  */fails)
    echo "WARNING: [generic] Falling back on generic information extractor" >&2
    echo "ERROR: [generic] Unable to download webpage: HTTP Error 404: Not Found" >&2
    exit 1 ;;
  *)
    echo "ERROR: fake-yt-dlp: no story for $url" >&2
    exit 9 ;;
esac
