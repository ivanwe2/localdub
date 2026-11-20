//! Downloading a video from a link (YouTube and the other sites yt-dlp knows) into the videos
//! folder. A download needs no GPU, so it runs beside a dub; one download at a time. The page
//! follows it live (GET /api/download/events), like the job.
//!
//! yt-dlp is asked to print three kinds of lines on stdout, which `parse_line` reads:
//!
//! ```text
//! [info] dQw4w9WgXcQ 137+140 The video's title        the id, the formats (picture+sound), the title
//! [progress] {"downloaded_bytes": 1024, "total_bytes": 4096, "speed": 512.0, "eta": 6, ...}
//! [file] /root/dub/videos/yt-dQw4w9WgXcQ-The-video-s-title.mp4
//! ```
//!
//! Its warnings and errors go to stderr (`ERROR: ...`).

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::start::{is_inside, is_safe_file_name};
use crate::videos::{Video, safe_file_name};

/// How many of yt-dlp's latest messages (stderr) a download keeps for the page.
pub const KEEP_LOG: usize = 100;

/// The body of POST /api/downloads.
#[derive(Debug, PartialEq, Deserialize)]
pub struct DownloadRequest {
    pub url: String,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum DownloadState {
    Downloading,
    /// The video is in the videos folder (`video`).
    Done,
    Failed,
}

/// The one download going on (or the last one), as the page sees it.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Download {
    pub url: String,
    pub state: DownloadState,
    /// The video's title, once yt-dlp has found it.
    pub title: Option<String>,
    /// Which file is coming now (from 1), of how many: YouTube sends the picture and the sound
    /// separately, and yt-dlp puts them together at the end.
    pub part: u32,
    pub parts: u32,
    /// Of the file coming now; the total is an estimate on some sites.
    pub downloaded_bytes: Option<u64>,
    pub total_bytes: Option<u64>,
    pub speed_bps: Option<f64>,
    pub eta_s: Option<f64>,
    /// The finished video, to start a run with.
    pub video: Option<Video>,
    pub error: Option<String>,
    /// yt-dlp's latest warnings and errors, oldest first.
    pub log: Vec<String>,
    /// The video's id on its site, and the file yt-dlp says it made (they find the video).
    #[serde(skip)]
    id: Option<String>,
    #[serde(skip)]
    file: Option<String>,
    /// The file the progress lines are about, to notice when the next part starts.
    #[serde(skip)]
    part_file: Option<String>,
}

/// One line yt-dlp printed on stdout.
#[derive(Debug, PartialEq)]
pub enum Line {
    Info {
        id: String,
        formats: String,
        title: String,
    },
    Progress {
        downloaded_bytes: Option<u64>,
        total_bytes: Option<u64>,
        speed: Option<f64>,
        eta: Option<f64>,
        filename: Option<String>,
    },
    File(String),
    /// Anything else (yt-dlp's own messages, should a setting let them through).
    Other,
}

/// Reads one line of yt-dlp's stdout. Missing or odd progress fields are just left empty.
pub fn parse_line(line: &str) -> Line {
    if let Some(rest) = line.strip_prefix("[info] ") {
        let mut parts = rest.splitn(3, ' ');
        if let (Some(id), Some(formats)) = (parts.next(), parts.next()) {
            return Line::Info {
                id: id.to_string(),
                formats: formats.to_string(),
                title: parts.next().unwrap_or("").to_string(),
            };
        }
    }
    if let Some(json) = line.strip_prefix("[progress] ")
        && let Ok(p) = serde_json::from_str::<Value>(json)
    {
        let number = |key: &str| p.get(key).and_then(Value::as_f64).filter(|n| *n >= 0.0);
        return Line::Progress {
            downloaded_bytes: number("downloaded_bytes").map(|n| n as u64),
            total_bytes: number("total_bytes")
                .or_else(|| number("total_bytes_estimate"))
                .map(|n| n as u64),
            speed: number("speed"),
            eta: number("eta"),
            filename: p.get("filename").and_then(Value::as_str).map(String::from),
        };
    }
    if let Some(path) = line.strip_prefix("[file] ") {
        return Line::File(path.to_string());
    }
    Line::Other
}

