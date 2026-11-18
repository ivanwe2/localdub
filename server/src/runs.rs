//! The runs on disk: every folder in `<home>/runs` that has a `state.json`.
//!
//! The engine (src/localdub/run.py) writes state.json and adds fields as stages finish, e.g.:
//!
//! ```json
//! {"source": "/videos/market.mp4", "channel": "mychannel", "start": null, "length": null,
//!  "created": "2026-11-12T22:19:12", "done": ["prep", "separate"], "timings": {"prep": 0.4},
//!  "video": "/videos/market.mp4", "seconds": 170.0}
//! ```
//!
//! `seconds` (the length of the audio) only appears once the prep stage has run; `outputs` (the
//! dubbed video and subtitles) once the mix stage has, and `shorts` once Shorts are rendered.

use std::collections::BTreeMap;
use std::path::Path;

use serde::Serialize;

/// One run in the list that GET /runs returns: the contract with the web UI.
#[derive(Debug, PartialEq, Serialize)]
pub struct RunSummary {
    /// The folder's name, which is also the run's name for the CLI (`localdub run <name>`).
    pub name: String,
    pub channel: String,
    /// When the run was started, ISO 8601 as the engine writes it (so it also sorts as text).
    pub created: String,
    /// The video it dubs.
    pub source: String,
    /// The length of the audio; None until the prep stage has run.
    pub seconds: Option<f64>,
    /// The stages finished so far, in order.
    pub done: Vec<String>,
    /// The dubbed video is ready: the mix stage is done.
    pub finished: bool,
    /// The run's results so far.
    pub files: RunFiles,
}

/// The results of a run, as paths relative to its folder: the browser fetches them from
/// `/files/<run name>/<path>`.
#[derive(Debug, Default, PartialEq, Serialize)]
pub struct RunFiles {
    /// The dubbed video.
    pub video: Option<String>,
    /// The subtitles: the dubbed language first, then the original.
    pub subtitles: Vec<String>,
    /// The rendered Shorts, in both languages.
    pub shorts: Vec<String>,
}

#[derive(serde::Deserialize)]
struct StateFile {
    source: String,
    channel: String,
    created: String,
    done: Vec<String>,
    seconds: Option<f64>,
    #[serde(default)]
    outputs: Outputs,
    /// Language ("dub" / "original") → the Shorts' paths.
    #[serde(default)]
    shorts: BTreeMap<String, Vec<String>>,
}

#[derive(Default, serde::Deserialize)]
struct Outputs {
    video: Option<String>,
    #[serde(default)]
    subtitles: Vec<String>,
}

/// An absolute path the engine wrote, relative to the run's folder. It's found by its
/// `/runs/<name>/` part, so the server and engine needn't spell the home folder the same way.
fn in_run(path: &str, name: &str) -> Option<String> {
    let marker = format!("/runs/{name}/");
    path.find(&marker)
        .map(|i| path[i + marker.len()..].to_string())
}

