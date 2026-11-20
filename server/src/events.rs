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

use serde::{Deserialize, Serialize};

/// One line of the engine's output. The JSON field "event" says which case it is
/// (like System.Text.Json's [JsonPolymorphic(TypeDiscriminatorPropertyName = "event")]).
#[derive(Debug, PartialEq, Deserialize)]
#[serde(tag = "event", rename_all = "lowercase")]
pub enum Event {
    /// A request to a model finished: what, by which model, how long, how fast.
    Call(Call),
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
    /// `localdub languages`: every language the engine knows.
    Languages { languages: Vec<Language> },
    /// How far a stage that works line by line is (translate, voice).
    Progress(Progress),
    #[serde(other)]
    Other,
}

/// `{"event": "progress", "stage": "voice", "label": "voicing, take 1", "done": 31, "total": 58,
/// "line": 31, "at": 72.4}`: `at` is where that line is in the video, in seconds.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct Progress {
    pub stage: String,
    pub label: Option<String>,
    pub done: u32,
    pub total: u32,
    pub line: Option<u32>,
    pub at: Option<f64>,
}

/// A language in the engine's table (src/localdub/languages.py), as `localdub languages` lists it.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct Language {
    /// ISO 639-1, "bg".
    pub code: String,
    /// In English, "Bulgarian".
    pub name: String,
    /// Can a channel be dubbed into it? (Does the voice model speak it?)
    pub dub: bool,
    /// Its spelling dictionary ("bg_BG"), and whether it is installed; any language with one can
    /// be a channel's video language.
    pub dictionary: Option<String>,
    pub dictionary_installed: bool,
    /// The Debian package with the dictionary.
    pub apt: Option<String>,
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

#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct WaitingFor {
    pub kind: String,
    pub file: String,
}

/// One request to a model (src/localdub/llm.py): text ("chat"), voice clone ("tts"), transcription
/// ("asr") or voice/music separation ("separation"). The token counts and speeds are the model
/// server's own numbers, for chat only.
#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
pub struct Call {
    pub kind: String,
    pub model: String,
    pub label: String,
    pub seconds: f64,
    /// Generated tokens, and how fast they came.
    pub tokens: Option<u64>,
    pub tok_per_s: Option<f64>,
    /// The prompt's tokens, and how fast the model read them.
    pub prompt_tokens: Option<u64>,
    pub prompt_per_s: Option<f64>,
    /// For a voice clone: the characters spoken.
    pub chars: Option<u64>,
    /// For a transcription: the audio's length.
    pub audio_seconds: Option<f64>,
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
    fn progress_line_by_line() {
        let voicing = r#"{"event": "progress", "stage": "voice", "label": "voicing, take 1", "done": 31,
                          "total": 58, "line": 31, "at": 72.4}"#;
        let checking = r#"{"event": "progress", "stage": "voice", "label": "checking take 1", "done": 0, "total": 58}"#;

        let Some(Event::Progress(voicing)) = parse_line(voicing) else {
            panic!("not progress")
        };
        let Some(Event::Progress(checking)) = parse_line(checking) else {
            panic!("not progress")
        };

        assert_eq!(
            (voicing.done, voicing.total, voicing.at),
            (31, 58, Some(72.4))
        );
        assert_eq!((checking.line, checking.at), (None, None));
    }

    #[test]
    fn a_model_call() {
        let line = r#"{"event": "call", "kind": "chat", "model": "gemma4-31b", "label": "translating 1-20",
                       "seconds": 24.1, "tokens": 2463, "tok_per_s": 103.2, "prompt_tokens": 1890,
                       "prompt_per_s": 910.4}"#;
        let tts = r#"{"event": "call", "kind": "tts", "model": "qwen3-tts", "label": "voice clone",
                      "seconds": 0.9, "chars": 64}"#;

        let Some(Event::Call(chat)) = parse_line(line) else {
            panic!("not a call: {line}")
        };
        let Some(Event::Call(tts)) = parse_line(tts) else {
            panic!("not a call: {tts}")
        };

        assert_eq!(
            (chat.model.as_str(), chat.tok_per_s),
            ("gemma4-31b", Some(103.2))
        );
        assert_eq!(chat.prompt_tokens, Some(1890));
        assert_eq!(
            (tts.kind.as_str(), tts.chars, tts.tokens),
            ("tts", Some(64), None)
        );
    }

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

    #[test]
    fn the_languages_list() {
        let line = r#"{"event": "languages", "languages": [
            {"code": "en", "name": "English", "dub": true, "dictionary": "en_US", "dictionary_installed": true, "apt": "hunspell-en-us"},
            {"code": "bg", "name": "Bulgarian", "dub": false, "dictionary": "bg_BG", "dictionary_installed": false, "apt": "hunspell-bg"}]}"#;

        let Some(Event::Languages { languages }) = parse_line(line) else {
            panic!("not the languages: {line}")
        };

        assert_eq!(languages.len(), 2);
        assert!(languages[0].dub && !languages[1].dub);
        assert_eq!(languages[1].apt.as_deref(), Some("hunspell-bg"));
    }
}
