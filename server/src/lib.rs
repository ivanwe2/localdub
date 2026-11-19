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

use axum::body::Body;
use axum::extract::{DefaultBodyLimit, Path as UrlPath, Request, State};
use axum::http::{StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::IntoResponse;
use axum::response::sse::{self, KeepAlive, Sse};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use serde::Serialize;
use tokio::io::AsyncWriteExt;
use tokio::sync::watch;
use tokio_stream::wrappers::WatchStream;
use tokio_stream::{Stream, StreamExt};
use tower_http::services::{ServeDir, ServeFile};

use crate::job::{Job, JobState};
use crate::runs::{RunSummary, Waiting};
use crate::start::{ShortsRequest, StartRequest};

/// An error answer: a status code and a plain-text reason the page can show as it is.
type Failure = (StatusCode, String);

fn server_error(e: impl std::fmt::Display) -> Failure {
    (StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
}

#[derive(Serialize)]
struct Health {
    ok: bool,
    version: &'static str,
}

/// What every handler can ask for. Cloning it is cheap: the shared parts sit behind `Arc`, a
/// reference-counted pointer, so every clone points at the same engine command and the same job.
#[derive(Clone)]
pub struct AppState {
    /// Where the channels, runs and uploaded videos live (LOCALDUB_HOME, else ~/dub).
    pub home: PathBuf,
    /// The engine's command: the program and any arguments that come before ours, e.g. ["localdub"].
    pub engine: Arc<Vec<String>>,
    /// The current (or last) job. A watch channel holds one value and tells every subscriber when
    /// it changes; that is what the live progress stream (GET /api/job/events) listens to.
    pub job: Arc<watch::Sender<Option<Job>>>,
    /// The built web UI (web/dist), served at `/`; None serves the API only.
    pub web: Option<PathBuf>,
}

impl AppState {
    pub fn new(home: PathBuf, engine: Vec<String>) -> AppState {
        AppState {
            home,
            engine: Arc::new(engine),
            job: Arc::new(watch::Sender::new(None)),
            web: None,
        }
    }

    /// Also serve the web UI from this folder.
    pub fn with_web(mut self, dir: PathBuf) -> AppState {
        self.web = Some(dir);
        self
    }

    fn videos(&self) -> PathBuf {
        self.home.join("videos")
    }
}

async fn health() -> Json<Health> {
    Json(Health {
        ok: true,
        version: env!("CARGO_PKG_VERSION"),
    })
}

/// Starts the engine with `args` for the run `run`, in the background, and returns the new job at
/// once. One job at a time: while one is running, 409 Conflict. Every endpoint that starts the
/// engine (a new run, continuing one, Shorts) goes through here.
fn launch(state: &AppState, run: &str, args: Vec<String>) -> Result<Job, Failure> {
    let Some((program, before)) = state.engine.split_first() else {
        return Err(server_error(
            "no engine command configured (LOCALDUB_ENGINE)",
        ));
    };

    // Check and claim in one step, inside the channel's lock, so two requests can't both start.
    let mut busy_with = None;
    state.job.send_if_modified(|current| match current {
        Some(job) if job.state == JobState::Running => {
            busy_with = Some(job.run.clone());
            false // nothing changed, nobody is told
        }
        _ => {
            *current = Some(Job::new(run));
            true
        }
    });
    if let Some(busy) = busy_with {
        let msg = format!("'{busy}' is still running; one run at a time");
        return Err((StatusCode::CONFLICT, msg));
    }

    // The background task gets its own copies: it outlives this request.
    let program = program.clone();
    let mut all_args = before.to_vec();
    all_args.extend(args);
    let job = state.job.clone();
    tokio::spawn(async move {
        let outcome = runner::run_engine(&program, &all_args, |event| {
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

    Ok(state.job.borrow().clone().expect("the job was set above"))
}

/// The run named in the URL, or 404 Not Found.
async fn find_run(state: &AppState, name: &str) -> Result<RunSummary, Failure> {
    let not_found = || (StatusCode::NOT_FOUND, format!("no run called {name:?}"));
    if !start::is_safe_name(name) {
        return Err(not_found());
    }
    runs::read_run(&state.home, name)
        .await
        .map_err(server_error)?
        .ok_or_else(not_found)
}

/// GET /api/runs: every run on disk, newest first.
async fn get_runs(State(state): State<AppState>) -> Result<Json<Vec<RunSummary>>, Failure> {
    let list = runs::list_runs(&state.home).await.map_err(server_error)?;
    Ok(Json(list))
}

/// POST /api/runs: starts a new run (202 Accepted, with the job).
async fn start_run(
    State(state): State<AppState>,
    Json(req): Json<StartRequest>,
) -> Result<(StatusCode, Json<Job>), Failure> {
    req.validate(&state.home)
        .map_err(|e| (StatusCode::UNPROCESSABLE_ENTITY, e))?;
    let job = launch(&state, &req.name, start::engine_args(&req))?;
    Ok((StatusCode::ACCEPTED, Json(job)))
}

/// POST /api/runs/{name}/continue: goes on where the run stopped: after a review or an error
/// (`localdub run <name>`), or renders the picked Shorts (`localdub shorts <name> --lang ..`).
async fn continue_run(
    State(state): State<AppState>,
    UrlPath(name): UrlPath<String>,
) -> Result<(StatusCode, Json<Job>), Failure> {
    let run = find_run(&state, &name).await?;
    let args = match &run.waiting_for {
        Some(Waiting {
            kind,
            lang: Some(lang),
            ..
        }) if kind == "shorts" => start::shorts_args(&name, lang),
        _ if !run.finished => start::continue_args(&name),
        _ => {
            let msg = format!("'{name}' is finished and waits for nothing");
            return Err((StatusCode::CONFLICT, msg));
        }
    };
    Ok((StatusCode::ACCEPTED, Json(launch(&state, &name, args)?)))
}

/// POST /api/runs/{name}/shorts `{"lang": "dub"}`: proposes Shorts for a finished run; the run then
/// waits for you to pick them (its review is the candidates list), and continuing renders them.
async fn make_shorts(
    State(state): State<AppState>,
    UrlPath(name): UrlPath<String>,
    Json(req): Json<ShortsRequest>,
) -> Result<(StatusCode, Json<Job>), Failure> {
    let run = find_run(&state, &name).await?;
    if !run.finished {
        let msg = format!("'{name}' isn't dubbed yet; Shorts come after the dub");
        return Err((StatusCode::CONFLICT, msg));
    }
    let job = launch(&state, &name, start::shorts_args(&name, req.lang.as_str()))?;
    Ok((StatusCode::ACCEPTED, Json(job)))
}

/// What a run waits for, with the text of the file to edit.
#[derive(Serialize)]
struct Review {
    kind: String,
    lang: Option<String>,
    file: String,
    text: String,
}

/// The run's review file: the one it waits for, or 404 if it waits for nothing.
async fn review_of(state: &AppState, name: &str) -> Result<(Waiting, PathBuf), Failure> {
    let run = find_run(state, name).await?;
    let Some(waiting) = run.waiting_for else {
        let msg = format!("'{name}' waits for no review");
        return Err((StatusCode::NOT_FOUND, msg));
    };
    let path = state.home.join("runs").join(name).join(&waiting.file);
    Ok((waiting, path))
}

/// GET /api/runs/{name}/review: the file the run waits for, to edit in the page.
async fn get_review(
    State(state): State<AppState>,
    UrlPath(name): UrlPath<String>,
) -> Result<Json<Review>, Failure> {
    let (waiting, path) = review_of(&state, &name).await?;
    let text = tokio::fs::read_to_string(&path)
        .await
        .map_err(server_error)?;
    Ok(Json(Review {
        kind: waiting.kind,
        lang: waiting.lang,
        file: waiting.file,
        text,
    }))
}

/// PUT /api/runs/{name}/review, the body is the file's new text: saves it (204 No Content). It is
/// written to a temporary file first and then renamed, so the engine never reads half a file.
async fn put_review(
    State(state): State<AppState>,
    UrlPath(name): UrlPath<String>,
    text: String,
) -> Result<StatusCode, Failure> {
    let (_, path) = review_of(&state, &name).await?;
    let tmp = path.with_extension("txt.tmp");
    tokio::fs::write(&tmp, text).await.map_err(server_error)?;
    tokio::fs::rename(&tmp, &path).await.map_err(server_error)?;
    Ok(StatusCode::NO_CONTENT)
}

/// A video in the uploads folder.
#[derive(Serialize)]
struct Video {
    name: String,
    /// The path to give POST /api/runs as `video`.
    path: String,
    bytes: u64,
}

/// GET /api/videos: the uploaded videos, by name.
async fn list_videos(State(state): State<AppState>) -> Result<Json<Vec<Video>>, Failure> {
    let mut entries = match tokio::fs::read_dir(state.videos()).await {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Json(Vec::new())),
        Err(e) => return Err(server_error(e)),
    };
    let mut videos = Vec::new();
    while let Some(entry) = entries.next_entry().await.map_err(server_error)? {
        let name = entry.file_name().to_string_lossy().into_owned();
        let meta = entry.metadata().await.map_err(server_error)?;
        if meta.is_file() && !name.starts_with('.') {
            let path = entry.path().to_string_lossy().into_owned();
            videos.push(Video {
                name,
                path,
                bytes: meta.len(),
            });
        }
    }
    videos.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(Json(videos))
}

/// PUT /api/videos/{file}, the body is the video: stores it in the uploads folder, streamed to
/// disk piece by piece so even a big video never sits in memory (201 Created). An existing video
/// isn't replaced (409); an interrupted upload leaves nothing behind.
async fn upload_video(
    State(state): State<AppState>,
    UrlPath(file): UrlPath<String>,
    body: Body,
) -> Result<(StatusCode, Json<Video>), Failure> {
    if !start::is_safe_file_name(&file) {
        let msg = format!("file name {file:?}: {}", start::NAME_RULE);
        return Err((StatusCode::UNPROCESSABLE_ENTITY, msg));
    }
    let dir = state.videos();
    tokio::fs::create_dir_all(&dir)
        .await
        .map_err(server_error)?;
    let dest = dir.join(&file);
    if tokio::fs::try_exists(&dest).await.unwrap_or(false) {
        let msg = format!("{file} is already uploaded");
        return Err((StatusCode::CONFLICT, msg));
    }

    let part = dir.join(format!(".{file}.part")); // hidden until complete
    let mut out = tokio::fs::File::create(&part).await.map_err(server_error)?;
    let mut bytes = 0u64;
    let mut stream = body.into_data_stream();
    while let Some(chunk) = stream.next().await {
        let written = match chunk {
            Ok(chunk) => out.write_all(&chunk).await.map(|_| chunk.len()),
            Err(e) => Err(std::io::Error::other(e)),
        };
        match written {
            Ok(n) => bytes += n as u64,
            Err(e) => {
                drop(out);
                let _ = tokio::fs::remove_file(&part).await;
                let msg = format!("the upload of {file} broke off: {e}");
                return Err((StatusCode::BAD_REQUEST, msg));
            }
        }
    }
    out.flush().await.map_err(server_error)?;
    drop(out);
    tokio::fs::rename(&part, &dest)
        .await
        .map_err(server_error)?;

    let path = dest.to_string_lossy().into_owned();
    let video = Video {
        name: file,
        path,
        bytes,
    };
    Ok((StatusCode::CREATED, Json(video)))
}

/// GET /api/job: the current (or last) job; null if nothing has run since the server started.
async fn get_job(State(state): State<AppState>) -> Json<Option<Job>> {
    Json(state.job.borrow().clone())
}

/// GET /api/job/events: the job as Server-Sent Events, its state right away and again every time it
/// changes, for as long as the browser stays connected (`new EventSource("/api/job/events")`).
async fn job_events(
    State(state): State<AppState>,
) -> Sse<impl Stream<Item = Result<sse::Event, axum::Error>>> {
    let updates =
        WatchStream::new(state.job.subscribe()).map(|job| sse::Event::default().json_data(job));
    Sse::new(updates).keep_alive(KeepAlive::default())
}

/// The whole server:
/// - `/api/...` the JSON API: health, runs (start, continue, review, Shorts), uploads, the job
/// - `/files/<run>/<path>` a run's files, e.g. the dubbed video (ServeDir also answers range
///   requests, so the browser can seek in a video, and never serves anything outside runs/)
/// - everything else: the web UI, with index.html for any path it doesn't have
pub fn app(state: AppState) -> Router {
    let api = Router::new()
        .route("/health", get(health))
        .route("/runs", get(get_runs).post(start_run))
        .route("/runs/{name}/continue", post(continue_run))
        .route("/runs/{name}/shorts", post(make_shorts))
        .route("/runs/{name}/review", get(get_review).put(put_review))
        .route("/videos", get(list_videos))
        .route(
            "/videos/{file}",
            put(upload_video).layer(DefaultBodyLimit::disable()), // videos are big
        )
        .route("/job", get(get_job))
        .route("/job/events", get(job_events));
    let mut router = Router::new()
        .nest("/api", api)
        .nest_service("/files", ServeDir::new(state.home.join("runs")));
    if let Some(web) = &state.web {
        let index = ServeFile::new(web.join("index.html"));
        router = router.fallback_service(ServeDir::new(web).fallback(index));
    }
    router.with_state(state)
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

/// Guards the server against other web sites open in the same browser:
/// - **Host**: answers only requests addressed to one of `hosts` (any letter case). That stops DNS
///   rebinding: a page from another site that points its own domain at this machine still sends
///   its own domain as the Host. (Like ASP.NET Core's AllowedHosts / HostFiltering.)
/// - **Origin**: anything that changes something (POST, PUT, …) must come from one of those hosts
///   too, or the browser's `Sec-Fetch-Site` must not say "cross-site". That stops another site's
///   page from making the browser send, say, a form to POST …/continue (cross-site request forgery).
///
/// Requests without these headers don't come from a browser (curl, scripts, the tests) and pass.
pub fn allow_hosts(router: Router, hosts: Vec<String>) -> Router {
    let hosts = Arc::new(hosts);
    router.layer(middleware::from_fn(move |req: Request, next: Next| {
        let hosts = hosts.clone();
        async move {
            match refusal(&req, &hosts) {
                Some(msg) => (StatusCode::FORBIDDEN, msg).into_response(),
                None => next.run(req).await,
            }
        }
    }))
}

/// Why `allow_hosts` refuses this request, if it does.
fn refusal(req: &Request, hosts: &[String]) -> Option<String> {
    let header = |name| {
        req.headers()
            .get(name)
            .map(|v| String::from_utf8_lossy(v.as_bytes()).into_owned())
    };
    let known = |host: &str| hosts.iter().any(|h| h.eq_ignore_ascii_case(host));

    if let Some(host) = header(header::HOST).filter(|host| !known(host)) {
        return Some(format!(
            "this server doesn't answer to host {host:?} (see LOCALDUB_ALLOWED_HOSTS)"
        ));
    }
    if req.method().is_safe() {
        return None; // GET, HEAD, OPTIONS change nothing
    }
    if let Some(origin) = header(header::ORIGIN) {
        let host = origin.split_once("://").map_or("", |(_, rest)| rest);
        if !known(host) {
            return Some(format!(
                "requests from {origin} may not change anything here (see LOCALDUB_ALLOWED_HOSTS)"
            ));
        }
    }
    if header(header::HeaderName::from_static("sec-fetch-site")).as_deref() == Some("cross-site") {
        return Some("requests from other sites may not change anything here".to_string());
    }
    None
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
            let (_, job) = get_json(app(state.clone()), "/api/job").await;
            if job["state"] != "running" {
                return job;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        panic!("the job was still running after 5 s");
    }

    /// GET `path` (with any extra headers) and return the status and the body as text.
    async fn get_text(app: Router, path: &str, headers: &[(&str, &str)]) -> (StatusCode, String) {
        let mut request = Request::get(path);
        for (name, value) in headers {
            request = request.header(*name, *value);
        }
        let response = app
            .oneshot(request.body(Body::empty()).unwrap())
            .await
            .unwrap();
        let status = response.status();
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        (status, String::from_utf8_lossy(&bytes).into_owned())
    }

    #[tokio::test]
    async fn a_runs_files_are_served_also_in_ranges() {
        let home = tempfile::tempdir().unwrap();
        let run = home.path().join("runs").join("market");
        std::fs::create_dir_all(&run).unwrap();
        std::fs::write(
            run.join("market.en.srt"),
            "1\n00:00:00,000 --> 00:00:01,000\nHi\n",
        )
        .unwrap();
        let app = app(test_state(home.path()));

        let (status, text) = get_text(app.clone(), "/files/market/market.en.srt", &[]).await;
        assert_eq!(status, StatusCode::OK);
        assert!(text.ends_with("Hi\n"));

        // what a video player asks for when you seek
        let (status, text) = get_text(
            app,
            "/files/market/market.en.srt",
            &[("range", "bytes=0-0")],
        )
        .await;
        assert_eq!(status, StatusCode::PARTIAL_CONTENT);
        assert_eq!(text, "1");
    }

    #[tokio::test]
    async fn nothing_outside_the_runs_folder_is_served() {
        let home = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(home.path().join("runs")).unwrap();
        std::fs::write(home.path().join("secret.txt"), "not for the browser").unwrap();

        for path in ["/files/../secret.txt", "/files/%2e%2e/secret.txt"] {
            let (status, text) = get_text(app(test_state(home.path())), path, &[]).await;
            assert_eq!(status, StatusCode::NOT_FOUND, "{path}");
            assert!(!text.contains("not for the browser"), "{path}");
        }
    }

    #[tokio::test]
    async fn the_web_ui_is_served_at_the_root_and_for_its_own_pages() {
        let home = tempfile::tempdir().unwrap();
        let web = tempfile::tempdir().unwrap();
        std::fs::write(web.path().join("index.html"), "<h1>localdub</h1>").unwrap();
        let state = test_state(home.path()).with_web(web.path().to_path_buf());

        for path in ["/", "/runs/market"] {
            let (status, text) = get_text(app(state.clone()), path, &[]).await;
            assert_eq!(status, StatusCode::OK, "{path}");
            assert!(text.contains("localdub"), "{path}");
        }
        let (status, json) = get_json(app(state), "/api/health").await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(json["ok"], true, "the API still answers under /api");
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
        let mut request = Request::get("/api/health");
        if let Some(host) = host {
            request = request.header("host", host);
        }
        let response = app.oneshot(request.body(Body::empty()).unwrap()).await;
        response.unwrap().status()
    }

    /// POST /api/runs/half/continue with these headers, through the guard; returns the status.
    async fn continue_with(headers: &[(&str, &str)]) -> StatusCode {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());
        make_run(&state, "half", BEFORE_REVIEW1, &[]);
        let app = allow_hosts(app(state), default_hosts("127.0.0.1:8080"));
        let mut request = Request::post("/api/runs/half/continue");
        for (name, value) in headers {
            request = request.header(*name, *value);
        }
        let response = app.oneshot(request.body(Body::empty()).unwrap()).await;
        response.unwrap().status()
    }

    #[tokio::test]
    async fn other_sites_cannot_make_the_browser_change_anything() {
        let host = ("host", "localhost:8080");
        let evil = ("origin", "https://evil.example");
        let cross_site = ("sec-fetch-site", "cross-site");
        let own_page = [
            host,
            ("origin", "http://localhost:8080"),
            ("sec-fetch-site", "same-origin"),
        ];

        assert_eq!(continue_with(&[host, evil]).await, StatusCode::FORBIDDEN);
        assert_eq!(
            continue_with(&[host, cross_site]).await,
            StatusCode::FORBIDDEN
        );
        assert_eq!(continue_with(&own_page).await, StatusCode::ACCEPTED);
        assert_eq!(
            continue_with(&[host]).await,
            StatusCode::ACCEPTED,
            "curl, scripts"
        );
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

        let (status, json) = get_json(app(test_state(home.path())), "/api/runs").await;

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

        let (status, _) = get_json(app(test_state(home.path())), "/api/runs").await;

        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    }

    #[tokio::test]
    async fn there_is_no_job_before_the_first_run() {
        let home = tempfile::tempdir().unwrap();

        let (status, job) = get_json(app(test_state(home.path())), "/api/job").await;

        assert_eq!(status, StatusCode::OK);
        assert_eq!(job, Value::Null);
    }

    #[tokio::test]
    async fn starting_a_run_answers_at_once_and_the_job_follows_the_engine() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());

        let (status, job) = post_json(
            app(state.clone()),
            "/api/runs",
            start_body(&state, "done-run"),
        )
        .await;

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
            "/api/runs",
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
            "/api/runs",
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

        let (first, _) = post_json(
            app(state.clone()),
            "/api/runs",
            start_body(&state, "slow-run"),
        )
        .await;
        let (second, _) = post_json(
            app(state.clone()),
            "/api/runs",
            start_body(&state, "done-run"),
        )
        .await;

        assert_eq!(first, StatusCode::ACCEPTED);
        assert_eq!(second, StatusCode::CONFLICT);
        let job = job_when_settled(&state).await;
        assert_eq!(job["run"], "slow-run");
        assert_eq!(job["state"], "done");
    }

    /// A request with a body; `send` returns its status and JSON answer (Null for plain text).
    fn with_body(
        method: &str,
        path: &str,
        content_type: &str,
        body: impl Into<Body>,
    ) -> Request<Body> {
        Request::builder()
            .method(method)
            .uri(path)
            .header("content-type", content_type)
            .body(body.into())
            .unwrap()
    }

    /// A run folder as the engine leaves it: these stages done, and these files (path, text) in it.
    fn make_run(state: &AppState, name: &str, done: &[&str], files: &[(&str, &str)]) {
        let dir = state.home.join("runs").join(name);
        std::fs::create_dir_all(&dir).unwrap();
        let run_state = json!({"source": "/v.mp4", "channel": "c", "created": "2026-11-20T10:00:00",
                               "done": done, "timings": {}});
        std::fs::write(dir.join("state.json"), run_state.to_string()).unwrap();
        for (path, text) in files {
            let file = dir.join(path);
            std::fs::create_dir_all(file.parent().unwrap()).unwrap();
            std::fs::write(file, text).unwrap();
        }
    }

    const BEFORE_REVIEW1: &[&str] = &["prep", "separate", "transcribe", "spelling"];
    const FINISHED: &[&str] = &["prep", "review1", "review2", "voice", "mix"];

    #[tokio::test]
    async fn a_review_is_read_saved_and_then_the_run_continues() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());
        let review = (
            "review-1-transcript.txt",
            "status: pending\n[1] BG: Здравей\n",
        );
        make_run(&state, "done-run", BEFORE_REVIEW1, &[review]);

        let (_, runs) = get_json(app(state.clone()), "/api/runs").await;
        assert_eq!(runs[0]["waiting_for"]["kind"], "review1");

        let (status, review) = get_json(app(state.clone()), "/api/runs/done-run/review").await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(review["kind"], "review1");
        assert_eq!(review["text"], "status: pending\n[1] BG: Здравей\n");

        let edited = "status: approved\n[1] BG: Здравейте\n";
        let put = with_body("PUT", "/api/runs/done-run/review", "text/plain", edited);
        let (status, _) = send(app(state.clone()), put).await;
        assert_eq!(status, StatusCode::NO_CONTENT);
        let file = state.home.join("runs/done-run/review-1-transcript.txt");
        assert_eq!(std::fs::read_to_string(file).unwrap(), edited);

        let (status, job) =
            post_json(app(state.clone()), "/api/runs/done-run/continue", json!({})).await;
        assert_eq!(status, StatusCode::ACCEPTED);
        assert_eq!(job["run"], "done-run");
        let job = job_when_settled(&state).await;
        assert_eq!(
            job["state"], "done",
            "the fake engine's done-run story ran: {job}"
        );
    }

    #[tokio::test]
    async fn a_run_that_waits_for_nothing_has_no_review() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());
        make_run(&state, "plain", FINISHED, &[]);

        let (get, _) = get_json(app(state.clone()), "/api/runs/plain/review").await;
        let put = with_body(
            "PUT",
            "/api/runs/plain/review",
            "text/plain",
            "status: approved",
        );
        let (put, _) = send(app(state), put).await;

        assert_eq!((get, put), (StatusCode::NOT_FOUND, StatusCode::NOT_FOUND));
    }

    #[tokio::test]
    async fn a_finished_run_is_not_continued_but_makes_shorts() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());
        make_run(&state, "market", FINISHED, &[]);

        let (again, _) =
            post_json(app(state.clone()), "/api/runs/market/continue", json!({})).await;
        let body = json!({"lang": "dub"});
        let (shorts, _) = post_json(app(state.clone()), "/api/runs/market/shorts", body).await;

        assert_eq!(again, StatusCode::CONFLICT);
        assert_eq!(shorts, StatusCode::ACCEPTED);
        let job = job_when_settled(&state).await;
        assert_eq!(job["state"], "waiting");
        assert_eq!(job["waiting_for"]["kind"], "shorts");
    }

    #[tokio::test]
    async fn shorts_wait_for_the_dub() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());
        make_run(&state, "half", BEFORE_REVIEW1, &[]);

        let body = json!({"lang": "original"});
        let (status, _) = post_json(app(state), "/api/runs/half/shorts", body).await;

        assert_eq!(status, StatusCode::CONFLICT);
    }

    #[tokio::test]
    async fn picked_shorts_are_rendered_by_continuing_with_the_shorts_command() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());
        let picked = (
            "shorts/dub/candidates.txt",
            "status: approved\n[x] 1   00:10-00:40\n",
        );
        make_run(&state, "market", FINISHED, &[picked]);

        let (status, _) =
            post_json(app(state.clone()), "/api/runs/market/continue", json!({})).await;

        assert_eq!(status, StatusCode::ACCEPTED);
        let job = job_when_settled(&state).await;
        assert_eq!(
            job["waiting_for"]["kind"], "shorts",
            "the engine ran `shorts`: {job}"
        );
    }

    #[tokio::test]
    async fn runs_that_do_not_exist_are_not_found() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());

        for path in ["/api/runs/nothing/review", "/api/runs/..%2F..%2Fetc/review"] {
            let (status, _) = get_json(app(state.clone()), path).await;
            assert_eq!(status, StatusCode::NOT_FOUND, "{path}");
        }
        let (status, _) = post_json(app(state), "/api/runs/nothing/continue", json!({})).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn a_video_is_uploaded_to_disk_and_listed() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());
        let video = vec![7u8; 3 * 1024 * 1024]; // bigger than axum's usual 2 MB limit for a body

        let put = with_body("PUT", "/api/videos/market.mp4", "video/mp4", video.clone());
        let (status, uploaded) = send(app(state.clone()), put).await;

        assert_eq!(status, StatusCode::CREATED);
        assert_eq!(uploaded["bytes"], video.len());
        let path = uploaded["path"].as_str().unwrap().to_string();
        assert_eq!(std::fs::read(&path).unwrap(), video);
        let (_, videos) = get_json(app(state.clone()), "/api/videos").await;
        assert_eq!(
            videos,
            json!([{"name": "market.mp4", "path": path, "bytes": video.len()}])
        );

        let again = with_body("PUT", "/api/videos/market.mp4", "video/mp4", vec![1u8]);
        let (status, _) = send(app(state.clone()), again).await;
        assert_eq!(
            status,
            StatusCode::CONFLICT,
            "an uploaded video isn't replaced"
        );

        // and the uploaded video can be dubbed
        let body = json!({"video": path, "channel": "c", "name": "done-run"});
        let (status, _) = post_json(app(state), "/api/runs", body).await;
        assert_eq!(status, StatusCode::ACCEPTED);
    }

    #[tokio::test]
    async fn uploads_with_unsafe_names_are_refused() {
        let home = tempfile::tempdir().unwrap();
        let state = test_state(home.path());

        for name in ["..%2Fescape.mp4", ".hidden.mp4", "a%20b.mp4"] {
            let put = with_body(
                "PUT",
                &format!("/api/videos/{name}"),
                "video/mp4",
                vec![1u8],
            );
            let (status, _) = send(app(state.clone()), put).await;
            assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY, "{name}");
        }
        assert!(!home.path().join("escape.mp4").exists());
    }

    #[tokio::test]
    async fn a_video_outside_the_home_folder_is_refused() {
        let home = tempfile::tempdir().unwrap();
        let elsewhere = tempfile::NamedTempFile::new().unwrap();
        let body = json!({"video": elsewhere.path(), "channel": "c", "name": "x"});

        let request = with_body("POST", "/api/runs", "application/json", body.to_string());
        let (status, text) = get_text_from(app(test_state(home.path())), request).await;

        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert!(text.contains("only videos inside"), "{text}");
    }

    /// Sends a request and returns the status and the body as text.
    async fn get_text_from(app: Router, request: Request<Body>) -> (StatusCode, String) {
        let response = app.oneshot(request).await.unwrap();
        let status = response.status();
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        (status, String::from_utf8_lossy(&bytes).into_owned())
    }

    #[tokio::test]
    async fn a_start_request_without_a_name_is_refused() {
        let home = tempfile::tempdir().unwrap();
        let body = json!({"video": "done-run", "channel": "c"});

        let (status, _) = post_json(app(test_state(home.path())), "/api/runs", body).await;

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
            Request::post("/api/runs")
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await;

        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(message, Value::Null, "the reason comes back as plain text");
        let (_, job) = get_json(app(state), "/api/job").await;
        assert_eq!(job, Value::Null, "no job was started");
    }

    #[tokio::test]
    async fn an_engine_that_cannot_be_started_fails_the_job() {
        let home = tempfile::tempdir().unwrap();
        let state = AppState::new(home.path().to_path_buf(), vec!["/no/such/localdub".into()]);

        post_json(
            app(state.clone()),
            "/api/runs",
            start_body(&state, "done-run"),
        )
        .await;

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
        let request = Request::get("/api/job/events").body(Body::empty()).unwrap();
        let response = app(state.clone()).oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(response.headers()["content-type"], "text/event-stream");

        post_json(
            app(state.clone()),
            "/api/runs",
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
