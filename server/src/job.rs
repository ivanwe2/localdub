//! The job: the one engine run the server is doing (or did last), as the browser will see it.
//! It starts as "running" and is updated from the engine's events as they arrive (runner.rs).

use serde::Serialize;

use crate::events::{Event, ResultStatus, StageStatus, WaitingFor};

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
        }
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
            Event::Stage { stage, status: StageStatus::Started | StageStatus::Waiting, .. } => {
                self.current = Some(stage);
            },
            Event::Stage { stage, status: StageStatus::Done, .. } => {
                self.done.push(stage);
                self.current = None;
            },  
            Event::Result { status, waiting_for, error, .. } => {
                self.state = match status {
                    ResultStatus::Done => JobState::Done,
                    ResultStatus::Waiting => JobState::Waiting,
                    ResultStatus::Error => JobState::Failed,
                };
                self.waiting_for = waiting_for;
                self.error = error;
            }
            Event::Other => {}
        };
    }

    /// The engine has exited. If it never sent a result (e.g. it crashed), the job failed.
    pub fn finish(&mut self, exit_code: i32) {
        if self.state == JobState::Running {
            self.state = JobState::Failed;
            self.error = Some(format!(
                "the engine stopped without saying how it ended (exit code {exit_code})"
            ));
        }
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
