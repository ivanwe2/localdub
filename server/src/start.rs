//! Starting the engine: what the browser sends, the checks it must pass, and the engine commands
//! it turns into (a new run, continuing one, making Shorts).

use std::path::Path;

use serde::Deserialize;

/// The body of POST /api/runs.
#[derive(Debug, PartialEq, Deserialize)]
pub struct StartRequest {
    /// The video file: a path inside the localdub home folder (uploads go to its `videos/`).
    pub video: String,
    /// The channel whose profile the run uses (and, with reviews, teaches).
    pub channel: String,
    /// The run's name, which is also its folder in runs/.
    pub name: String,
    /// Skip both reviews; nothing is learned from the run.
    #[serde(default)]
    pub no_review: bool,
    /// When the dub is done, also make the top N Shorts in both languages (0 = none). With
    /// `shorts_only`: render the top N Shorts (0 = wait for a pick).
    #[serde(default)]
    pub shorts: u32,
    /// No dub: the transcript, then Shorts in the video's language.
    #[serde(default)]
    pub shorts_only: bool,
    /// A new channel's languages: the video's and the dub's, as codes ("bg", "en"). Required for a
    /// channel that doesn't exist yet (only the video's for Shorts only); for an existing one the
    /// engine checks they match.
    pub source: Option<String>,
    pub target: Option<String>,
    /// Optional for a new channel: "man" or "woman", for the translation's grammar.
    pub speaker: Option<String>,
}

impl StartRequest {
    /// Checks the request before anything runs; the error says what's wrong. The values become
    /// engine arguments and folder names, so: a name and channel can't reach outside their folder
    /// or pass for an engine option, and the video must be an existing file inside `home`.
    pub fn validate(&self, home: &Path) -> Result<(), String> {
        if !is_safe_name(&self.name) {
            return Err(format!("name {:?}: {NAME_RULE}", self.name));
        }
        if !is_safe_name(&self.channel) {
            return Err(format!("channel {:?}: {NAME_RULE}", self.channel));
        }
        let video = Path::new(&self.video);
        if self.video.starts_with('-') || !video.is_file() {
            return Err(format!("video {:?}: no such file", self.video));
        }
        if !is_inside(video, home) {
            return Err(format!(
                "video {:?}: only videos inside {} can be dubbed (upload it, or put it there)",
                self.video,
                home.display()
            ));
        }
        for (what, code) in [("video", &self.source), ("dub", &self.target)] {
            if let Some(code) = code
                && !is_language_code(code)
            {
                return Err(format!(
                    "{what} language {code:?}: a language code like \"bg\" or \"en\""
                ));
            }
        }
        if let Some(speaker) = &self.speaker
            && !matches!(speaker.as_str(), "man" | "woman")
        {
            return Err(format!("speaker {speaker:?}: \"man\" or \"woman\""));
        }
        let profile = home
            .join("channels")
            .join(&self.channel)
            .join("profile.json");
        if !profile.is_file()
            && (self.source.is_none() || (self.target.is_none() && !self.shorts_only))
        {
            return Err(format!(
                "channel {:?} is new: choose the video's language{}",
                self.channel,
                if self.shorts_only {
                    ""
                } else {
                    " and the dub's"
                }
            ));
        }
        Ok(())
    }
}

/// An ISO 639 code as the engine's table has them: two or three lowercase letters.
fn is_language_code(s: &str) -> bool {
    (2..=3).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_lowercase())
}

/// The body of POST /api/runs/{name}/shorts.
#[derive(Debug, PartialEq, Deserialize)]
pub struct ShortsRequest {
    pub lang: ShortsLang,
}

/// Which audio and captions the Shorts get.
#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ShortsLang {
    /// The dubbed video, captions in the dubbed language.
    Dub,
    /// The original video, captions in its own language.
    Original,
}

impl ShortsLang {
    pub fn as_str(self) -> &'static str {
        match self {
            ShortsLang::Dub => "dub",
            ShortsLang::Original => "original",
        }
    }
}

