//! The Models screen: which model does each task (text, vision, voice, hearing, separation), on
//! which server. The engine owns all of it (`localdub models`, src/localdub/models.py); here the
//! page's requests are checked and turned into its arguments. A server's API key goes to the
//! engine on stdin, never in its arguments, and never comes back.

use std::collections::BTreeMap;

use serde::Deserialize;

pub const TASKS: [&str; 5] = ["text", "vision", "voice", "hearing", "separation"];
pub const KINDS: [&str; 2] = ["llama-swap", "openai"];
/// What the text model thinks about, each with its own level.
pub const USES: [&str; 4] = ["spelling", "translate", "shorten", "shorts"];
pub const LEVELS: [&str; 5] = ["default", "none", "low", "medium", "high"];

/// A task's model: `{"server": "main", "model": "gemma4-31b"}`.
#[derive(Debug, PartialEq, Deserialize)]
pub struct Pick {
    pub server: String,
    pub model: String,
}

/// The body of PUT /api/models: the tasks to move and the thinking levels to change.
#[derive(Debug, Default, PartialEq, Deserialize)]
pub struct Change {
    #[serde(default)]
    pub tasks: BTreeMap<String, Pick>,
    #[serde(default)]
    pub efforts: BTreeMap<String, String>,
}

impl Change {
    /// `models --set TASK=SERVER/MODEL … --effort USE=LEVEL …`, or what's wrong with the request.
    pub fn args(&self) -> Result<Vec<String>, String> {
        if self.tasks.is_empty() && self.efforts.is_empty() {
            return Err("nothing to change".into());
        }
        let mut args = vec!["models".to_string()];
        if !self.tasks.is_empty() {
            args.push("--set".into());
            for (task, pick) in &self.tasks {
                if !TASKS.contains(&task.as_str()) {
                    return Err(format!("task {task:?}: one of {}", TASKS.join(", ")));
                }
                if !is_server_name(&pick.server) {
                    return Err(format!("server {:?}: {SERVER_RULE}", pick.server));
                }
                if !is_model(&pick.model) {
                    return Err(format!("model {:?}: {MODEL_RULE}", pick.model));
                }
                args.push(format!("{task}={}/{}", pick.server, pick.model));
            }
        }
        if !self.efforts.is_empty() {
            args.push("--effort".into());
            for (use_, level) in &self.efforts {
                if !USES.contains(&use_.as_str()) || !LEVELS.contains(&level.as_str()) {
                    return Err(format!(
                        "thinking {use_:?} = {level:?}: one of {} = one of {}",
                        USES.join(", "),
                        LEVELS.join(", ")
                    ));
                }
                args.push(format!("{use_}={level}"));
            }
        }
        Ok(args)
    }
}

/// The body of PUT /api/models/servers/{name}. `key`: a new key (an empty one removes it); left
/// out, the key stays as it is.
#[derive(Debug, PartialEq, Deserialize)]
pub struct ServerChange {
    pub url: String,
    pub kind: String,
    pub key: Option<String>,
}

impl ServerChange {
    /// `models --server NAME --url URL --kind KIND [--key-stdin]`, and the key for stdin.
    pub fn args(&self, name: &str) -> Result<(Vec<String>, Option<String>), String> {
        if !is_server_name(name) {
            return Err(format!("server {name:?}: {SERVER_RULE}"));
        }
        if !is_url(&self.url) {
            return Err(format!(
                "url {:?}: an http:// or https:// address, without spaces",
                self.url
            ));
        }
        if !KINDS.contains(&self.kind.as_str()) {
            return Err(format!("kind {:?}: one of {}", self.kind, KINDS.join(", ")));
        }
        let key = self.key.as_ref().map(|k| k.trim().to_string()); // a pasted key often ends in a newline
        if let Some(key) = &key
            && (key.len() > 4096 || key.chars().any(char::is_control))
        {
            return Err("the key: one line of text".into());
        }
        let mut args = vec![
            "models".to_string(),
            "--server".into(),
            name.into(),
            "--url".into(),
            self.url.clone(),
            "--kind".into(),
            self.kind.clone(),
        ];
        if self.key.is_some() {
            args.push("--key-stdin".into());
        }
        Ok((args, key))
    }
}

/// The body of POST /api/models/presets/{name}: the setup's key, if it needs one (it goes to the
/// engine on stdin).
#[derive(Debug, Default, PartialEq, Deserialize)]
pub struct PresetRequest {
    #[serde(default)]
    pub key: Option<String>,
}

impl PresetRequest {
    /// `models --preset NAME [--key-stdin]`, and the key for stdin. Which setups there are is the
    /// engine's to say (an unknown one is refused there).
    pub fn args(&self, name: &str) -> Result<(Vec<String>, Option<String>), String> {
        if !is_server_name(name) {
            return Err(format!("setup {name:?}: {SERVER_RULE}"));
        }
        let key = self.key.as_ref().map(|k| k.trim().to_string());
        if let Some(key) = &key
            && (key.is_empty() || key.len() > 4096 || key.chars().any(char::is_control))
        {
            return Err("the key: one line of text".into());
        }
        let mut args = vec!["models".to_string(), "--preset".into(), name.into()];
        if key.is_some() {
            args.push("--key-stdin".into());
        }
        Ok((args, key))
    }
}

