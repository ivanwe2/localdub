//! The job: the one engine run the server is doing (or did last), as the browser will see it.
//! It starts as "running" and is updated from the engine's events as they arrive (runner.rs).

use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;

use crate::events::{Call, Event, Progress, ResultStatus, StageStatus, WaitingFor};

/// Now, in milliseconds since 1970 (what JavaScript's Date.now() gives, so the page can compare).
pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as u64)
}

/// How many of the latest model calls and log lines a job keeps for the page.
pub const KEEP_CALLS: usize = 40;
pub const KEEP_LOG: usize = 300;

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum JobState {
    /// The engine is working.
    Running,
    /// The run finished: the dubbed video is ready.
    Done,
    /// The engine stopped for a person (a review, or picking Shorts); continue the run afterwards.
    Waiting,
    /// The engine reported an error, or stopped without saying how it ended.
    Failed,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Job {
    /// The run's name.
    pub run: String,
    pub state: JobState,
    /// All the stages of the run, in order (from the start event).
    pub stages: Vec<String>,
    /// The stages finished so far, including ones done in an earlier attempt.
    pub done: Vec<String>,
    /// The stage the engine is working on (or waiting in) right now.
    pub current: Option<String>,
    /// What a person needs to do, when the job is waiting.
    pub waiting_for: Option<WaitingFor>,
    /// What went wrong, when the job failed.
    pub error: Option<String>,
    /// The latest requests to the models, oldest first (at most KEEP_CALLS).
    pub calls: Vec<Call>,
    /// The engine's latest messages for people (its stderr), oldest first (at most KEEP_LOG).
    pub log: Vec<String>,
    /// When the job started and ended, and when the current stage started (ms since 1970).
    pub started_ms: u64,
    pub ended_ms: Option<u64>,
    pub stage_started_ms: Option<u64>,
    /// How long each stage finished in this job took, in seconds.
    pub timings: BTreeMap<String, f64>,
    /// How far the current stage is, if it works line by line.
    pub progress: Option<Progress>,
}

impl Job {
    /// A job that has just been started for this run; the engine's events fill in the rest.
    pub fn new(run: &str) -> Job {
        Job {
            run: run.to_string(),
            state: JobState::Running,
            stages: Vec::new(),
            done: Vec::new(),
            current: None,
            waiting_for: None,
            error: None,
            calls: Vec::new(),
            log: Vec::new(),
            started_ms: now_ms(),
            ended_ms: None,
            stage_started_ms: None,
            timings: BTreeMap::new(),
            progress: None,
        }
    }

    /// One line the engine wrote for people.
    pub fn log_line(&mut self, line: String) {
        keep_last(&mut self.log, line, KEEP_LOG);
    }

    /// Updates the job from one of the engine's events. The tests below say exactly what each
    /// event changes.
    pub fn apply(&mut self, event: Event) {
        match event {
            Event::Start { run, stages, done } => {
                self.run = run;
                self.stages = stages;
                self.done = done;
            }
            Event::Stage {
                stage,
                status: StageStatus::Started,
                ..
            } => {
                self.current = Some(stage);
                self.stage_started_ms = Some(now_ms());
                self.progress = None;
            }
            Event::Stage {
                stage,
                status: StageStatus::Waiting,
                ..
            } => {
                self.current = Some(stage);
            }
            Event::Stage {
                stage,
                status: StageStatus::Done,
                seconds,
            } => {
                if let Some(seconds) = seconds {
                    self.timings.insert(stage.clone(), seconds);
                }
                self.done.push(stage);
                self.current = None;
                self.stage_started_ms = None;
                self.progress = None;
            }
            Event::Progress(progress) => self.progress = Some(progress),
            Event::Result {
                status,
                waiting_for,
                error,
                ..
            } => {
                self.state = match status {
                    ResultStatus::Done => JobState::Done,
                    ResultStatus::Waiting => JobState::Waiting,
                    ResultStatus::Error => JobState::Failed,
                };
                self.waiting_for = waiting_for;
                self.error = error;
                self.ended_ms = Some(now_ms());
            }
            Event::Call(call) => keep_last(&mut self.calls, call, KEEP_CALLS),
            Event::Languages { .. } | Event::Models(_) | Event::ModelsTest(_) | Event::Other => {} // nothing a run's progress shows
        };
    }

    /// The job failed for a reason outside the engine's own report, e.g. it couldn't be started.
    pub fn fail(&mut self, error: String) {
        self.state = JobState::Failed;
        self.error = Some(error);
        self.ended_ms.get_or_insert_with(now_ms);
    }

    /// The engine has exited. If it never sent a result (e.g. it crashed), the job failed.
    pub fn finish(&mut self, exit_code: i32) {
        self.ended_ms.get_or_insert_with(now_ms);
        if self.state == JobState::Running {
            self.state = JobState::Failed;
            self.error = Some(format!(
                "the engine stopped without saying how it ended (exit code {exit_code})"
            ));
        }
    }
}

/// Adds `item` to the end of `list`, dropping the oldest items beyond `max`.
fn keep_last<T>(list: &mut Vec<T>, item: T, max: usize) {
    list.push(item);
    if list.len() > max {
        list.drain(..list.len() - max);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn start(stages: &[&str], done: &[&str]) -> Event {
        Event::Start {
            run: "market".into(),
            stages: stages.iter().map(|s| s.to_string()).collect(),
            done: done.iter().map(|s| s.to_string()).collect(),
        }
    }

    fn stage(name: &str, status: StageStatus) -> Event {
        Event::Stage {
            stage: name.into(),
            status,
            seconds: None,
        }
    }

    fn result(status: ResultStatus) -> Event {
        Event::Result {
            status,
            exit_code: 0,
            waiting_for: None,
            error: None,
        }
    }

    fn chat_call(n: u64) -> Event {
        Event::Call(Call {
            kind: "chat".into(),
            model: "gemma4-31b".into(),
            label: format!("translating {n}"),
            seconds: 1.0,
            tokens: Some(n),
            tok_per_s: Some(100.0),
            prompt_tokens: None,
            prompt_per_s: None,
            chars: None,
            audio_seconds: None,
        })
    }

    #[test]
    fn the_job_keeps_the_latest_model_calls_and_log_lines() {
        let mut job = Job::new("market");

        for n in 0..(KEEP_CALLS as u64 + 5) {
            job.apply(chat_call(n));
        }
        for n in 0..(KEEP_LOG + 7) {
            job.log_line(format!("line {n}"));
        }

        assert_eq!(job.calls.len(), KEEP_CALLS);
        assert_eq!(job.calls[0].tokens, Some(5), "the oldest went first");
        assert_eq!(
            job.calls.last().unwrap().tokens,
            Some(KEEP_CALLS as u64 + 4)
        );
        assert_eq!(job.log.len(), KEEP_LOG);
        assert_eq!(job.log[0], "line 7");
        assert_eq!(
            job.state,
            JobState::Running,
            "calls and log lines don't change the state"
        );
    }

    #[test]
    fn the_job_keeps_its_times_stage_timings_and_progress() {
        let mut job = Job::new("market");

        job.apply(start(&["translate", "voice"], &[]));
        job.apply(stage("translate", StageStatus::Started));
        let stage_started = job.stage_started_ms;
        job.apply(Event::Progress(Progress {
            stage: "translate".into(),
            label: Some("translating".into()),
            done: 20,
            total: 58,
            line: Some(20),
            at: Some(61.2),
        }));
        let during = job.progress.clone();
        job.apply(Event::Stage {
            stage: "translate".into(),
            status: StageStatus::Done,
            seconds: Some(47.0),
        });
        job.apply(result(ResultStatus::Done));

        assert!(job.started_ms > 0 && stage_started.is_some_and(|t| t >= job.started_ms));
        assert_eq!(during.map(|p| (p.done, p.at)), Some((20, Some(61.2))));
        assert_eq!(job.progress, None, "a finished stage has no progress");
        assert_eq!(job.timings.get("translate"), Some(&47.0));
        assert!(job.ended_ms.is_some_and(|end| end >= job.started_ms));
    }

    #[test]
    fn a_new_job_is_running() {
        let job = Job::new("market");

        assert_eq!(job.run, "market");
        assert_eq!(job.state, JobState::Running);
    }

    #[test]
    fn while_running_it_knows_the_current_stage() {
        let mut job = Job::new("market");

        job.apply(start(&["prep", "separate", "mix"], &[]));
        job.apply(stage("prep", StageStatus::Started));

        assert_eq!(job.stages, vec!["prep", "separate", "mix"]);
        assert_eq!(job.current, Some("prep".to_string()));
        assert!(job.done.is_empty());
        assert_eq!(job.state, JobState::Running);
    }

    #[test]
    fn a_finished_stage_is_added_to_done() {
        let mut job = Job::new("market");

        job.apply(start(&["prep", "separate"], &[]));
        job.apply(stage("prep", StageStatus::Started));
        job.apply(stage("prep", StageStatus::Done));

        assert_eq!(job.done, vec!["prep"]);
        assert_eq!(job.current, None, "between stages nothing is current");
    }

    #[test]
    fn a_resumed_run_starts_with_the_stages_done_before() {
        let mut job = Job::new("market");

        job.apply(start(&["prep", "separate", "mix"], &["prep", "separate"]));

        assert_eq!(job.done, vec!["prep", "separate"]);
    }

    #[test]
    fn a_finished_run_is_done() {
        let mut job = Job::new("market");

        job.apply(start(&["prep", "mix"], &[]));
        for name in ["prep", "mix"] {
            job.apply(stage(name, StageStatus::Started));
            job.apply(stage(name, StageStatus::Done));
        }
        job.apply(result(ResultStatus::Done));

        assert_eq!(job.state, JobState::Done);
        assert_eq!(job.done, vec!["prep", "mix"]);
        assert_eq!(job.current, None);
    }

    #[test]
    fn a_run_that_stops_for_a_review_is_waiting_and_says_what_for() {
        let mut job = Job::new("market");

        job.apply(start(&["prep", "review1", "mix"], &[]));
        job.apply(stage("prep", StageStatus::Started));
        job.apply(stage("prep", StageStatus::Done));
        job.apply(stage("review1", StageStatus::Started));
        job.apply(stage("review1", StageStatus::Waiting));
        job.apply(Event::Result {
            status: ResultStatus::Waiting,
            exit_code: 3,
            waiting_for: Some(WaitingFor {
                kind: "review1".into(),
                file: "/runs/market/review-1-transcript.txt".into(),
            }),
            error: None,
        });

        assert_eq!(job.state, JobState::Waiting);
        assert_eq!(job.current, Some("review1".to_string()));
        assert_eq!(
            job.waiting_for.as_ref().map(|w| w.kind.as_str()),
            Some("review1")
        );
    }

    #[test]
    fn a_failed_run_keeps_the_error() {
        let mut job = Job::new("market");

        job.apply(start(&["prep", "mix"], &[]));
        job.apply(stage("prep", StageStatus::Started));
        job.apply(Event::Result {
            status: ResultStatus::Error,
            exit_code: 1,
            waiting_for: None,
            error: Some("in stage 'prep': the video has no audio track".into()),
        });

        assert_eq!(job.state, JobState::Failed);
        assert_eq!(
            job.error.as_deref(),
            Some("in stage 'prep': the video has no audio track")
        );
    }

    #[test]
    fn other_events_change_nothing() {
        let mut job = Job::new("market");
        job.apply(start(&["prep"], &[]));
        let before = job.clone();

        job.apply(Event::Other);

        assert_eq!(job, before);
    }

    #[test]
    fn an_engine_that_stops_without_a_result_has_failed() {
        let mut job = Job::new("market");
        job.apply(start(&["prep"], &[]));

        job.finish(137);

        assert_eq!(job.state, JobState::Failed);
        assert!(job.error.as_deref().unwrap_or("").contains("137"));
    }

    #[test]
    fn finishing_after_a_result_keeps_that_result() {
        let mut job = Job::new("market");
        job.apply(start(&["prep"], &[]));
        job.apply(result(ResultStatus::Done));

        job.finish(0);

        assert_eq!(job.state, JobState::Done);
        assert_eq!(job.error, None);
    }
}