/// The body of POST /api/runs/{name}/lines/{id}: new words for one line of a finished dub (empty:
/// the line stays silent), or another take of the words it has, or both.
#[derive(Debug, Default, PartialEq, Deserialize)]
pub struct LineRequest {
    /// The line's new words in the dub language; None keeps them.
    #[serde(default)]
    pub text: Option<String>,
    /// Voice it again with new seeds.
    #[serde(default)]
    pub new_take: bool,
}

/// The longest line the page may send, in characters (a dub line is a sentence or two).
pub const MAX_LINE_CHARS: usize = 1000;

impl LineRequest {
    /// The engine's arguments, `line <run> <id> [--text-stdin] [--new-take]`, and the words for its
    /// stdin. Words a person typed never go on the command line: they may start with '-' and would
    /// read as an option.
    pub fn engine_args(&self, run: &str, id: u32) -> Result<(Vec<String>, Option<String>), String> {
        let mut args = vec!["line".to_string(), run.to_string(), id.to_string()];
        let words = match &self.text {
            Some(text) if text.chars().any(|c| c.is_control() && !c.is_whitespace()) => {
                return Err("the line's words have control characters in them".to_string());
            }
            Some(text) => {
                // one line, as the engine reads it: every run of spaces or line breaks becomes a space
                let one_line = text.split_whitespace().collect::<Vec<_>>().join(" ");
                if one_line.chars().count() > MAX_LINE_CHARS {
                    let msg = format!("a line has at most {MAX_LINE_CHARS} characters");
                    return Err(msg);
                }
                args.push("--text-stdin".to_string());
                Some(one_line)
            }
            None => None,
        };
        if self.new_take {
            args.push("--new-take".to_string());
        }
        if words.is_none() && !self.new_take {
            return Err(
                "give the line new words (text) or ask for a new take (new_take)".to_string(),
            );
        }
        Ok((args, words))
    }
}

pub const NAME_RULE: &str =
    "use letters, digits, '.', '_' and '-', starting with a letter or digit, at most 64 characters";

/// A run or channel name that is safe as a folder name and as an argument: no '/', no leading '-'
/// or '.', so no "..", nothing the engine would read as an option.
pub fn is_safe_name(s: &str) -> bool {
    safe(s, 64)
}

/// The same rule for an uploaded video's file name, which may be a little longer.
pub fn is_safe_file_name(s: &str) -> bool {
    safe(s, 128)
}

fn safe(s: &str, max: usize) -> bool {
    let mut chars = s.chars();
    let starts_well = chars.next().is_some_and(char::is_alphanumeric);
    starts_well
        && s.chars().count() <= max
        && chars.all(|c| c.is_alphanumeric() || matches!(c, '.' | '_' | '-'))
}

/// Is `path` inside `folder`, after resolving `..` and symbolic links? False if either is missing.
pub fn is_inside(path: &Path, folder: &Path) -> bool {
    match (path.canonicalize(), folder.canonicalize()) {
        (Ok(path), Ok(folder)) => path.starts_with(folder),
        _ => false,
    }
}

/// The engine's arguments for a new run, without the program itself and without `--json`
/// (run_engine adds that):
/// `run <video> --channel <channel> --name <name>`, then `--shorts-only`, `--no-review` and
/// `--shorts N` if asked for.
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
    if req.shorts_only {
        args.push("--shorts-only".to_string());
    }
    if req.no_review {
        args.push("--no-review".to_string());
    }
    if req.shorts > 0 {
        args.push("--shorts".to_string());
        args.push(req.shorts.to_string());
    }
    for (flag, value) in [
        ("--from", &req.source),
        ("--to", &req.target),
        ("--speaker", &req.speaker),
    ] {
        if let Some(value) = value {
            args.push(flag.to_string());
            args.push(value.clone());
        }
    }
    args
}

/// Continuing a run where it stopped (after a review, or an error): `run <name>`.
pub fn continue_args(run: &str) -> Vec<String> {
    vec!["run".to_string(), run.to_string()]
}

/// Making voice samples from a run's speech to compare: `voice <name>`.
pub fn voice_args(run: &str) -> Vec<String> {
    vec!["voice".to_string(), run.to_string()]
}

/// Making one of them (or "current") the channel's voice: `voice <name> --pick <pick>`.
pub fn pick_args(run: &str, pick: &str) -> Vec<String> {
    vec![
        "voice".to_string(),
        run.to_string(),
        "--pick".to_string(),
        pick.to_string(),
    ]
}