impl Download {
    pub fn new(url: &str) -> Download {
        Download {
            url: url.to_string(),
            state: DownloadState::Downloading,
            title: None,
            part: 0,
            parts: 1,
            downloaded_bytes: None,
            total_bytes: None,
            speed_bps: None,
            eta_s: None,
            video: None,
            error: None,
            log: Vec::new(),
            id: None,
            file: None,
            part_file: None,
        }
    }

    /// Updates the download from one of yt-dlp's lines.
    pub fn apply(&mut self, line: Line) {
        match line {
            Line::Info { id, formats, title } => {
                self.id = Some(id);
                self.parts = formats.split('+').count() as u32;
                self.title = Some(title).filter(|t| !t.is_empty() && t != "NA");
            }
            Line::Progress {
                downloaded_bytes,
                total_bytes,
                speed,
                eta,
                filename,
            } => {
                if self.part == 0 || filename != self.part_file {
                    self.part += 1;
                    self.parts = self.parts.max(self.part);
                    self.part_file = filename;
                }
                self.downloaded_bytes = downloaded_bytes;
                self.total_bytes = total_bytes;
                self.speed_bps = speed;
                self.eta_s = eta;
            }
            Line::File(path) => self.file = Some(path),
            Line::Other => {}
        }
    }

    /// One line yt-dlp wrote on stderr.
    pub fn log_line(&mut self, line: String) {
        self.log.push(line);
        if self.log.len() > KEEP_LOG {
            self.log.drain(..self.log.len() - KEEP_LOG);
        }
    }

    /// yt-dlp has exited: the video it made, or why there is none.
    pub fn finish(&mut self, outcome: Result<Video, String>) {
        self.speed_bps = None;
        self.eta_s = None;
        match outcome {
            Ok(video) => {
                self.state = DownloadState::Done;
                self.video = Some(video);
            }
            Err(error) => {
                self.state = DownloadState::Failed;
                self.error = Some(error);
            }
        }
    }

    /// Why yt-dlp failed, in its own words if it said: its last `ERROR:` line.
    fn failure(&self, code: i32) -> String {
        let said = self
            .log
            .iter()
            .rev()
            .find_map(|l| l.strip_prefix("ERROR: "));
        match said {
            Some(error) => error.to_string(),
            None => format!("yt-dlp stopped with exit code {code}"),
        }
    }
}

/// A link yt-dlp may be given: http(s), one piece (no spaces), not too long. It always comes after
/// `--` too, so it can never pass for an option.
pub fn check_url(url: &str) -> Result<(), String> {
    let rest = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"));
    if rest.is_none_or(str::is_empty) {
        return Err("a link to the video, starting with https://".to_string());
    }
    if url.len() > 2048 {
        return Err("that link is too long (at most 2048 characters)".to_string());
    }
    if url.chars().any(|c| c.is_whitespace() || c.is_control()) {
        return Err("a link has no spaces in it".to_string());
    }
    Ok(())
}

/// yt-dlp's arguments for downloading `url` into the videos folder `dir`:
/// - one video, even from a link into a playlist;
/// - the best picture up to 1080p, preferably H.264 with AAC sound (plays everywhere), as mp4;
///   picture and sound as separate files where the site has them (YouTube refuses its old
///   single-file format 18 these days, with 403 Forbidden);
/// - named `yt-<id>-<title>.mp4`, the title cut to 60 bytes with anything but letters, digits,
///   '.', '_' and '-' turned into '-', so the name passes the server's file-name rule;
/// - unfinished files in the hidden `.partial` folder, so the videos list never shows them;
/// - the lines `parse_line` reads (`--print` makes yt-dlp quiet otherwise; `--progress` keeps the
///   progress).
pub fn args(dir: &Path, url: &str) -> Vec<String> {
    let dir = dir.to_string_lossy().into_owned();
    let partial = format!("temp:{dir}/.partial");
    [
        "--newline",
        "--no-playlist",
        "--playlist-items",
        "1",
        "--socket-timeout",
        "30",
        "-f",
        "bv+ba/b",
        "-S",
        "res:1080,vcodec:h264,acodec:aac",
        "--merge-output-format",
        "mp4",
        "--replace-in-metadata",
        "title",
        r"[^\w.-]+",
        "-",
        "--replace-in-metadata",
        "title",
        r"^[-.]+|[-.]+$",
        "",
        "-P",
        dir.as_str(),
        "-P",
        partial.as_str(),
        "-o",
        "yt-%(id)s-%(title).60B.%(ext)s",
        "--progress",
        "--progress-template",
        "download:[progress] %(progress)j",
        "--print",
        "before_dl:[info] %(id)s %(format_id)s %(fulltitle)s",
        "--print",
        "after_move:[file] %(filepath)s",
        "--",
        url,
    ]
    .map(String::from)
    .to_vec()
}