/// `models --server NAME --remove`.
pub fn remove_args(name: &str) -> Result<Vec<String>, String> {
    if !is_server_name(name) {
        return Err(format!("server {name:?}: {SERVER_RULE}"));
    }
    Ok(vec![
        "models".into(),
        "--server".into(),
        name.into(),
        "--remove".into(),
    ])
}

/// The body of POST /api/models/test.
#[derive(Debug, PartialEq, Deserialize)]
pub struct TestRequest {
    pub task: String,
}

/// `models --test TASK`.
pub fn test_args(task: &str) -> Result<Vec<String>, String> {
    if !TASKS.contains(&task) {
        return Err(format!("task {task:?}: one of {}", TASKS.join(", ")));
    }
    Ok(vec!["models".into(), "--test".into(), task.into()])
}

const SERVER_RULE: &str =
    "letters, digits, '.', '_' and '-', starting with a letter or digit, at most 32";
const MODEL_RULE: &str = "a model's name as its server lists it: no spaces, not starting with '-'";

/// A server's name: safe in settings and as an argument (no leading '-').
fn is_server_name(s: &str) -> bool {
    let mut chars = s.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && s.len() <= 32
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

/// A model's name as servers spell them ("gemma4-31b", "org/model", "qwen3:32b").
fn is_model(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 200
        && !s.starts_with('-')
        && !s.chars().any(|c| c.is_whitespace() || c.is_control())
}

fn is_url(s: &str) -> bool {
    (s.starts_with("http://") || s.starts_with("https://"))
        && s.len() <= 2048
        && !s.chars().any(|c| c.is_whitespace() || c.is_control())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn task_picks_and_thinking_levels_become_the_engines_flags() {
        let change: Change = serde_json::from_str(
            r#"{"tasks": {"text": {"server": "cloud", "model": "org/model-1"}, "voice": {"server": "main", "model": "qwen3-tts"}},
                "efforts": {"translate": "low"}}"#,
        )
        .unwrap();

        assert_eq!(
            change.args().unwrap(),
            [
                "models",
                "--set",
                "text=cloud/org/model-1",
                "voice=main/qwen3-tts",
                "--effort",
                "translate=low"
            ]
        );
    }

    #[test]
    fn unknown_tasks_odd_names_and_levels_are_refused() {
        let pick = |task: &str, server: &str, model: &str| Change {
            tasks: BTreeMap::from([(
                task.to_string(),
                Pick {
                    server: server.into(),
                    model: model.into(),
                },
            )]),
            ..Change::default()
        };
        for bad in [
            pick("speech", "main", "x"),
            pick("text", "-x", "m"),
            pick("text", "a/b", "m"),
            pick("text", "main", "--help"),
            pick("text", "main", "two words"),
            pick("text", "main", ""),
            Change {
                efforts: BTreeMap::from([("translate".into(), "max".into())]),
                ..Change::default()
            },
            Change::default(),
        ] {
            assert!(bad.args().is_err(), "{bad:?}");
        }
    }

    #[test]
    fn a_server_and_its_key_the_key_goes_to_stdin_only() {
        let change = ServerChange {
            url: "https://api.example.com/v1".into(),
            kind: "openai".into(),
            key: Some(" sk-secret \n".into()),
        };

        let (args, key) = change.args("cloud").unwrap();

        assert_eq!(
            args,
            [
                "models",
                "--server",
                "cloud",
                "--url",
                "https://api.example.com/v1",
                "--kind",
                "openai",
                "--key-stdin"
            ]
        );
        assert_eq!(key.as_deref(), Some("sk-secret"));
        assert!(!args.iter().any(|a| a.contains("sk-secret")));
        let no_key = ServerChange {
            key: None,
            ..change
        };
        assert_eq!(no_key.args("cloud").unwrap().1, None);
    }

    #[test]
    fn server_urls_kinds_and_names_must_have_the_right_shape() {
        let good = || ServerChange {
            url: "http://gpu-box:9876".into(),
            kind: "llama-swap".into(),
            key: None,
        };
        assert!(good().args("main").is_ok());
        for (name, url, kind) in [
            ("-x", "http://a", "openai"),
            ("a b", "http://a", "openai"),
            ("cloud", "ftp://a", "openai"),
            ("cloud", "http://a b", "openai"),
            ("cloud", "http://a", "ollama"),
        ] {
            let change = ServerChange {
                url: url.into(),
                kind: kind.into(),
                ..good()
            };
            assert!(change.args(name).is_err(), "{name} {url} {kind}");
        }
        assert!(remove_args("cloud").is_ok() && remove_args("../x").is_err());
        assert!(test_args("voice").is_ok() && test_args("--help").is_err());
    }
}