/// Dubbing a finished run again with the channel's (new) voice: `run <name> --redo voice`.
pub fn revoice_args(run: &str) -> Vec<String> {
    vec![
        "run".to_string(),
        run.to_string(),
        "--redo".to_string(),
        "voice".to_string(),
    ]
}

/// Making (or, once picked, rendering) a finished run's Shorts: `shorts <name> --lang <lang>`.
pub fn shorts_args(run: &str, lang: &str) -> Vec<String> {
    vec![
        "shorts".to_string(),
        run.to_string(),
        "--lang".to_string(),
        lang.to_string(),
    ]
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
            shorts_only: false,
            source: None,
            target: None,
            speaker: None,
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
    fn continuing_and_making_shorts() {
        assert_eq!(continue_args("market"), ["run", "market"]);
        assert_eq!(
            shorts_args("market", ShortsLang::Original.as_str()),
            ["shorts", "market", "--lang", "original"]
        );
    }

    #[test]
    fn a_fixed_line_sends_its_words_on_stdin() {
        let words = LineRequest {
            text: Some("  --not an option,\n\tjust words ".into()),
            new_take: false,
        };
        let take = LineRequest {
            text: None,
            new_take: true,
        };
        let silent = LineRequest {
            text: Some(String::new()),
            new_take: false,
        };

        let (args, input) = words.engine_args("market", 31).unwrap();
        assert_eq!(args, ["line", "market", "31", "--text-stdin"]);
        assert_eq!(input.as_deref(), Some("--not an option, just words"));
        let (args, input) = take.engine_args("market", 31).unwrap();
        assert_eq!((args.last().unwrap().as_str(), input), ("--new-take", None));
        assert_eq!(
            silent.engine_args("market", 2).unwrap().1.as_deref(),
            Some("")
        );
        assert!(
            LineRequest::default().engine_args("market", 1).is_err(),
            "nothing to do"
        );
        let long = LineRequest {
            text: Some("a".repeat(MAX_LINE_CHARS + 1)),
            new_take: false,
        };
        let control = LineRequest {
            text: Some("bell\u{7}".into()),
            new_take: false,
        };
        assert!(long.engine_args("market", 1).is_err());
        assert!(control.engine_args("market", 1).is_err());
    }

    #[test]
    fn voice_samples_picks_and_dubbing_again() {
        assert_eq!(voice_args("market"), ["voice", "market"]);
        assert_eq!(pick_args("market", "2"), ["voice", "market", "--pick", "2"]);
        assert_eq!(revoice_args("market"), ["run", "market", "--redo", "voice"]);
    }

    #[test]
    fn the_browser_only_has_to_send_what_it_needs() {
        let req: StartRequest = serde_json::from_str(
            r#"{"video": "/videos/market.mp4", "channel": "mychannel", "name": "market"}"#,
        )
        .unwrap();

        assert_eq!(req, request(false, 0));
    }

    #[test]
    fn shorts_come_in_two_languages_only() {
        let dub: ShortsRequest = serde_json::from_str(r#"{"lang": "dub"}"#).unwrap();

        assert_eq!(dub.lang, ShortsLang::Dub);
        assert!(serde_json::from_str::<ShortsRequest>(r#"{"lang": "--help"}"#).is_err());
    }

    fn with(name: &str, channel: &str, video: &str) -> StartRequest {
        StartRequest {
            video: video.into(),
            channel: channel.into(),
            name: name.into(),
            no_review: false,
            shorts: 0,
            shorts_only: false,
            source: Some("bg".into()),
            target: Some("en".into()),
            speaker: None,
        }
    }

    /// A home folder with one video in it; returns (home, the video's path).
    fn home_with_video() -> (tempfile::TempDir, String) {
        let home = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(home.path().join("videos")).unwrap();
        let video = home.path().join("videos").join("market.mp4");
        std::fs::write(&video, "not really a video").unwrap();
        (home, video.to_str().unwrap().to_string())
    }

    #[test]
    fn a_good_request_passes_the_checks() {
        let (home, video) = home_with_video();

        assert_eq!(
            with("market-2026.11", "mychannel", &video).validate(home.path()),
            Ok(())
        );
        assert_eq!(
            with("пазар_1", "канал", &video).validate(home.path()),
            Ok(())
        ); // any language's letters
    }

    #[test]
    fn names_that_could_escape_their_folder_or_pass_for_options_are_refused() {
        let (home, video) = home_with_video();
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
                with(bad, "mychannel", &video)
                    .validate(home.path())
                    .is_err(),
                "name {bad:?}"
            );
            assert!(
                with("market", bad, &video).validate(home.path()).is_err(),
                "channel {bad:?}"
            );
        }
    }

    #[test]
    fn the_video_must_be_an_existing_file_inside_the_home_folder() {
        let (home, _) = home_with_video();
        let elsewhere = tempfile::NamedTempFile::new().unwrap();
        let sneaky = home.path().join("videos").join("..").join("..").join("etc");

        for video in [
            "/no/such/video.mp4",
            "--help",
            home.path().to_str().unwrap(), // a folder
            elsewhere.path().to_str().unwrap(),
            sneaky.to_str().unwrap(),
        ] {
            assert!(
                with("m", "c", video).validate(home.path()).is_err(),
                "{video}"
            );
        }
    }

    #[test]
    fn uploaded_file_names_follow_the_same_rule() {
        assert!(!is_safe_file_name("Market day (1).mp4")); // the page cleans names like this first
        assert!(is_safe_file_name("Market-day-1.mp4"));
        assert!(is_safe_file_name("Пазар.mkv"));
        assert!(!is_safe_file_name("../x.mp4"));
        assert!(!is_safe_file_name(".hidden.mp4"));
        assert!(!is_safe_file_name(&format!("{}.mp4", "a".repeat(130))));
    }

    #[test]
    fn a_new_channels_languages_and_speaker_become_engine_flags() {
        let mut req = request(true, 0);
        req.source = Some("de".into());
        req.target = Some("es".into());
        req.speaker = Some("woman".into());

        assert_eq!(
            engine_args(&req)[6..],
            [
                "--no-review",
                "--from",
                "de",
                "--to",
                "es",
                "--speaker",
                "woman"
            ]
        );
    }

    #[test]
    fn a_new_channel_needs_its_languages_and_an_existing_one_does_not() {
        let (home, video) = home_with_video();
        let mut req = with("market", "newchannel", &video);
        req.target = None;

        let refused = req.validate(home.path());
        let channel = home.path().join("channels").join("newchannel");
        std::fs::create_dir_all(&channel).unwrap();
        std::fs::write(channel.join("profile.json"), "{}").unwrap();

        assert!(
            refused
                .unwrap_err()
                .contains("is new: choose the video's language")
        );
        assert_eq!(req.validate(home.path()), Ok(()));
    }

    #[test]
    fn shorts_only_needs_only_the_video_language() {
        let (home, video) = home_with_video();
        let mut req = with("market", "clips", &video);
        req.target = None;
        req.shorts_only = true;
        req.shorts = 2;

        assert_eq!(req.validate(home.path()), Ok(()));
        assert_eq!(
            engine_args(&req)[6..],
            ["--shorts-only", "--shorts", "2", "--from", "bg"]
        );
        req.source = None;
        assert!(
            req.validate(home.path())
                .unwrap_err()
                .ends_with("is new: choose the video's language")
        );
    }

    #[test]
    fn languages_and_speakers_must_have_the_right_shape() {
        let (home, video) = home_with_video();

        for (source, target, speaker) in [
            ("--help", "en", None),
            ("bg", "EN", None),
            ("bg", "english", None),
            ("bg", "", None),
            ("bg", "en", Some("other")),
            ("bg", "en", Some("")),
        ] {
            let mut req = with("market", "newchannel", &video);
            req.source = Some(source.into());
            req.target = Some(target.into());
            req.speaker = speaker.map(Into::into);
            assert!(
                req.validate(home.path()).is_err(),
                "{source} {target} {speaker:?}"
            );
        }
    }

    #[test]
    fn a_request_without_a_name_is_refused() {
        let req = serde_json::from_str::<StartRequest>(
            r#"{"video": "/videos/market.mp4", "channel": "mychannel"}"#,
        );

        assert!(req.is_err());
    }
}
