//! Runs a program as a child process and hands over what it prints the moment it prints it, line
//! by line: the engine (`localdub ... --json`: each event on stdout, each message for people on
//! stderr), so the server can show progress and the engine's log while a run is going, and yt-dlp
//! for downloads.

use std::process::Stdio;

use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;

use crate::events::{Event, parse_line};

/// Runs `program` with `args` plus `--json`, calls `on_event` for every event and `on_log` for
/// every line on stderr as they arrive, and returns the exit code once the program has ended
/// (0 done, 3 waiting for a person, else an error; -1 if it was killed). Starting the program can
/// fail, e.g. when it doesn't exist.
pub async fn run_engine(
    program: &str,
    args: &[String],
    on_event: impl FnMut(Event),
    on_log: impl FnMut(String),
) -> std::io::Result<i32> {
    run_engine_with_input(program, args, None, on_event, on_log).await
}

/// The same, with `input` written to the engine's stdin (an API key: never on a command line,
/// where other users of the machine could see it in the process list).
pub async fn run_engine_with_input(
    program: &str,
    args: &[String],
    input: Option<String>,
    mut on_event: impl FnMut(Event),
    on_log: impl FnMut(String),
) -> std::io::Result<i32> {
    let mut all_args = args.to_vec();
    all_args.push("--json".to_string());
    let on_line = |line: String| {
        if let Some(event) = parse_line(&line) {
            on_event(event);
        }
    };
    run_process_with_input(program, &all_args, input, on_line, on_log).await
}

/// Runs `program` with `args`, calls `on_out` for every line on stdout and `on_err` for every
/// line on stderr as they arrive, and returns the exit code once it has ended (-1 if it was
/// killed). A line that isn't valid UTF-8 is passed on with the broken bytes replaced.
pub async fn run_process(
    program: &str,
    args: &[String],
    on_out: impl FnMut(String),
    on_err: impl FnMut(String),
) -> std::io::Result<i32> {
    run_process_with_input(program, args, None, on_out, on_err).await
}

/// The same, with `input` (one line) on its stdin; without it, stdin is empty.
pub async fn run_process_with_input(
    program: &str,
    args: &[String],
    input: Option<String>,
    mut on_out: impl FnMut(String),
    mut on_err: impl FnMut(String),
) -> std::io::Result<i32> {
    let mut child = Command::new(program)
        .args(args)
        .stdin(if input.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        // if the caller stops waiting (a timeout, a dropped request), the program is stopped too,
        // so nothing keeps running on the GPUs unseen
        .kill_on_drop(true)
        .spawn()?;
    if let (Some(input), Some(mut stdin)) = (input, child.stdin.take()) {
        // a line is far smaller than the pipe's buffer, so this can't wait on the reading below;
        // dropping `stdin` closes it, so the program sees the end of its input
        stdin.write_all(format!("{input}\n").as_bytes()).await?;
    }
    let mut out = BufReader::new(child.stdout.take().expect("stdout is piped")).split(b'\n');
    let mut err = BufReader::new(child.stderr.take().expect("stderr is piped")).split(b'\n');
    let text = |bytes: Vec<u8>| {
        let line = String::from_utf8_lossy(&bytes);
        line.strip_suffix('\r').unwrap_or(&line).to_string()
    };

    // Read both at once until both are closed: a pipe nobody reads fills up and blocks the program.
    // `select!` waits for whichever has a line first (C#: Task.WhenAny over the two reads).
    let (mut out_open, mut err_open) = (true, true);
    while out_open || err_open {
        tokio::select! {
            line = out.next_segment(), if out_open => match line? {
                Some(line) => on_out(text(line)),
                None => out_open = false,
            },
            line = err.next_segment(), if err_open => match line? {
                Some(line) => on_err(text(line)),
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
    async fn any_program_hands_over_its_lines_even_broken_ones() {
        let script = r"printf 'one\r\ntwo \377\nlast without newline'; printf 'oops\n' >&2; exit 4";
        let args = vec!["-c".to_string(), script.to_string()];
        let (mut out, mut err) = (Vec::new(), Vec::new());

        let code = run_process("sh", &args, |l| out.push(l), |l| err.push(l))
            .await
            .unwrap();

        assert_eq!(code, 4);
        assert_eq!(out, ["one", "two \u{FFFD}", "last without newline"]);
        assert_eq!(err, ["oops"]);
    }

    #[tokio::test]
    async fn a_program_that_does_not_exist_is_an_error() {
        let result = run_engine("/no/such/localdub", &[], |_| {}, |_| {}).await;

        assert_eq!(result.unwrap_err().kind(), std::io::ErrorKind::NotFound);
    }
}
