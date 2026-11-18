//! The engine's events: what `localdub run ... --json` prints on stdout, one JSON object per line.
//! The contract is src/localdub/events.py (and the README, "For scripts, servers and agents").
//! A run that stops at the first review prints, for example:
//!
//! ```text
//! {"event": "start", "command": "run", "run": "market", "channel": "mychannel", "dir": "/root/dub/runs/market",
//!  "stages": ["prep", "separate", "transcribe", ...], "done": []}
//! {"event": "stage", "stage": "prep", "status": "started"}
//! {"event": "stage", "stage": "prep", "status": "done", "seconds": 0.4}
//! ...
//! {"event": "stage", "stage": "review1", "status": "waiting"}
//! {"event": "result", "status": "waiting", "exit_code": 3, "run": "market",
//!  "waiting_for": {"kind": "review1", "file": "/root/dub/runs/market/review-1-transcript.txt"}}
//! ```
//!
//! Only the fields the server uses are here; serde ignores the rest.

use serde::Deserialize;

/// One line of the engine's output. The JSON field "event" says which case it is
/// (≈ System.Text.Json's [JsonPolymorphic(TypeDiscriminatorPropertyName = "event")]).
#[derive(Debug, PartialEq, Deserialize)]
#[serde(tag = "event", rename_all = "lowercase")]
pub enum Event {
    /// The command started: the run's name, all its stages, and the ones done in an earlier attempt.
    Start {
        run: String,
        #[serde(default)] // missing → empty list (`localdub shorts` sends no stages)
        stages: Vec<String>,
        #[serde(default)]
        done: Vec<String>,
    },
    Stage {
        stage: String,
        status: StageStatus,
        seconds: Option<f64>,
    },
    Result {
        status: ResultStatus,
        exit_code: i32,
        waiting_for: Option<WaitingFor>,
        error: Option<String>,
    },
    #[serde(other)]
    Other,
}

#[derive(Debug, PartialEq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum StageStatus {
    Started,
    Done,
    Waiting,
}

#[derive(Debug, PartialEq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ResultStatus {
    Done,
    Waiting,
    Error,
}

#[derive(Debug, PartialEq, Deserialize)]
pub struct WaitingFor {
    pub kind: String,
    pub file: String,
}

/// One line of the engine's output as an event; None if the line isn't one (not JSON, or no
/// "event" field).
pub fn parse_line(line: &str) -> Option<Event> {
    serde_json::from_str(line).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn start() {
        let line = r#"{"event": "start", "command": "run", "run": "market", "channel": "mychannel",
                       "dir": "/root/dub/runs/market", "stages": ["prep", "separate"], "done": ["prep"]}"#;
        assert_eq!(
            parse_line(line),
            Some(Event::Start {
                run: "market".into(),
                stages: vec!["prep".into(), "separate".into()],
                done: vec!["prep".into()],
            })
        );
    }

    #[test]
    fn a_stage_that_started_has_no_seconds_yet() {
        let line = r#"{"event": "stage", "stage": "separate", "status": "started"}"#;
        assert_eq!(
            parse_line(line),
            Some(Event::Stage {
                stage: "separate".into(),
                status: StageStatus::Started,
                seconds: None,
            })
        );
    }

    #[test]
    fn a_finished_stage_says_how_long_it_took() {
        let line = r#"{"event": "stage", "stage": "separate", "status": "done", "seconds": 19.3}"#;
        assert_eq!(
            parse_line(line),
            Some(Event::Stage {
                stage: "separate".into(),
                status: StageStatus::Done,
                seconds: Some(19.3),
            })
        );
    }

    #[test]
    fn a_stage_can_wait_for_a_person() {
        let line = r#"{"event": "stage", "stage": "review1", "status": "waiting"}"#;
        assert_eq!(
            parse_line(line),
            Some(Event::Stage {
                stage: "review1".into(),
                status: StageStatus::Waiting,
                seconds: None,
            })
        );
    }

    #[test]
    fn the_result_of_a_finished_run() {
        let line = r#"{"event": "result", "status": "done", "exit_code": 0, "run": "market",
                       "channel": "mychannel", "dir": "/root/dub/runs/market",
                       "outputs": {"video": "/root/dub/runs/market/market.en.mp4"}, "shorts": {},
                       "timings": {"prep": 0.4}}"#;
        assert_eq!(
            parse_line(line),
            Some(Event::Result {
                status: ResultStatus::Done,
                exit_code: 0,
                waiting_for: None,
                error: None,
            })
        );
    }

    #[test]
    fn a_waiting_result_says_what_for() {
        let line = r#"{"event": "result", "status": "waiting", "exit_code": 3, "run": "market",
                       "waiting_for": {"kind": "review1", "file": "/root/dub/runs/market/review-1-transcript.txt"}}"#;
        assert_eq!(
            parse_line(line),
            Some(Event::Result {
                status: ResultStatus::Waiting,
                exit_code: 3,
                waiting_for: Some(WaitingFor {
                    kind: "review1".into(),
                    file: "/root/dub/runs/market/review-1-transcript.txt".into(),
                }),
                error: None,
            })
        );
    }

    #[test]
    fn an_error_result_has_the_message() {
        let line = r#"{"event": "result", "status": "error", "exit_code": 1, "run": "market",
                       "error": "in stage 'voice': HTTP 503", "stage": "voice"}"#;
        assert_eq!(
            parse_line(line),
            Some(Event::Result {
                status: ResultStatus::Error,
                exit_code: 1,
                waiting_for: None,
                error: Some("in stage 'voice': HTTP 503".into()),
            })
        );
    }

    #[test]
    fn other_events_are_recognised_but_not_used() {
        let line = r#"{"event": "check", "what": "ffmpeg", "ok": true, "hint": ""}"#;
        assert_eq!(parse_line(line), Some(Event::Other));
    }

    #[test]
    fn a_line_that_is_not_an_event_is_none() {
        assert_eq!(parse_line("Loading weights: 100%"), None);
        assert_eq!(parse_line(r#"{"no_event_field": 1}"#), None);
        assert_eq!(parse_line(""), None);
    }
}
