//! Starts the server: reads where the runs live and how to start the engine, then serves the app
//! from lib.rs.
//!
//!   LOCALDUB_HOME           channels and runs (default ~/dub), the same folder the engine uses
//!   LOCALDUB_ENGINE         the engine's command (default `localdub`), e.g. "/usr/local/bin/localdub"
//!   LOCALDUB_LISTEN         address and port (default 127.0.0.1:8080: this machine only). There is
//!                           no login yet, so open it to the network (0.0.0.0:8080) only on a
//!                           trusted LAN.
//!   LOCALDUB_ALLOWED_HOSTS  more names the browser may use to reach it, comma-separated, e.g.
//!                           "192.168.0.71:8080,llama-cpp:8080" (localhost always works)
//!   LOCALDUB_WEB            the built web UI (default: web/dist or ../web/dist, if it exists)
//!   LOCALDUB_MODELS_URL     llama-swap, for the models' and GPUs' status (default
//!                           http://127.0.0.1:9876; "none" turns the status off)
//!   LLAMA_API_KEY           its API key (the service reads it from /etc/llama.env)

use std::path::PathBuf;
use std::time::Duration;

use localdub_server::status::{ModelServer, keep_asking};
use localdub_server::{AppState, allow_hosts, app, default_hosts};

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let home = match std::env::var("LOCALDUB_HOME") {
        Ok(dir) => PathBuf::from(dir),
        Err(_) => PathBuf::from(std::env::var("HOME").unwrap_or_default()).join("dub"),
    };
    let engine: Vec<String> = match std::env::var("LOCALDUB_ENGINE") {
        Ok(command) => command.split_whitespace().map(String::from).collect(),
        Err(_) => vec!["localdub".to_string()],
    };
    let listen = std::env::var("LOCALDUB_LISTEN").unwrap_or_else(|_| "127.0.0.1:8080".to_string());
    let mut hosts = default_hosts(&listen);
    if let Ok(extra) = std::env::var("LOCALDUB_ALLOWED_HOSTS") {
        let extra = extra.split(',').map(str::trim).filter(|h| !h.is_empty());
        hosts.extend(extra.map(String::from));
    }

    let web = match std::env::var("LOCALDUB_WEB") {
        Ok(dir) => Some(PathBuf::from(dir)),
        Err(_) => ["web/dist", "../web/dist"]
            .into_iter()
            .map(PathBuf::from)
            .find(|dir| dir.join("index.html").is_file()),
    };

    let listener = tokio::net::TcpListener::bind(&listen).await?;
    eprintln!(
        "localdub-server: http://{listen}, runs in {}, engine: {}, answers to: {}, web UI: {}",
        home.display(),
        engine.join(" "),
        hosts.join(", "),
        web.as_ref()
            .map_or("none (API only)".to_string(), |d| d.display().to_string())
    );
    let mut state = AppState::new(home, engine);
    if let Some(dir) = web {
        state = state.with_web(dir);
    }
    let models_url = std::env::var("LOCALDUB_MODELS_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:9876".to_string());
    if models_url != "none" {
        let key = std::env::var("LLAMA_API_KEY")
            .ok()
            .filter(|k| !k.is_empty());
        let server = ModelServer {
            url: models_url,
            key,
        };
        tokio::spawn(keep_asking(
            server.clone(),
            state.status.clone(),
            Duration::from_secs(2),
        ));
        state = state.with_models(server);
    }
    let app = allow_hosts(app(state), hosts);
    axum::serve(listener, app).await?;
    Ok(())
}
