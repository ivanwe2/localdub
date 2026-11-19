//! What the AI hardware is doing right now: the models llama-swap has running, and each GPU's load,
//! memory, temperature and power (from llama-swap's /metrics). Asked every couple of seconds, but
//! only while someone watches (GET /api/status/events), so an idle server asks nothing.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tokio::sync::watch;

/// Where the models are: llama-swap's address and its API key.
#[derive(Debug, Clone)]
pub struct ModelServer {
    pub url: String,
    pub key: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Status {
    /// The models whose servers are up (starting or ready).
    pub models: Vec<RunningModel>,
    pub gpus: Vec<Gpu>,
    /// The machine's memory.
    pub memory: Option<Memory>,
    /// Why the model server couldn't be asked, if it couldn't.
    pub error: Option<String>,
    /// When it was asked, in milliseconds since 1970.
    pub at_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunningModel {
    pub model: String,
    pub state: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: String,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Gpu {
    pub id: String,
    pub name: String,
    pub util_percent: f64,
    pub memory_used_bytes: f64,
    pub memory_total_bytes: f64,
    pub temperature_c: f64,
    pub power_w: f64,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Memory {
    pub used_bytes: f64,
    pub total_bytes: f64,
}

/// The GPUs and the machine's memory from llama-swap's /metrics, which is in the Prometheus text
/// format: comment lines starting with '#', and lines like
/// `llamaswap_gpu_util_percent{id="0",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-…"} 87.5`.
pub fn parse_metrics(text: &str) -> (Vec<Gpu>, Option<Memory>) {
    let mut gpus: BTreeMap<String, Gpu> = BTreeMap::new();
    let mut memory: Option<Memory> = None;
    for line in text.lines().filter(|l| !l.starts_with('#')) {
        let Some((key, value)) = line.rsplit_once(' ') else {
            continue;
        };
        let Ok(value) = value.trim().parse::<f64>() else {
            continue;
        };
        let (metric, labels) = match key.split_once('{') {
            Some((metric, labels)) => (metric, labels.trim_end_matches('}')),
            None => (key, ""),
        };
        if let Some(field) = metric.strip_prefix("llamaswap_memory_") {
            let memory = memory.get_or_insert_with(Memory::default);
            match field {
                "used_bytes" => memory.used_bytes = value,
                "total_bytes" => memory.total_bytes = value,
                _ => {}
            }
        } else if let Some(field) = metric.strip_prefix("llamaswap_gpu_") {
            let id = label(labels, "id").unwrap_or_default();
            let gpu = gpus.entry(id.clone()).or_insert_with(|| Gpu {
                id,
                name: label(labels, "name").unwrap_or_default(),
                ..Gpu::default()
            });
            match field {
                "util_percent" => gpu.util_percent = value,
                "memory_used_bytes" => gpu.memory_used_bytes = value,
                "memory_total_bytes" => gpu.memory_total_bytes = value,
                "temperature_celsius" => gpu.temperature_c = value,
                "power_draw_watts" => gpu.power_w = value,
                _ => {}
            }
        }
    }
    (gpus.into_values().collect(), memory)
}

/// One label's value from `id="0",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-…"`.
fn label(labels: &str, key: &str) -> Option<String> {
    labels.split("\",").find_map(|pair| {
        let (k, v) = pair.split_once("=\"")?;
        (k.trim() == key).then(|| v.trim_end_matches('"').to_string())
    })
}

async fn get_text(
    client: &reqwest::Client,
    server: &ModelServer,
    path: &str,
) -> Result<String, String> {
    let url = format!("{}{path}", server.url.trim_end_matches('/'));
    let mut request = client.get(url).timeout(Duration::from_secs(5));
    if let Some(key) = &server.key {
        request = request.bearer_auth(key);
    }
    let response = request.send().await.map_err(|e| format!("{path}: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("{path}: HTTP {}", response.status()));
    }
    response.text().await.map_err(|e| format!("{path}: {e}"))
}

#[derive(Deserialize)]
struct Running {
    running: Vec<RunningModel>,
}

/// Asks the model server once. Whatever fails is in `error`; the rest is still filled in.
pub async fn ask(client: &reqwest::Client, server: &ModelServer) -> Status {
    let mut status = Status {
        at_ms: now_ms(),
        ..Status::default()
    };
    let mut errors = Vec::new();
    match get_text(client, server, "/running").await {
        Ok(text) => match serde_json::from_str::<Running>(&text) {
            Ok(running) => {
                status.models = running
                    .running
                    .into_iter()
                    .filter(|m| m.state != "stopped" && m.state != "shutdown")
                    .collect()
            }
            Err(e) => errors.push(format!("/running: {e}")),
        },
        Err(e) => errors.push(e),
    }
    match get_text(client, server, "/metrics").await {
        Ok(text) => (status.gpus, status.memory) = parse_metrics(&text),
        Err(e) => errors.push(e),
    }
    if !errors.is_empty() {
        status.error = Some(format!(
            "couldn't ask the model server ({}): {}",
            server.url,
            errors.join("; ")
        ));
    }
    status
}

/// Asks every `every` for as long as the server runs, but only while someone is subscribed to
/// `status` (the page's live stream), and publishes each answer there.
pub async fn keep_asking(server: ModelServer, status: Arc<watch::Sender<Status>>, every: Duration) {
    let client = reqwest::Client::new();
    loop {
        if status.receiver_count() > 0 {
            let fresh = ask(&client, &server).await;
            status.send_replace(fresh);
        }
        tokio::time::sleep(every).await;
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as u64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::{HeaderMap, StatusCode};
    use axum::routing::get;

    /// As CT 103's llama-swap reports it (the GPUs' serial numbers made up).
    const METRICS: &str = r#"# HELP llamaswap_cpu_util_percent CPU utilization per core (0-100)
# TYPE llamaswap_cpu_util_percent gauge
llamaswap_cpu_util_percent{core="0"} 1.000000000003638
llamaswap_memory_total_bytes 31977373696
llamaswap_memory_used_bytes 3292528640
llamaswap_gpu_temperature_celsius{id="0",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-0000"} 36
llamaswap_gpu_temperature_celsius{id="1",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-1111"} 33
llamaswap_gpu_util_percent{id="0",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-0000"} 0
llamaswap_gpu_util_percent{id="1",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-1111"} 97
llamaswap_gpu_memory_used_bytes{id="0",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-0000"} 4.567597056e+09
llamaswap_gpu_memory_used_bytes{id="1",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-1111"} 1.4971568128e+10
llamaswap_gpu_memory_total_bytes{id="0",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-0000"} 1.7103323136e+10
llamaswap_gpu_memory_total_bytes{id="1",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-1111"} 1.7103323136e+10
llamaswap_gpu_power_draw_watts{id="0",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-0000"} 3.76
llamaswap_gpu_power_draw_watts{id="1",name="NVIDIA GeForce RTX 5060 Ti",uuid="GPU-1111"} 6.85
"#;

    const RUNNING: &str = r#"{"running": [
        {"model": "nemotron", "state": "ready", "cmd": "/root/.local/bin/nemo-speech serve ...",
         "proxy": "http://localhost:5803", "ttl": 0, "name": "Nemotron 3.5 Transcribe",
         "description": "transcription with word times and the take check, on GPU 1"},
        {"model": "qwen3-tts", "state": "starting", "name": "Qwen3-TTS 1.7B Base", "description": ""},
        {"model": "old", "state": "stopped"}]}"#;

    #[test]
    fn the_gpus_and_memory_are_read_from_the_metrics() {
        let (gpus, memory) = parse_metrics(METRICS);

        assert_eq!(gpus.len(), 2);
        assert_eq!(gpus[1].id, "1");
        assert_eq!(gpus[1].name, "NVIDIA GeForce RTX 5060 Ti");
        assert_eq!(gpus[1].util_percent, 97.0);
        assert_eq!(gpus[1].memory_used_bytes, 14_971_568_128.0);
        assert_eq!(gpus[1].memory_total_bytes, 17_103_323_136.0);
        assert_eq!((gpus[0].temperature_c, gpus[0].power_w), (36.0, 3.76));
        assert_eq!(memory.unwrap().total_bytes, 31_977_373_696.0);
    }

    #[test]
    fn a_label_is_found_by_its_whole_name() {
        let labels = r#"uuid="GPU-9",id="1",name="A, B""#;

        assert_eq!(
            label(labels, "id").as_deref(),
            Some("1"),
            "not the end of uuid"
        );
        assert_eq!(label(labels, "uuid").as_deref(), Some("GPU-9"));
        assert_eq!(label(labels, "missing"), None);
    }

    /// A stand-in for llama-swap on a free port that wants this API key.
    async fn fake_llama_swap() -> String {
        let authorized = |headers: &HeaderMap| {
            headers.get("authorization").and_then(|v| v.to_str().ok()) == Some("Bearer k")
        };
        let app = axum::Router::new()
            .route(
                "/running",
                get(move |h: HeaderMap| async move {
                    if authorized(&h) {
                        Ok(RUNNING)
                    } else {
                        Err(StatusCode::UNAUTHORIZED)
                    }
                }),
            )
            .route(
                "/metrics",
                get(move |h: HeaderMap| async move {
                    if authorized(&h) {
                        Ok(METRICS)
                    } else {
                        Err(StatusCode::UNAUTHORIZED)
                    }
                }),
            );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        url
    }

    #[tokio::test]
    async fn the_model_server_is_asked_with_its_key() {
        let url = fake_llama_swap().await;
        let server = ModelServer {
            url,
            key: Some("k".into()),
        };

        let status = ask(&reqwest::Client::new(), &server).await;

        assert_eq!(status.error, None);
        let models: Vec<_> = status
            .models
            .iter()
            .map(|m| (m.model.as_str(), m.state.as_str()))
            .collect();
        assert_eq!(
            models,
            [("nemotron", "ready"), ("qwen3-tts", "starting")],
            "stopped ones left out"
        );
        assert_eq!(status.models[0].name, "Nemotron 3.5 Transcribe");
        assert_eq!(status.gpus.len(), 2);
        assert!(status.at_ms > 0);
    }

    #[tokio::test]
    async fn a_wrong_key_or_no_server_is_reported_not_fatal() {
        let url = fake_llama_swap().await;
        let wrong = ModelServer {
            url,
            key: Some("nope".into()),
        };
        let nobody = ModelServer {
            url: "http://127.0.0.1:1".into(),
            key: None,
        };
        let client = reqwest::Client::new();

        let wrong = ask(&client, &wrong).await;
        let nobody = ask(&client, &nobody).await;

        assert!(
            wrong.error.as_deref().unwrap().contains("401"),
            "{:?}",
            wrong.error
        );
        assert!(nobody.error.is_some() && nobody.models.is_empty() && nobody.gpus.is_empty());
    }
}
