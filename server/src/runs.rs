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
    /// What a person has to do before the run can go on; None if nothing.
    pub waiting_for: Option<Waiting>,
}

/// A run waiting for a person: a file to edit and approve (its first line `status: approved`),
/// then the run is continued.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Waiting {
    /// "review1" (the transcript), "review2" (the translation) or "shorts" (picking Shorts).
    pub kind: String,
    /// For Shorts: whose candidates, "dub" or "original".
    pub lang: Option<String>,
    /// The file to edit, relative to the run's folder.
    pub file: String,
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
        let name = entry.file_name().to_string_lossy().into_owned();
        if let Some(run) = read_summary(&entry.path(), name).await? {
            runs.push(run);
        }
    }

    runs.sort_by(|a, b| b.created.cmp(&a.created));
    Ok(runs)
}

/// One run by name; None if there is no such run (or its state.json can't be read).
pub async fn read_run(home: &Path, name: &str) -> std::io::Result<Option<RunSummary>> {
    read_summary(&home.join("runs").join(name), name.to_string()).await
}

/// The run in folder `dir`; None if it has no readable state.json (a warning is printed if it's
/// broken), an error only for other I/O problems.
async fn read_summary(dir: &Path, name: String) -> std::io::Result<Option<RunSummary>> {
    let path = dir.join("state.json");
    let text = match tokio::fs::read_to_string(&path).await {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) if e.kind() == std::io::ErrorKind::NotADirectory => return Ok(None),
        Err(e) => return Err(e),
    };
    let state: StateFile = match serde_json::from_str(&text) {
        Ok(state) => state,
        Err(e) => {
            eprintln!("skipping {}: {e}", path.display());
            return Ok(None);
        }
    };
    let finished = state.done.iter().any(|s| s == "mix");
    let waiting_for = waiting_for(dir, &state, finished).await;
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
    Ok(Some(RunSummary {
        name,
        channel: state.channel,
        created: state.created,
        source: state.source,
        seconds: state.seconds,
        done: state.done,
        finished,
        files,
        waiting_for,
    }))
}

/// What the run waits for, worked out from its files, so it is known after a server restart too:
/// - before the mix: a review whose stage isn't done yet and whose file the engine has written
/// - once finished: Shorts candidates the engine proposed but hasn't rendered yet
async fn waiting_for(dir: &Path, state: &StateFile, finished: bool) -> Option<Waiting> {
    let done = |stage: &str| state.done.iter().any(|s| s == stage);
    if !finished {
        for (stage, file) in [
            ("review1", "review-1-transcript.txt"),
            ("review2", "review-2-translation.txt"),
        ] {
            if !done(stage) && exists(&dir.join(file)).await {
                return Some(Waiting {
                    kind: stage.to_string(),
                    lang: None,
                    file: file.to_string(),
                });
            }
        }
        return None;
    }
    for lang in ["original", "dub"] {
        let file = format!("shorts/{lang}/candidates.txt");
        if !state.shorts.contains_key(lang) && exists(&dir.join(&file)).await {
            return Some(Waiting {
                kind: "shorts".to_string(),
                lang: Some(lang.to_string()),
                file,
            });
        }
    }
    None
}

async fn exists(path: &Path) -> bool {
    tokio::fs::try_exists(path).await.unwrap_or(false)
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
                    waiting_for: None,
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
                    waiting_for: None,
                },
            ]
        );
    }

    /// A file inside a run's folder (sub folders are made).
    fn write_file(home: &Path, run: &str, path: &str, text: &str) {
        let file = home.join("runs").join(run).join(path);
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(file, text).unwrap();
    }

    const BEFORE_REVIEW1: &str = r#"{"source": "/v.mp4", "channel": "c", "created": "2026-11-16T10:00:00",
        "done": ["prep", "separate", "transcribe", "spelling"], "timings": {}}"#;
    const BEFORE_REVIEW2: &str = r#"{"source": "/v.mp4", "channel": "c", "created": "2026-11-16T10:00:00",
        "done": ["prep", "separate", "transcribe", "spelling", "review1", "translate"], "timings": {}}"#;
    const FINISHED: &str = r#"{"source": "/v.mp4", "channel": "c", "created": "2026-11-16T10:00:00",
        "done": ["prep", "review1", "review2", "voice", "mix"], "timings": {}}"#;

    async fn waiting(home: &Path, run: &str) -> Option<Waiting> {
        read_run(home, run).await.unwrap().unwrap().waiting_for
    }

    #[tokio::test]
    async fn a_run_at_a_review_waits_for_it() {
        let home = tempfile::tempdir().unwrap();
        write_run(home.path(), "one", BEFORE_REVIEW1);
        write_file(
            home.path(),
            "one",
            "review-1-transcript.txt",
            "status: pending\n",
        );
        write_run(home.path(), "two", BEFORE_REVIEW2);
        write_file(
            home.path(),
            "two",
            "review-1-transcript.txt",
            "status: approved\n",
        );
        write_file(
            home.path(),
            "two",
            "review-2-translation.txt",
            "status: pending\n",
        );

        let one = waiting(home.path(), "one").await.unwrap();
        let two = waiting(home.path(), "two").await.unwrap();

        assert_eq!(
            (one.kind.as_str(), one.file.as_str()),
            ("review1", "review-1-transcript.txt")
        );
        assert_eq!(
            (two.kind.as_str(), two.file.as_str()),
            ("review2", "review-2-translation.txt")
        );
    }

    #[tokio::test]
    async fn a_run_waits_for_nothing_before_its_review_file_exists_or_after_it_is_done() {
        let home = tempfile::tempdir().unwrap();
        write_run(home.path(), "early", BEFORE_REVIEW1); // still transcribing: no review file yet
        write_run(home.path(), "skipped", FINISHED); // --no-review leaves the files, but the stages are done
        write_file(
            home.path(),
            "skipped",
            "review-1-transcript.txt",
            "status: pending\n",
        );

        assert_eq!(waiting(home.path(), "early").await, None);
        assert_eq!(waiting(home.path(), "skipped").await, None);
    }

    #[tokio::test]
    async fn a_finished_run_waits_for_shorts_proposed_but_not_rendered() {
        let home = tempfile::tempdir().unwrap();
        write_run(home.path(), "picking", FINISHED);
        write_file(
            home.path(),
            "picking",
            "shorts/dub/candidates.txt",
            "status: pending\n",
        );
        let rendered = FINISHED.replace(
            r#""timings": {}"#,
            r#""timings": {}, "shorts": {"dub": ["/root/dub/runs/done/shorts/dub/short-1-x.mp4"]}"#,
        );
        write_run(home.path(), "done", &rendered);
        write_file(
            home.path(),
            "done",
            "shorts/dub/candidates.txt",
            "status: approved\n",
        );

        let picking = waiting(home.path(), "picking").await.unwrap();

        assert_eq!(picking.kind, "shorts");
        assert_eq!(picking.lang.as_deref(), Some("dub"));
        assert_eq!(picking.file, "shorts/dub/candidates.txt");
        assert_eq!(waiting(home.path(), "done").await, None);
    }

    #[tokio::test]
    async fn reading_a_run_that_does_not_exist_gives_none() {
        let home = tempfile::tempdir().unwrap();

        assert_eq!(read_run(home.path(), "nothing").await.unwrap(), None);
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
