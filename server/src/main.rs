//! Starts the server: reads where the runs live, then serves the app from lib.rs on port 8080.

use std::path::PathBuf;

use localdub_server::{AppState, app};

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await?;
    let home = match std::env::var("LOCALDUB_HOME") {
        Ok(dir) => PathBuf::from(dir),
        Err(_) => PathBuf::from(std::env::var("HOME").unwrap_or_default()).join("dub"),
    };

    axum::serve(listener, app(AppState { home })).await?;
    Ok(())
}
