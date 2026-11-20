//! The channels on disk (`<home>/channels/<name>/profile.json`), for the page's new-run form: which
//! channels exist and what each dubs from and into. A profile from before channels had languages
//! (engine 0.9.0) is read as Bulgarian → English, as the engine reads it.

use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::start::is_safe_name;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Channel {
    pub name: String,
    /// The video's language and the dub's: codes ("bg", "en") and names ("Bulgarian").
    pub source: String,
    pub target: String,
    pub source_name: String,
    pub target_name: String,
    /// "man" / "woman" when set; only the translation's grammar uses it.
    pub speaker: Option<String>,
    /// Has the channel a voice sample yet? (Its first run chooses one.)
    pub has_voice: bool,
}

/// The parts of profile.json read here; serde ignores the rest.
#[derive(Deserialize)]
struct Profile {
    source_lang: Option<String>,
    target_lang: Option<String>,
    source_language_name: Option<String>,
    target_language_name: Option<String>,
    #[serde(default)]
    speaker: String,
    #[serde(default)]
    voice_ref: String,
}

/// Every channel with a readable profile, by name.
pub async fn list_channels(home: &Path) -> std::io::Result<Vec<Channel>> {
    let mut entries = match tokio::fs::read_dir(home.join("channels")).await {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e),
    };
    let mut channels = Vec::new();
    while let Some(entry) = entries.next_entry().await? {
        let name = entry.file_name().to_string_lossy().into_owned();
        if !is_safe_name(&name) {
            continue; // not a folder the engine would make
        }
        if let Some(channel) = read_channel(&entry.path(), name).await {
            channels.push(channel);
        }
    }
    channels.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(channels)
}

/// The channel in `dir`; None if it has no profile (yet) or the profile isn't JSON.
async fn read_channel(dir: &Path, name: String) -> Option<Channel> {
    let text = tokio::fs::read_to_string(dir.join("profile.json"))
        .await
        .ok()?;
    let profile: Profile = serde_json::from_str(&text).ok()?;

    let (source, target, source_name, target_name) = match profile.source_lang {
        // what every profile meant before channels had languages (settings.LEGACY_LANGUAGES)
        None => (
            "bg".into(),
            "en".into(),
            "Bulgarian".into(),
            "English".into(),
        ),
        Some(source) => {
            let target = profile.target_lang.unwrap_or_default();
            let source_name = profile
                .source_language_name
                .unwrap_or_else(|| source.clone());
            let target_name = profile
                .target_language_name
                .unwrap_or_else(|| target.clone());
            (source, target, source_name, target_name)
        }
    };
    let voice = Path::new(&profile.voice_ref);
    let has_voice = !profile.voice_ref.is_empty()
        && tokio::fs::try_exists(dir.join(voice)) // join keeps an absolute path as it is
            .await
            .unwrap_or(false);
    Some(Channel {
        name,
        source,
        target,
        source_name,
        target_name,
        speaker: Some(profile.speaker).filter(|s| !s.is_empty()),
        has_voice,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_profile(home: &Path, name: &str, json: &str) -> std::path::PathBuf {
        let dir = home.join("channels").join(name);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("profile.json"), json).unwrap();
        dir
    }

    #[tokio::test]
    async fn channels_with_their_languages_by_name() {
        let home = tempfile::tempdir().unwrap();
        let market = write_profile(
            home.path(),
            "market",
            r#"{"source_lang": "de", "target_lang": "es", "source_language_name": "German",
                "target_language_name": "Spanish", "speaker": "woman", "voice_ref": "voice.wav"}"#,
        );
        std::fs::write(market.join("voice.wav"), "RIFF").unwrap();
        write_profile(
            home.path(),
            "friend",
            r#"{"names": {}, "voice_ref": "gone.wav"}"#,
        );

        let channels = list_channels(home.path()).await.unwrap();

        assert_eq!(channels.len(), 2);
        let (friend, market) = (&channels[0], &channels[1]);
        assert_eq!(
            (
                friend.name.as_str(),
                friend.source.as_str(),
                friend.target.as_str()
            ),
            ("friend", "bg", "en"),
            "a profile from before languages is Bulgarian → English"
        );
        assert!(!friend.has_voice, "its voice file is missing");
        assert_eq!(friend.speaker, None);
        assert_eq!(
            (market.source_name.as_str(), market.target_name.as_str()),
            ("German", "Spanish")
        );
        assert_eq!(market.speaker.as_deref(), Some("woman"));
        assert!(market.has_voice);
    }

    #[tokio::test]
    async fn no_channels_folder_no_channels_and_broken_profiles_are_left_out() {
        let home = tempfile::tempdir().unwrap();
        assert!(list_channels(home.path()).await.unwrap().is_empty());

        write_profile(home.path(), "broken", "{not json");
        std::fs::create_dir_all(home.path().join("channels").join("empty")).unwrap();

        assert!(list_channels(home.path()).await.unwrap().is_empty());
    }
}
