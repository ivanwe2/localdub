//! A run's voice samples for the page's voice picker: what `localdub voice <run>` leaves in the
//! run's voice-candidates.json (version 2): up to 3 stretches of the run's speech and the channel's
//! voice now, each with test sentences cloned from it and, with the speaker model, a score. Paths are
//! relative to the run's folder; the page fetches them from `/files/<run>/<path>`.

use std::path::Path;

use serde::{Deserialize, Serialize};

/// The file, in the run's folder.
pub const SAMPLES_FILE: &str = "voice-candidates.json";

#[derive(Debug, PartialEq, Deserialize, Serialize)]
pub struct VoiceSamples {
    /// The dub language: the test sentences are in it.
    pub lang: String,
    /// The test sentences, in the order of each sample's `tests`.
    pub lines: Vec<String>,
    /// Were the samples scored? (Only with the speaker model installed.)
    pub scored: bool,
    /// The candidates, best first when scored.
    pub candidates: Vec<Candidate>,
    /// The channel's voice now; None if it has none yet.
    pub current: Option<Current>,
    /// What was picked: a candidate's number, "current", a stretch, a file; None while waiting.
    pub picked: Option<serde_json::Value>,
}

#[derive(Debug, PartialEq, Deserialize, Serialize)]
pub struct Candidate {
    /// Its number, the same whatever the order (for picking it).
    pub n: u32,
    /// Where in the video it is, in seconds.
    pub start: f64,
    pub end: f64,
    /// How much its clones sound like the speaker, 0..1; None without the speaker model.
    pub sim: Option<f64>,
    /// The sample itself.
    pub path: String,
    /// The test sentences cloned from it.
    pub tests: Vec<String>,
}

#[derive(Debug, PartialEq, Deserialize, Serialize)]
pub struct Current {
    /// Where the channel's voice came from, e.g. "market, candidate 2 (…)".
    pub from: String,
    pub sim: Option<f64>,
    pub path: String,
    pub tests: Vec<String>,
}

/// The run's voice samples; None if it has none, or only a file from before version 2 (a plain
/// list, without test clones: asking again makes a new one).
pub async fn read_samples(run_dir: &Path) -> std::io::Result<Option<VoiceSamples>> {
    let text = match tokio::fs::read_to_string(run_dir.join(SAMPLES_FILE)).await {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    Ok(serde_json::from_str(&text).ok())
}

/// Does the run wait for a voice to be picked? (Samples made, nothing picked yet.)
pub async fn waits_for_pick(run_dir: &Path) -> bool {
    matches!(read_samples(run_dir).await, Ok(Some(samples)) if samples.picked.is_none())
}

/// The body of POST /api/runs/{name}/voice/pick: `{"pick": 2}` or `{"pick": "current"}`.
#[derive(Debug, PartialEq, Deserialize)]
pub struct PickRequest {
    pub pick: Pick,
}

/// A candidate's number, or a word ("current": keep the channel's voice). `untagged`: serde tries
/// each case in turn (C#: a JSON converter that accepts a number or a string).
#[derive(Debug, PartialEq, Deserialize)]
#[serde(untagged)]
pub enum Pick {
    Number(u32),
    Word(String),
}

const PICK_RULE: &str = "a candidate's number, or \"current\" to keep the channel's voice";

impl Pick {
    /// The engine's `--pick` argument, or why this pick is refused.
    pub fn as_arg(&self) -> Result<String, String> {
        match self {
            Pick::Number(n) if (1..=99).contains(n) => Ok(n.to_string()),
            Pick::Word(word) if word == "current" => Ok(word.clone()),
            Pick::Number(n) => Err(format!("pick {n}: {PICK_RULE}")),
            Pick::Word(word) => Err(format!("pick {word:?}: {PICK_RULE}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLES: &str = r#"{"version": 2, "lang": "en", "lines": ["Hi everyone."], "scored": true,
        "candidates": [{"n": 2, "start": 40.0, "end": 52.0, "ids": [7, 8], "snr_db": 31.5, "sim": 0.97,
                        "path": "voice-candidates/candidate-2.wav", "tests": ["voice-candidates/test-ab-1.wav"]}],
        "current": {"from": "market, candidate 1", "sim": 0.95, "path": "voice-candidates/current-cd.wav",
                    "tests": ["voice-candidates/test-cd-1.wav"]},
        "picked": null}"#;

    fn run_dir_with(text: Option<&str>) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        if let Some(text) = text {
            std::fs::write(dir.path().join(SAMPLES_FILE), text).unwrap();
        }
        dir
    }

    #[tokio::test]
    async fn samples_are_read_and_an_open_pick_is_waited_for() {
        let dir = run_dir_with(Some(SAMPLES));

        let samples = read_samples(dir.path()).await.unwrap().unwrap();

        assert_eq!(samples.candidates[0].n, 2);
        assert_eq!(samples.current.as_ref().unwrap().sim, Some(0.95));
        assert!(waits_for_pick(dir.path()).await);
    }

    #[tokio::test]
    async fn nothing_to_pick_without_samples_after_a_pick_or_from_an_older_engine() {
        let picked = SAMPLES.replace(r#""picked": null"#, r#""picked": 2"#);
        let old = r#"[{"start": 40.0, "end": 52.0, "path": "/root/dub/runs/m/voice-candidates/candidate-1.wav"}]"#;

        for text in [None, Some(picked.as_str()), Some(old)] {
            assert!(!waits_for_pick(run_dir_with(text).path()).await, "{text:?}");
        }
        assert_eq!(
            read_samples(run_dir_with(Some(old)).path()).await.unwrap(),
            None
        );
    }

    #[test]
    fn a_pick_is_a_candidates_number_or_current() {
        let pick = |json: &str| serde_json::from_str::<PickRequest>(json).map(|r| r.pick.as_arg());

        assert_eq!(pick(r#"{"pick": 2}"#).unwrap(), Ok("2".to_string()));
        assert_eq!(
            pick(r#"{"pick": "current"}"#).unwrap(),
            Ok("current".to_string())
        );
        for bad in [
            r#"{"pick": 0}"#,
            r#"{"pick": "--help"}"#,
            r#"{"pick": "01:20-01:38"}"#,
        ] {
            assert!(pick(bad).unwrap().is_err(), "{bad}");
        }
        assert!(pick(r#"{"pick": -1}"#).is_err());
    }
}