/// Every run under `home/runs`, newest first.
///
/// - only folders with a state.json are runs; skip anything else in `runs/` (files, empty folders)
/// - a state.json that isn't valid JSON, or lacks a field you need: print a warning and skip that
///   run, so one broken run doesn't break the whole list
/// - no `runs` folder at all (nothing has run yet): an empty list
/// - any other I/O error (e.g. no permission): return it
pub async fn list_runs(home: &Path) -> std::io::Result<Vec<RunSummary>> {
    let mut entries = match tokio::fs::read_dir(home.join("runs")).await {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e),
    };
    let mut runs = Vec::new();

    while let Some(entry) = entries.next_entry().await? {
        if !entry.file_type().await?.is_dir() {
            continue;
        }
        let path = entry.path().join("state.json");
        let text = match tokio::fs::read_to_string(&path).await {
            Ok(text) => text,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(e) => return Err(e),
        };
        let state: StateFile = match serde_json::from_str(&text) {
            Ok(state) => state,
            Err(e) => {
                eprintln!("skipping {}: {e}", path.display());
                continue;
            }
        };
        let name = entry.file_name().to_string_lossy().into_owned();
        let finished = state.done.iter().any(|s| s == "mix");
        let in_this_run = |path: &String| in_run(path, &name);
        let files = RunFiles {
            video: state.outputs.video.as_ref().and_then(in_this_run),
            subtitles: state
                .outputs
                .subtitles
                .iter()
                .filter_map(in_this_run)
                .collect(),
            shorts: state
                .shorts
                .values()
                .flatten()
                .filter_map(in_this_run)
                .collect(),
        };
        runs.push(RunSummary {
            name,
            channel: state.channel,
            created: state.created,
            source: state.source,
            seconds: state.seconds,
            done: state.done,
            finished,
            files,
        });
    }

    runs.sort_by(|a, b| b.created.cmp(&a.created));
    Ok(runs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    /// A run folder with this state.json, the way the engine leaves it.
    fn write_run(home: &Path, name: &str, state: &str) {
        let dir = home.join("runs").join(name);
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("state.json"), state).unwrap();
    }

    fn strings(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[tokio::test]
    async fn lists_runs_newest_first() {
        let home = tempfile::tempdir().unwrap();
        write_run(
            home.path(),
            "market",
            r#"{"source": "/videos/market.mp4", "channel": "mychannel", "start": null, "length": null,
                "created": "2026-11-10T09:00:00",
                "done": ["prep", "separate", "transcribe", "spelling", "review1", "translate", "review2", "voice", "mix"],
                "timings": {"prep": 0.4, "voice": 105.2}, "seconds": 170.0,
                "outputs": {"video": "/runs/market/market.en.mp4"}}"#,
        );
        write_run(
            home.path(),
            "talk-60s-120s",
            r#"{"source": "/videos/talk.mp4", "channel": "other", "start": 60.0, "length": 120.0,
                "created": "2026-11-12T22:19:12", "done": ["prep"], "timings": {"prep": 0.3}, "seconds": 120.0}"#,
        );

        let runs = list_runs(home.path()).await.unwrap();

        assert_eq!(
            runs,
            vec![
                RunSummary {
                    name: "talk-60s-120s".into(),
                    channel: "other".into(),
                    created: "2026-11-12T22:19:12".into(),
                    source: "/videos/talk.mp4".into(),
                    seconds: Some(120.0),
                    done: strings(&["prep"]),
                    finished: false,
                    files: RunFiles::default(),
                },
                RunSummary {
                    name: "market".into(),
                    channel: "mychannel".into(),
                    created: "2026-11-10T09:00:00".into(),
                    source: "/videos/market.mp4".into(),
                    seconds: Some(170.0),
                    done: strings(&[
                        "prep",
                        "separate",
                        "transcribe",
                        "spelling",
                        "review1",
                        "translate",
                        "review2",
                        "voice",
                        "mix",
                    ]),
                    finished: true,
                    files: RunFiles {
                        video: Some("market.en.mp4".into()),
                        ..RunFiles::default()
                    },
                },
            ]
        );
    }

    #[tokio::test]
    async fn a_finished_run_lists_its_results_relative_to_its_folder() {
        let home = tempfile::tempdir().unwrap();
        write_run(
            home.path(),
            "market",
            r#"{"source": "/videos/market.mp4", "channel": "c", "created": "2026-11-10T09:00:00",
                "done": ["prep", "mix"], "timings": {},
                "outputs": {"video": "/root/dub/runs/market/market.en.mp4",
                            "subtitles": ["/root/dub/runs/market/market.en.srt", "/root/dub/runs/market/market.bg.srt"],
                            "check": "/root/dub/runs/market/check.txt", "lines_to_check": 0},
                "shorts": {"original": ["/root/dub/runs/market/shorts/original/short-1-a.mp4"],
                           "dub": ["/root/dub/runs/market/shorts/dub/short-2-b.mp4", "/elsewhere/x.mp4"]}}"#,
        );

        let runs = list_runs(home.path()).await.unwrap();

        assert_eq!(
            runs[0].files,
            RunFiles {
                video: Some("market.en.mp4".into()),
                subtitles: strings(&["market.en.srt", "market.bg.srt"]),
                shorts: strings(&["shorts/dub/short-2-b.mp4", "shorts/original/short-1-a.mp4"]),
            },
            "a path outside the run's folder is left out"
        );
    }

    #[tokio::test]
    async fn a_new_run_has_no_length_yet() {
        let home = tempfile::tempdir().unwrap();
        write_run(
            home.path(),
            "fresh",
            r#"{"source": "/videos/new.mp4", "channel": "mychannel", "start": null, "length": null,
                "created": "2026-11-16T10:00:00", "done": [], "timings": {}}"#,
        );

        let runs = list_runs(home.path()).await.unwrap();

        assert_eq!(runs.len(), 1);
        assert_eq!(runs[0].seconds, None);
        assert!(runs[0].done.is_empty());
        assert!(!runs[0].finished);
    }

    #[tokio::test]
    async fn skips_what_is_not_a_readable_run() {
        let home = tempfile::tempdir().unwrap();
        write_run(
            home.path(),
            "good",
            r#"{"source": "/v.mp4", "channel": "c", "created": "2026-11-16T10:00:00", "done": [], "timings": {}}"#,
        );
        write_run(home.path(), "broken", "{not json");
        write_run(
            home.path(),
            "no-channel",
            r#"{"source": "/v.mp4", "created": "2026-11-16T10:00:00", "done": []}"#,
        );
        fs::create_dir_all(home.path().join("runs").join("empty-folder")).unwrap();
        fs::write(home.path().join("runs").join("notes.txt"), "not a run").unwrap();

        let runs = list_runs(home.path()).await.unwrap();

        let names: Vec<&str> = runs.iter().map(|r| r.name.as_str()).collect();
        assert_eq!(names, vec!["good"]);
    }

    #[tokio::test]
    async fn no_runs_folder_means_no_runs() {
        let home = tempfile::tempdir().unwrap();

        let runs = list_runs(home.path()).await.unwrap();

        assert!(runs.is_empty());
    }
}
