//! Starting a run: what the browser sends to POST /runs, the checks it must pass, and the engine
//! command it turns into.

use std::path::Path;

use serde::Deserialize;

/// The body of POST /runs.
#[derive(Debug, PartialEq, Deserialize)]
pub struct StartRequest {
    /// The video file: a path on the machine where the engine runs.
    pub video: String,
    /// The channel whose profile the run uses (and, with reviews, teaches).
    pub channel: String,
    /// The run's name, which is also its folder in runs/.
    pub name: String,
    /// Skip both reviews; nothing is learned from the run.
    #[serde(default)]
    pub no_review: bool,
    /// When the dub is done, also make the top N Shorts in both languages (0 = none).
    #[serde(default)]
    pub shorts: u32,
}

impl StartRequest {
    /// Checks the request before anything runs; the error says what's wrong. The values become
    /// engine arguments and folder names, so: a name and channel can't reach outside their folder
    /// or pass for an engine option, and the video must be an existing file.
    pub fn validate(&self) -> Result<(), String> {
        if !is_safe_name(&self.name) {
            return Err(format!("name {:?}: {NAME_RULE}", self.name));
        }
        if !is_safe_name(&self.channel) {
            return Err(format!("channel {:?}: {NAME_RULE}", self.channel));
        }
        if self.video.starts_with('-') || !Path::new(&self.video).is_file() {
            return Err(format!("video {:?}: no such file", self.video));
        }
        Ok(())
    }
}

const NAME_RULE: &str =
    "use letters, digits, '.', '_' and '-', starting with a letter or digit, at most 64 characters";

/// A run or channel name that is safe as a folder name and as an argument: no '/', no leading '-'
/// or '.', so no "..", nothing the engine would read as an option.
fn is_safe_name(s: &str) -> bool {
    let mut chars = s.chars();
    let starts_well = chars.next().is_some_and(char::is_alphanumeric);
    starts_well
        && s.chars().count() <= 64
        && chars.all(|c| c.is_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

/// The engine's arguments for this request, without the program itself and without `--json`
/// (run_engine adds that):
/// `run <video> --channel <channel> --name <name>`, then `--no-review` and `--shorts N` if asked for.
pub fn engine_args(req: &StartRequest) -> Vec<String> {
    // `req` is only borrowed, so its Strings are copied (clone) into the list we hand back.
    let mut args = vec![
        "run".to_string(),
        req.video.clone(),
        "--channel".to_string(),
        req.channel.clone(),
        "--name".to_string(),
        req.name.clone(),
    ];
    if req.no_review {
        args.push("--no-review".to_string());
    }
    if req.shorts > 0 {
        args.push("--shorts".to_string());
        args.push(req.shorts.to_string());
    }
    args
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(no_review: bool, shorts: u32) -> StartRequest {
        StartRequest {
            video: "/videos/market.mp4".into(),
            channel: "mychannel".into(),
            name: "market".into(),
            no_review,
            shorts,
        }
    }

    #[test]
    fn a_plain_run() {
        assert_eq!(
            engine_args(&request(false, 0)),
            vec![
                "run",
                "/videos/market.mp4",
                "--channel",
                "mychannel",
                "--name",
                "market"
            ]
        );
    }

    #[test]
    fn without_reviews() {
        let args = engine_args(&request(true, 0));

        assert_eq!(args.len(), 7);
        assert_eq!(args.last().map(String::as_str), Some("--no-review"));
    }

    #[test]
    fn with_shorts() {
        let args = engine_args(&request(false, 3));

        assert_eq!(args[6..], ["--shorts", "3"]);
    }

    #[test]
    fn without_reviews_and_with_shorts() {
        let args = engine_args(&request(true, 2));

        assert_eq!(args[6..], ["--no-review", "--shorts", "2"]);
    }

    #[test]
    fn the_browser_only_has_to_send_what_it_needs() {
        let req: StartRequest = serde_json::from_str(
            r#"{"video": "/videos/market.mp4", "channel": "mychannel", "name": "market"}"#,
        )
        .unwrap();

        assert_eq!(req, request(false, 0));
    }

    fn with(name: &str, channel: &str, video: &str) -> StartRequest {
        StartRequest {
            video: video.into(),
            channel: channel.into(),
            name: name.into(),
            no_review: false,
            shorts: 0,
        }
    }

    #[test]
    fn a_good_request_passes_the_checks() {
        let video = tempfile::NamedTempFile::new().unwrap();
        let path = video.path().to_str().unwrap();

        assert_eq!(with("market-2026.11", "mychannel", path).validate(), Ok(()));
        assert_eq!(with("пазар_1", "канал", path).validate(), Ok(())); // any language's letters
    }

    #[test]
    fn names_that_could_escape_their_folder_or_pass_for_options_are_refused() {
        let video = tempfile::NamedTempFile::new().unwrap();
        let path = video.path().to_str().unwrap();
        let too_long = "a".repeat(65);

        for bad in [
            "../escape",
            "a/b",
            "..",
            ".hidden",
            "-x",
            "--redo",
            "",
            "a b",
            &too_long,
        ] {
            assert!(
                with(bad, "mychannel", path).validate().is_err(),
                "name {bad:?}"
            );
            assert!(
                with("market", bad, path).validate().is_err(),
                "channel {bad:?}"
            );
        }
    }

    #[test]
    fn the_video_must_be_an_existing_file() {
        let folder = tempfile::tempdir().unwrap();

        assert!(with("m", "c", "/no/such/video.mp4").validate().is_err());
        assert!(with("m", "c", "--help").validate().is_err());
        assert!(
            with("m", "c", folder.path().to_str().unwrap())
                .validate()
                .is_err()
        );
    }

    #[test]
    fn a_request_without_a_name_is_refused() {
        let req = serde_json::from_str::<StartRequest>(
            r#"{"video": "/videos/market.mp4", "channel": "mychannel"}"#,
        );

        assert!(req.is_err());
    }
}