/// What became of the download once yt-dlp exited with `code`: the video in `dir` (renamed to a
/// safe name if it needs one), or why there is none.
pub async fn outcome(dir: &Path, download: &Download, code: i32) -> Result<Video, String> {
    if code != 0 {
        return Err(download.failure(code));
    }
    let Some(path) = find_file(dir, download).await else {
        return Err(format!(
            "yt-dlp finished, but the video isn't in {}",
            dir.display()
        ));
    };
    let path = with_safe_name(dir, path)
        .await
        .map_err(|e| format!("could not rename the video: {e}"))?;
    Video::at(&path).await.map_err(|e| e.to_string())
}

/// The file yt-dlp made: the one it printed, else the one named after the video's id (yt-dlp
/// prints nothing for a video it had downloaded before, on some versions).
async fn find_file(dir: &Path, download: &Download) -> Option<PathBuf> {
    if let Some(file) = &download.file {
        let path = PathBuf::from(file);
        if path.is_file()
            && path
                .parent()
                .is_some_and(|p| is_inside(p, dir) && is_inside(dir, p))
        {
            return Some(path);
        }
    }
    let id = download.id.as_ref()?;
    let prefixes = [format!("yt-{id}-"), format!("yt-{id}.")];
    let mut entries = tokio::fs::read_dir(dir).await.ok()?;
    while let Ok(Some(entry)) = entries.next_entry().await {
        let name = entry.file_name().to_string_lossy().into_owned();
        let is_file = entry.metadata().await.is_ok_and(|m| m.is_file());
        if is_file && !name.ends_with(".part") && prefixes.iter().any(|p| name.starts_with(p)) {
            return Some(entry.path());
        }
    }
    None
}

