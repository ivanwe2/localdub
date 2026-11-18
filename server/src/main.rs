//! Starts the server: reads where the runs live and how to start the engine, then serves the app
//! from lib.rs on port 8080.
//!
//!   LOCALDUB_HOME    channels and runs (default ~/dub), the same folder the engine uses
//!   LOCALDUB_ENGINE  the engine's command (default `localdub`), e.g. "/usr/local/bin/localdub"
//!   LOCALDUB_LISTEN  address and port (default 127.0.0.1:8080: this machine only). There is no
//!                    login yet, so open it to the network (0.0.0.0:8080) only on a trusted LAN.

use std::path::PathBuf;

use localdub_server::{AppState, app};

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

    let listener = tokio::net::TcpListener::bind(&listen).await?;
    eprintln!(
        "localdub-server: http://{listen}, runs in {}, engine: {}",
        home.display(),
        engine.join(" ")
    );
    axum::serve(listener, app(AppState::new(home, engine))).await?;
    Ok(())
}
