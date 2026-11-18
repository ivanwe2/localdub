//! The localdub server as a library: the routes and everything behind them. `main.rs` only reads
//! the settings and starts it (like an ASP.NET class library and a small host project). Everything
//! `pub` here is the library's API, so a finished piece doesn't count as "never used" before a
//! route uses it.

pub mod events;
pub mod runner;
pub mod runs;

use std::path::PathBuf;

use axum::extract::State;
use axum::http::StatusCode;
use axum::{Json, Router, routing::get};

#[derive(serde::Serialize)]
struct Health {
    ok: bool,
    version: &'static str,
}

/// What every handler can ask for: where the channels and runs live (LOCALDUB_HOME, else ~/dub).
#[derive(Clone)]
pub struct AppState {
    pub home: PathBuf,
}

async fn health() -> Json<Health> {
    Json(Health {
        ok: true,
        version: env!("CARGO_PKG_VERSION"),
    })
}

async fn get_runs(
    State(state): State<AppState>,
) -> Result<Json<Vec<runs::RunSummary>>, (StatusCode, String)> {
    let list = runs::list_runs(&state.home)
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    Ok(Json(list))
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/runs", get(get_runs))
        .with_state(state)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use http_body_util::BodyExt; // .collect() on a response body
    use tower::ServiceExt; // .oneshot() on a Router

    #[tokio::test]
    async fn test_health() {
        let Json(h) = health().await;
        assert!(h.ok);
        assert_eq!(h.version, env!("CARGO_PKG_VERSION"));
    }

    /// Send `GET path` through the whole app, as a browser would but without a network (like
    /// ASP.NET's WebApplicationFactory), and return the status and the body as JSON (Null if it isn't).
    async fn get_json(app: Router, path: &str) -> (StatusCode, serde_json::Value) {
        let request = Request::get(path).body(Body::empty()).unwrap();
        let response = app.oneshot(request).await.unwrap();
        let status = response.status();
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        let json = serde_json::from_slice(&bytes).unwrap_or(serde_json::Value::Null);
        (status, json)
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
        let state = AppState {
            home: home.path().to_path_buf(),
        };

        let (status, json) = get_json(app(state), "/runs").await;

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
        let state = AppState {
            home: home.path().to_path_buf(),
        };

        let (status, _) = get_json(app(state), "/runs").await;

        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    }
}