/// The video under a name the server accepts: as it is, or renamed (`-2`, `-3`… if that's taken).
async fn with_safe_name(dir: &Path, path: PathBuf) -> std::io::Result<PathBuf> {
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    if is_safe_file_name(&name) {
        return Ok(path);
    }
    let safe = safe_file_name(&name);
    let (stem, ext) = safe.rsplit_once('.').unwrap_or((&safe, ""));
    for n in 1..100 {
        let candidate = match (n, ext) {
            (1, _) => safe.clone(),
            (_, "") => format!("{stem}-{n}"),
            _ => format!("{stem}-{n}.{ext}"),
        };
        let new = dir.join(candidate);
        if !tokio::fs::try_exists(&new).await.unwrap_or(true) {
            tokio::fs::rename(&path, &new).await?;
            return Ok(new);
        }
    }
    Err(std::io::Error::other(format!(
        "no free name like {safe} in {}",
        dir.display()
    )))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn progress(done: u64, file: &str) -> Line {
        parse_line(&format!(
            r#"[progress] {{"status": "downloading", "downloaded_bytes": {done}, "total_bytes": 4000, "speed": 512.5, "eta": 6, "filename": "{file}"}}"#
        ))
    }

    #[test]
    fn yt_dlps_lines_are_read() {
        assert_eq!(
            parse_line("[info] dQw4w9WgXcQ 137+140 A title with spaces"),
            Line::Info {
                id: "dQw4w9WgXcQ".into(),
                formats: "137+140".into(),
                title: "A title with spaces".into()
            }
        );
        assert_eq!(
            progress(1000, "a.f137.mp4"),
            Line::Progress {
                downloaded_bytes: Some(1000),
                total_bytes: Some(4000),
                speed: Some(512.5),
                eta: Some(6.0),
                filename: Some("a.f137.mp4".into())
            }
        );
        assert_eq!(
            parse_line("[file] /home/videos/yt-x-y.mp4"),
            Line::File("/home/videos/yt-x-y.mp4".into())
        );
        assert_eq!(parse_line("[download] Destination: x"), Line::Other);
        assert_eq!(parse_line("[progress] not json"), Line::Other);
    }

    #[test]
    fn missing_progress_fields_stay_empty_and_an_estimate_will_do() {
        let line = parse_line(
            r#"[progress] {"downloaded_bytes": 10.0, "total_bytes": null, "total_bytes_estimate": 99.7, "speed": null}"#,
        );

        assert_eq!(
            line,
            Line::Progress {
                downloaded_bytes: Some(10),
                total_bytes: Some(99),
                speed: None,
                eta: None,
                filename: None
            }
        );
    }

    #[test]
    fn the_picture_and_the_sound_are_counted_as_parts() {
        let mut d = Download::new("https://example.com/v");

        d.apply(parse_line("[info] abc 137+140 Title"));
        d.apply(progress(1000, "a.f137.mp4"));
        d.apply(progress(4000, "a.f137.mp4"));
        let first = (d.part, d.parts, d.downloaded_bytes);
        d.apply(progress(500, "a.f140.m4a"));

        assert_eq!(first, (1, 2, Some(4000)));
        assert_eq!((d.part, d.parts, d.downloaded_bytes), (2, 2, Some(500)));
        assert_eq!(d.title.as_deref(), Some("Title"));
    }

    #[test]
    fn a_failure_is_told_in_yt_dlps_words() {
        let mut d = Download::new("https://example.com/v");
        d.log_line("WARNING: something minor".into());
        d.log_line("ERROR: [youtube] abc: Video unavailable".into());

        assert_eq!(d.failure(1), "[youtube] abc: Video unavailable");
        assert_eq!(
            Download::new("x").failure(2),
            "yt-dlp stopped with exit code 2"
        );
    }

    #[test]
    fn only_plain_web_links_are_downloaded() {
        assert_eq!(
            check_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            Ok(())
        );
        assert_eq!(check_url("http://example.com/v.mp4"), Ok(()));
        let long = format!("https://example.com/{}", "a".repeat(2050));
        for bad in [
            "",
            "-x",
            "--exec=rm",
            "https://",
            "ftp://example.com/v",
            "file:///etc/passwd",
            "https://example.com/a b",
            "https://example.com/\n--exec",
            &long,
        ] {
            assert!(check_url(bad).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn the_link_comes_last_after_the_end_of_options() {
        let args = args(Path::new("/root/dub/videos"), "https://example.com/v");

        assert_eq!(args[args.len() - 2..], ["--", "https://example.com/v"]);
        assert!(args.contains(&"temp:/root/dub/videos/.partial".to_string()));
    }

    #[tokio::test]
    async fn the_video_is_found_by_its_id_and_given_a_safe_name() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("yt-abc-Пазар в събота.mp4"), "video").unwrap();
        std::fs::write(dir.path().join("yt-abc-other.mp4.part"), "half").unwrap();
        let mut d = Download::new("https://example.com/v");
        d.apply(parse_line("[info] abc 18 Пазар в събота"));

        let video = outcome(dir.path(), &d, 0).await.unwrap();

        assert_eq!(video.name, "yt-abc-Пазар-в-събота.mp4");
        assert_eq!(video.bytes, 5);
        assert!(dir.path().join("yt-abc-Пазар-в-събота.mp4").is_file());
    }

    #[tokio::test]
    async fn a_printed_file_outside_the_videos_folder_is_not_taken() {
        let dir = tempfile::tempdir().unwrap();
        let elsewhere = tempfile::NamedTempFile::new().unwrap();
        let mut d = Download::new("https://example.com/v");
        d.apply(Line::File(elsewhere.path().to_string_lossy().into_owned()));

        let error = outcome(dir.path(), &d, 0).await.unwrap_err();

        assert!(error.contains("isn't in"), "{error}");
    }
}
