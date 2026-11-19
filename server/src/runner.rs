//! Runs the engine (`localdub ... --json`) as a child process and hands over what it prints the
//! moment it prints it: each event (stdout) and each message for people (stderr), so the server can
//! show progress and the engine's log while a run is going.

use std::process::Stdio;

use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;

use crate::events::{Event, parse_line};

/// Runs `program` with `args` plus `--json`, calls `on_event` for every event and `on_log` for
/// every line on stderr as they arrive, and returns the exit code once the program has ended
/// (0 done, 3 waiting for a person, else an error; -1 if it was killed). Starting the program can
/// fail, e.g. when it doesn't exist.
pub async fn run_engine(
    program: &str,
    args: &[String],
    mut on_event: impl FnMut(Event),
    mut on_log: impl FnMut(String),
) -> std::io::Result<i32> {
    let mut child = Command::new(program)
        .args(args)
        .arg("--json")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let mut out = BufReader::new(child.stdout.take().expect("stdout is piped")).lines();
    let mut err = BufReader::new(child.stderr.take().expect("stderr is piped")).lines();

    // Read both at once until both are closed: a pipe nobody reads fills up and blocks the engine.
    // `select!` waits for whichever has a line first (C#: Task.WhenAny over the two reads).
    let (mut out_open, mut err_open) = (true, true);
    while out_open || err_open {
        tokio::select! {
            line = out.next_line(), if out_open => match line? {
                Some(line) => {
                    if let Some(event) = parse_line(&line) {
                        on_event(event);
                    }
                }
                None => out_open = false,
            },
            line = err.next_line(), if err_open => match line? {
                Some(line) => on_log(line),
                None => err_open = false,
            },
        }
    }

    let status = child.wait().await?;
    Ok(status.code().unwrap_or(-1))
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{ResultStatus, StageStatus};

    /// The fake engine (testdata/fake-localdub.sh), run through `sh` so it needs no execute bit.
    const FAKE: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/testdata/fake-localdub.sh");

    /// Runs the fake engine for this run and collects everything it reports.
    async fn run_fake(run: &str) -> (i32, Vec<Event>) {
        let (code, events, _) = run_fake_with_log(run).await;
        (code, events)
    }

    /// The same, with the lines it wrote to stderr.
    async fn run_fake_with_log(run: &str) -> (i32, Vec<Event>, Vec<String>) {
        let args = vec![FAKE.to_string(), "run".to_string(), run.to_string()];
        let (mut events, mut log) = (Vec::new(), Vec::new());
        let code = run_engine("sh", &args, |e| events.push(e), |l| log.push(l))
            .await
            .unwrap();
        (code, events, log)
    }

    #[tokio::test]
    async fn the_engines_messages_for_people_are_handed_over_too() {
        let (_, _, log) = run_fake_with_log("done-run").await;

        assert_eq!(log, ["fake-localdub: messages for people go to stderr"]);
    }

    #[tokio::test]
    async fn a_finished_run_reports_every_event_in_order() {
        let (code, events) = run_fake("done-run").await;

        assert_eq!(code, 0);
        assert_eq!(
            events.len(),
            6,
            "6 events; the line that isn't one is skipped: {events:?}"
        );
        assert_eq!(
            events[0],
            Event::Start {
                run: "done-run".into(),
                stages: vec!["prep".into(), "mix".into()],
                done: vec![],
            }
        );
        assert_eq!(
            events[2],
            Event::Stage {
                stage: "prep".into(),
                status: StageStatus::Done,
                seconds: Some(0.5),
            }
        );
        assert_eq!(
            events[5],
            Event::Result {
                status: ResultStatus::Done,
                exit_code: 0,
                waiting_for: None,
                error: None,
            }
        );
    }

    #[tokio::test]
    async fn a_run_that_stops_for_a_review_exits_with_3() {
        let (code, events) = run_fake("review-run").await;

        assert_eq!(code, 3);
        match events.last() {
            Some(Event::Result {
                status: ResultStatus::Waiting,
                waiting_for: Some(waiting),
                ..
            }) => assert_eq!(waiting.kind, "review1"),
            other => panic!("expected a waiting result last, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn a_failing_run_reports_its_error() {
        let (code, events) = run_fake("failing-run").await;

        assert_eq!(code, 1);
        assert!(
            matches!(
                events.last(),
                Some(Event::Result {
                    status: ResultStatus::Error,
                    error: Some(_),
                    ..
                })
            ),
            "expected an error result last, got {events:?}"
        );
    }

    #[tokio::test]
    async fn a_program_that_does_not_exist_is_an_error() {
        let result = run_engine("/no/such/localdub", &[], |_| {}, |_| {}).await;

        assert_eq!(result.unwrap_err().kind(), std::io::ErrorKind::NotFound);
    }
}
