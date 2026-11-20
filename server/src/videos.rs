//! The videos folder (`<home>/videos`): uploads and downloads land there, and runs start from it.

use std::path::Path;

use serde::Serialize;

/// A video in the videos folder.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Video {
    pub name: String,
    /// The path to give POST /api/runs as `video`.
    pub path: String,
    pub bytes: u64,
}

impl Video {
    /// The video at `path`, with its size read from disk.
    pub async fn at(path: &Path) -> std::io::Result<Video> {
        let meta = tokio::fs::metadata(path).await?;
        Ok(Video {
            name: path
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default(),
            path: path.to_string_lossy().into_owned(),
            bytes: meta.len(),
        })
    }
}

/// The videos in `dir`, by name; none if it doesn't exist yet. Hidden ones are left out: half
/// uploads (`.name.part`) and yt-dlp's folder for unfinished downloads (`.partial`).
pub async fn list(dir: &Path) -> std::io::Result<Vec<Video>> {
    let mut entries = match tokio::fs::read_dir(dir).await {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e),
    };
    let mut videos = Vec::new();
    while let Some(entry) = entries.next_entry().await? {
        let name = entry.file_name().to_string_lossy().into_owned();
        let meta = entry.metadata().await?;
        if meta.is_file() && !name.starts_with('.') {
            let path = entry.path().to_string_lossy().into_owned();
            videos.push(Video {
                name,
                path,
                bytes: meta.len(),
            });
        }
    }
    videos.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(videos)
}

/// A name that passes `start::is_safe_file_name`, made from any file name the way the page cleans
/// an upload's name: other characters become '-'. "My Video (1).MP4" → "My-Video-1.mp4".
pub fn safe_file_name(name: &str) -> String {
    let (stem, ext) = match name.rsplit_once('.') {
        Some((stem, ext)) if !stem.is_empty() => (stem, ext),
        _ => (name, ""),
    };
    let ext: String = ext
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .map(|c| c.to_ascii_lowercase())
        .collect();
    let mut clean = String::new();
    for c in stem.chars() {
        if c.is_alphanumeric() || matches!(c, '.' | '_' | '-') {
            clean.push(c);
        } else if !clean.ends_with('-') {
            clean.push('-');
        }
    }
    let clean: String = clean
        .trim_start_matches(|c: char| !c.is_alphanumeric())
        .trim_end_matches('-')
        .chars()
        .take(100)
        .collect();
    let stem = if clean.is_empty() { "video" } else { &clean };
    if ext.is_empty() {
        stem.to_string()
    } else {
        format!("{stem}.{ext}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::start::is_safe_file_name;

    #[test]
    fn any_name_becomes_a_safe_one() {
        for (name, safe) in [
            ("My Video (1).MP4", "My-Video-1.mp4"),
            (
                "yt-abc-Пазарът в събота｜част 2.mp4",
                "yt-abc-Пазарът-в-събота-част-2.mp4",
            ),
            ("--help.mp4", "help.mp4"),
            ("...", "video"),
            ("no extension", "no-extension"),
        ] {
            assert_eq!(safe_file_name(name), safe, "{name}");
            assert!(is_safe_file_name(&safe_file_name(name)), "{name}");
        }
        assert!(is_safe_file_name(&safe_file_name(&"a ".repeat(200))));
    }

    #[tokio::test]
    async fn the_list_leaves_out_hidden_files_and_folders() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("b.mp4"), "bb").unwrap();
        std::fs::write(dir.path().join("a.mp4"), "a").unwrap();
        std::fs::write(dir.path().join(".a.mp4.part"), "half").unwrap();
        std::fs::create_dir(dir.path().join(".partial")).unwrap();

        let names: Vec<_> = list(dir.path())
            .await
            .unwrap()
            .into_iter()
            .map(|v| (v.name, v.bytes))
            .collect();

        assert_eq!(names, [("a.mp4".to_string(), 1), ("b.mp4".to_string(), 2)]);
        assert!(list(&dir.path().join("none")).await.unwrap().is_empty());
    }
}
