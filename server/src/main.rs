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

use std::path::PathBuf;

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

    let listener = tokio::net::TcpListener::bind(&listen).await?;
    eprintln!(
        "localdub-server: http://{listen}, runs in {}, engine: {}, answers to: {}",
        home.display(),
        engine.join(" "),
        hosts.join(", ")
    );
    let app = allow_hosts(app(AppState::new(home, engine)), hosts);
    axum::serve(listener, app).await?;
    Ok(())
}
