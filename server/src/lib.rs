//! The localdub server as a library: the routes and everything behind them. `main.rs` only reads
//! the settings and starts it (like an ASP.NET class library and a small host project). Everything
//! `pub` here is the library's API, so a finished piece doesn't count as "never used" before a
//! route uses it.

pub mod events;
pub mod job;
pub mod runner;
pub mod runs;
pub mod start;

use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::{StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::IntoResponse;
use axum::response::sse::{self, KeepAlive, Sse};
use axum::routing::get;
use axum::{Json, Router};
use tokio::sync::watch;
use tokio_stream::wrappers::WatchStream;
use tokio_stream::{Stream, StreamExt};

use crate::job::{Job, JobState};
use crate::start::{StartRequest, engine_args};

#[derive(serde::Serialize)]
struct Health {
    ok: bool,
    version: &'static str,
}

/// What every handler can ask for. Cloning it is cheap: the shared parts sit behind `Arc`, a
/// reference-counted pointer, so every clone points at the same engine command and the same job.
#[derive(Clone)]
pub struct AppState {
    /// Where the channels and runs live (LOCALDUB_HOME, else ~/dub).
    pub home: PathBuf,
    /// The engine's command: the program and any arguments that come before ours, e.g. ["localdub"].
    pub engine: Arc<Vec<String>>,
    /// The current (or last) job. A watch channel holds one value and tells every subscriber when
    /// it changes; that is what the live progress stream (GET /job/events) listens to.
    pub job: Arc<watch::Sender<Option<Job>>>,
}

impl AppState {
    pub fn new(home: PathBuf, engine: Vec<String>) -> AppState {
        AppState {
            home,
            engine: Arc::new(engine),
            job: Arc::new(watch::Sender::new(None)),
        }
    }
}

async fn health() -> Json<Health> {
    Json(Health {
        ok: true,
        version: env!("CARGO_PKG_VERSION"),
    })
}

/// GET /runs: every run on disk, newest first.
async fn get_runs(
    State(state): State<AppState>,
) -> Result<Json<Vec<runs::RunSummary>>, (StatusCode, String)> {
    let list = runs::list_runs(&state.home)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok(Json(list))
}

/// POST /runs: starts the engine for this run in the background and answers at once
/// (202 Accepted) with the new job. One run at a time: while one is running, 409 Conflict.
async fn start_run(
    State(state): State<AppState>,
    Json(req): Json<StartRequest>,
) -> Result<(StatusCode, Json<Job>), (StatusCode, String)> {
    req.validate()
        .map_err(|e| (StatusCode::UNPROCESSABLE_ENTITY, e))?;
    let Some((program, before)) = state.engine.split_first() else {
        let msg = "no engine command configured (LOCALDUB_ENGINE)".to_string();
        return Err((StatusCode::INTERNAL_SERVER_ERROR, msg));
    };

    // Check and claim in one step, inside the channel's lock, so two requests can't both start.
    let mut busy_with = None;
    state.job.send_if_modified(|current| match current {
        Some(job) if job.state == JobState::Running => {
            busy_with = Some(job.run.clone());
            false // nothing changed, nobody is told
        }
        _ => {
            *current = Some(Job::new(&req.name));
            true
        }
    });
    if let Some(run) = busy_with {
        let msg = format!("'{run}' is still running; one run at a time");
        return Err((StatusCode::CONFLICT, msg));
    }

    // The background task gets its own copies: it outlives this request.
    let program = program.clone();
    let mut args = before.to_vec();
    args.extend(engine_args(&req));
    let job = state.job.clone();
    tokio::spawn(async move {
        let outcome = runner::run_engine(&program, &args, |event| {
            job.send_modify(|current| {
                if let Some(job) = current {
                    job.apply(event);
                }
            });
        })
        .await;
        job.send_modify(|current| {
            if let Some(job) = current {
                match outcome {
                    Ok(code) => job.finish(code),
                    Err(e) => job.fail(format!("could not start the engine ({program}): {e}")),
                }
            }
        });
    });

    let job = state.job.borrow().clone().expect("the job was set above");
    Ok((StatusCode::ACCEPTED, Json(job)))
}

/// GET /job: the current (or last) job; null if nothing has run since the server started.
async fn get_job(State(state): State<AppState>) -> Json<Option<Job>> {
    Json(state.job.borrow().clone())
}

/// GET /job/events: the job as Server-Sent Events, its state right away and again every time it
/// changes, for as long as the browser stays connected (`new EventSource("/job/events")`).
async fn job_events(
    State(state): State<AppState>,
) -> Sse<impl Stream<Item = Result<sse::Event, axum::Error>>> {
    let updates =
        WatchStream::new(state.job.subscribe()).map(|job| sse::Event::default().json_data(job));
    Sse::new(updates).keep_alive(KeepAlive::default())
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/runs", get(get_runs).post(start_run))
        .route("/job", get(get_job))
        .route("/job/events", get(job_events))
        .with_state(state)
}

/// The Host header values the server answers to when it listens on `listen` (e.g.
/// "127.0.0.1:8080"): localhost, 127.0.0.1 and [::1] on that port, and the listen address itself.
pub fn default_hosts(listen: &str) -> Vec<String> {
    let port = listen.rsplit(':').next().unwrap_or("8080");
    let mut hosts = vec![
        format!("localhost:{port}"),
        format!("127.0.0.1:{port}"),
        format!("[::1]:{port}"),
    ];
    if !hosts.iter().any(|h| h == listen) {
        hosts.push(listen.to_string());
    }
    hosts
}

/// Answers only requests addressed to one of `hosts` (the Host header, any letter case). That stops
/// DNS rebinding: a web page from another site that points its own domain at this machine still
/// sends its own domain as the Host. Requests without a Host header don't come from a browser and
/// pass. (Like ASP.NET Core's AllowedHosts / HostFiltering.)
pub fn allow_hosts(router: Router, hosts: Vec<String>) -> Router {
    let hosts = Arc::new(hosts);
    router.layer(middleware::from_fn(move |req: Request, next: Next| {
        let hosts = hosts.clone();
        async move {
            let refused = req
                .headers()
                .get(header::HOST)
                .map(|h| String::from_utf8_lossy(h.as_bytes()).into_owned())
                .filter(|host| !hosts.iter().any(|h| h.eq_ignore_ascii_case(host)));
            match refused {
                Some(host) => {
                    let msg = format!(
                        "this server doesn't answer to host {host:?} (see LOCALDUB_ALLOWED_HOSTS)"
                    );
                    (StatusCode::FORBIDDEN, msg).into_response()
                }
                None => next.run(req).await,
            }
        }
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;
    use std::time::Duration;

    use axum::body::Body;
    use axum::http::Request;
    use http_body_util::BodyExt; // .collect() and .frame() on a response body
    use serde_json::{Value, json};
    use tower::ServiceExt; // .oneshot() on a Router

    /// A stand-in for localdub that prints a run's events without doing any work.
    const FAKE_ENGINE: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/testdata/fake-localdub.sh");

    /// The app's state for a test: runs in `home`, and the fake engine instead of localdub.
    fn test_state(home: &Path) -> AppState {
        AppState::new(home.to_path_buf(), vec!["sh".into(), FAKE_ENGINE.into()])
    }

    /// Sends a request through the whole app, as a browser would but without a network (like
    /// ASP.NET's WebApplicationFactory); returns the status and the body as JSON (Null if it isn't).
    async fn send(app: Router, request: Request<Body>) -> (StatusCode, Value) {
        let response = app.oneshot(request).await.unwrap();
        let status = response.status();
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        (
            status,
            serde_json::from_slice(&bytes).unwrap_or(Value::Null),
        )
    }

    async fn get_json(app: Router, path: &str) -> (StatusCode, Value) {
        send(app, Request::get(path).body(Body::empty()).unwrap()).await
    }

    async fn post_json(app: Router, path: &str, body: Value) -> (StatusCode, Value) {
        let request = Request::post(path)
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap();
        send(app, request).await
    }

    /// The body for POST /runs. The fake engine picks its story from the video argument.
    /// The body for POST /runs, with a (fake) video file named after the story: the fake engine
    /// picks what it prints from the video's file name.
    fn start_body(state: &AppState, story: &str) -> Value {
        let videos = state.home.join("videos");
        std::fs::create_dir_all(&videos).unwrap();
        let video = videos.join(story);
        std::fs::write(&video, "not really a video").unwrap();
        json!({"video": video, "channel": "c", "name": story})
    }

    /// Asks for the job until it isn't running any more (the fake engine needs milliseconds).
    async fn job_when_settled(state: &AppState) -> Value {
        for _ in 0..250 {
            let (_, job) = get_json(app(state.clone()), "/job").await;
            if job["state"] != "running" {
                return job;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        panic!("the job was still running after 5 s");
    }

    #[test]
    fn the_default_hosts_are_this_machine_on_the_listen_port() {
        assert_eq!(
            default_hosts("127.0.0.1:8080"),
            ["localhost:8080", "127.0.0.1:8080", "[::1]:8080"]
        );
        assert!(default_hosts("0.0.0.0:9000").contains(&"0.0.0.0:9000".to_string()));
    }

    /// GET /health with this Host header (or none), through the host check.
    async fn status_with_host(host: Option<&str>) -> StatusCode {
        let home = tempfile::tempdir().unwrap();
        let app = allow_hosts(
            app(test_state(home.path())),
            default_hosts("127.0.0.1:8080"),
        );
        let mut request = Request::get("/health");
        if let Some(host) = host {
            request = request.header("host", host);
        }
        let response = app.oneshot(request.body(Body::empty()).unwrap()).await;
        response.unwrap().status()
    }

    #[tokio::test]
    async fn requests_addressed_to_another_host_are_refused() {
        assert_eq!(
            status_with_host(Some("localhost:8080")).await,
            StatusCode::OK
        );
        assert_eq!(
            status_with_host(Some("LocalHost:8080")).await,
            StatusCode::OK
        );
        assert_eq!(status_with_host(None).await, StatusCode::OK);
        assert_eq!(
            status_with_host(Some("evil.example:8080")).await,
            StatusCode::FORBIDDEN
        );
        assert_eq!(
            status_with_host(Some("localhost:9999")).await,
            StatusCode::FORBIDDEN
        );
    }

    #[tokio::test]
    async fn test_health() {
        let Json(h) = health().await;
        assert!(h.ok);
        assert_eq!(h.version, env!("CARGO_PKG_VERSION"));
    }

    #[tokio::test]
    async fn get_runs_lists_the_runs() {
        let home = tempfile::tempdir().unwrap();
        let run = home.path().join("runs").join("market");
        std::fs::create_dir_all(&run).unwrap();
        std::fs::write(
            run.join("state.json"),
            r#"{"source": "/videos/market.mp4", "channel": "mychannel", "created": "2026-11-10T09:00:00",
                "done": ["prep"], "timings": {"prep": 0.4}, "seconds": 170.0}"#,
        )
        .unwrap();

        let (status, json) = get_json(app(test_state(home.path())), "/runs").await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(json[0]["name"], "market");
        assert_eq!(json[0]["seconds"], 170.0);
        assert_eq!(json[0]["finished"], false);
    }

    #[tokio::test]
    async fn get_runs_reports_a_broken_runs_folder_as_a_server_error() {
        let home = tempfile::tempdir().unwrap();
        std::fs::write(
            home.path().join("runs"),
            "a file where the runs folder should be",
        )
        .unwrap();

        let (status, _) = get_json(app(test_state(home.path())), "/runs").await;

        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    }

    #[tokio::test]
    async fn there_is_no_job_before_the_first_run() {
        let home = tempfile::tempdir().unwrap();

        let (status, job) = get_json(app(test_state(home.path())), "/job").await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(job, Value::Null);
    }

    #[tokio::test]
    async fn starting_a_run_answers_at_once_and_the_job_follows_the_engine() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());

        let (status, job) =
            post_json(app(state.clone()), "/runs", start_body(&state, "done-run")).await;

        assert_eq!(status, StatusCode::ACCEPTED);
        assert_eq!(job["run"], "done-run");
        assert_eq!(job["state"], "running");
        let job = job_when_settled(&state).await;
        assert_eq!(job["state"], "done");
        assert_eq!(job["done"], json!(["prep", "mix"]));
        assert_eq!(job["current"], Value::Null);
    }

    #[tokio::test]
    async fn a_run_that_stops_for_a_review_is_waiting() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());

        post_json(
            app(state.clone()),
            "/runs",
            start_body(&state, "review-run"),
        )
        .await;

        let job = job_when_settled(&state).await;
        assert_eq!(job["state"], "waiting");
        assert_eq!(job["waiting_for"]["kind"], "review1");
    }

    #[tokio::test]
    async fn a_failing_run_shows_the_engines_error() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());

        post_json(
            app(state.clone()),
            "/runs",
            start_body(&state, "failing-run"),
        )
        .await;

        let job = job_when_settled(&state).await;
        assert_eq!(job["state"], "failed");
        assert!(job["error"].as_str().unwrap().contains("no audio track"));
    }

    #[tokio::test]
    async fn only_one_run_at_a_time() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());

        let (first, _) =
            post_json(app(state.clone()), "/runs", start_body(&state, "slow-run")).await;
        let (second, _) =
            post_json(app(state.clone()), "/runs", start_body(&state, "done-run")).await;

        assert_eq!(first, StatusCode::ACCEPTED);
        assert_eq!(second, StatusCode::CONFLICT);
        let job = job_when_settled(&state).await;
        assert_eq!(job["run"], "slow-run");
        assert_eq!(job["state"], "done");
    }

    #[tokio::test]
    async fn a_start_request_without_a_name_is_refused() {
        let home = tempfile::tempdir().unwrap();
        let body = json!({"video": "done-run", "channel": "c"});

        let (status, _) = post_json(app(test_state(home.path())), "/runs", body).await;

        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
    }

    #[tokio::test]
    async fn an_unsafe_name_is_refused_before_anything_starts() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());
        let mut body = start_body(&state, "done-run");
        body["name"] = json!("../../escape");

        let (status, message) = send(
            app(state.clone()),
            Request::post("/runs")
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await;

        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(message, Value::Null, "the reason comes back as plain text");
        let (_, job) = get_json(app(state), "/job").await;
        assert_eq!(job, Value::Null, "no job was started");
    }

    #[tokio::test]
    async fn an_engine_that_cannot_be_started_fails_the_job() {
        let home = tempfile::tempdir().unwrap();
        let state = AppState::new(home.path().to_path_buf(), vec!["/no/such/localdub".into()]);

        post_json(app(state.clone()), "/runs", start_body(&state, "done-run")).await;

        let job = job_when_settled(&state).await;
        assert_eq!(job["state"], "failed");
        assert!(
            job["error"]
                .as_str()
                .unwrap()
                .contains("could not start the engine")
        );
    }

    #[tokio::test]
    async fn job_events_streams_the_job_as_it_changes() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());
        let request = Request::get("/job/events").body(Body::empty()).unwrap();
        let response = app(state.clone()).oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()["content-type"], "text/event-stream");

        post_json(
            app(state.clone()),
            "/runs",
            start_body(&state, "review-run"),
        )
        .await;

        // Read the stream until the job shows up as waiting for its review.
        let mut body = response.into_body();
        let mut seen = String::new();
        let found = tokio::time::timeout(Duration::from_secs(5), async {
            while let Some(Ok(frame)) = body.frame().await {
                if let Some(data) = frame.data_ref() {
                    seen.push_str(&String::from_utf8_lossy(data));
                }
                if seen.contains(r#""state":"waiting""#) {
                    return true;
                }
            }
            false
        })
        .await;
        assert!(matches!(found, Ok(true)), "events so far:\n{seen}");
    }
}
