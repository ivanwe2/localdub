//! A run's sound as a picture for the page's timeline: how loud the speaker's voice (`vocals.wav`)
//! and the music and effects (`music.wav`) are along the video, in a few hundred steps. Both WAVs
//! are what the separation stage left in the run's folder; reading a 30-minute one (about 300 MB)
//! takes well under a second.

use std::fs::File;
use std::io::{BufReader, Read, Seek, SeekFrom};
use std::path::Path;

use serde::Serialize;

/// How many steps a waveform has: plenty for a timeline as wide as a screen.
pub const STEPS: usize = 600;

/// GET /api/runs/{name}/waveform. Each step is 0–100, 100 being the track's loudest step; a
/// track is None until the separation stage has made it.
#[derive(Debug, PartialEq, Serialize)]
pub struct Waveform {
    pub seconds: Option<f64>,
    pub speech: Option<Vec<u8>>,
    pub music: Option<Vec<u8>>,
}

/// The waveform of the run in `dir` (blocking: run it with spawn_blocking).
pub fn of_run(dir: &Path) -> std::io::Result<Waveform> {
    let speech = peaks(&dir.join("vocals.wav"), STEPS)?;
    let music = peaks(&dir.join("music.wav"), STEPS)?;
    let seconds = speech.as_ref().or(music.as_ref()).map(|(s, _)| *s);
    Ok(Waveform {
        seconds,
        speech: speech.map(|(_, p)| p),
        music: music.map(|(_, p)| p),
    })
}

/// The sample formats read here.
#[derive(Clone, Copy)]
enum Sample {
    Int16,
    Int24,
    Int32,
    Float32,
}

/// The WAV at `path`: its length in seconds and its loudest sample in each of `steps` equal
/// stretches, scaled to 0–100. None if there's no such file or it isn't a PCM or float WAV.
pub fn peaks(path: &Path, steps: usize) -> std::io::Result<Option<(f64, Vec<u8>)>> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e),
    };
    let file_len = file.metadata()?.len();
    let mut wav = BufReader::with_capacity(1 << 20, file);
    let mut head = [0u8; 12];
    if wav.read_exact(&mut head).is_err() || &head[..4] != b"RIFF" || &head[8..] != b"WAVE" {
        return Ok(None);
    }

    let (mut format, mut channels, mut rate, mut bits) = (None, 0usize, 0u32, 0u16);
    let mut at = 12u64;
    loop {
        let mut chunk = [0u8; 8];
        if wav.read_exact(&mut chunk).is_err() {
            return Ok(None); // no data chunk
        }
        let size = u32::from_le_bytes([chunk[4], chunk[5], chunk[6], chunk[7]]) as u64;
        at += 8;
        if &chunk[..4] == b"fmt " {
            let mut fmt = vec![0u8; size as usize];
            wav.read_exact(&mut fmt)?;
            if fmt.len() < 16 {
                return Ok(None);
            }
            let mut tag = u16::from_le_bytes([fmt[0], fmt[1]]);
            channels = u16::from_le_bytes([fmt[2], fmt[3]]) as usize;
            rate = u32::from_le_bytes([fmt[4], fmt[5], fmt[6], fmt[7]]);
            bits = u16::from_le_bytes([fmt[14], fmt[15]]);
            if tag == 0xFFFE && fmt.len() >= 26 {
                tag = u16::from_le_bytes([fmt[24], fmt[25]]); // WAVE_FORMAT_EXTENSIBLE: the sub-format
            }
            format = match (tag, bits) {
                (1, 16) => Some(Sample::Int16),
                (1, 24) => Some(Sample::Int24),
                (1, 32) => Some(Sample::Int32),
                (3, 32) => Some(Sample::Float32),
                _ => return Ok(None),
            };
            if size % 2 == 1 {
                wav.seek_relative(1)?;
            }
            at += size + size % 2;
        } else if &chunk[..4] == b"data" {
            let Some(format) = format else {
                return Ok(None); // data before fmt
            };
            // a streamed WAV may not know its size: then it's the rest of the file
            let size = if size == 0 || size == u32::MAX as u64 || at + size > file_len {
                file_len - at
            } else {
                size
            };
            return read_peaks(&mut wav, format, channels, rate, bits, size, steps);
        } else {
            wav.seek(SeekFrom::Current((size + size % 2) as i64))?;
            at += size + size % 2;
        }
    }
}

fn read_peaks(
    wav: &mut impl Read,
    format: Sample,
    channels: usize,
    rate: u32,
    bits: u16,
    size: u64,
    steps: usize,
) -> std::io::Result<Option<(f64, Vec<u8>)>> {
    let width = (bits / 8) as usize;
    let frame = width * channels;
    if frame == 0 || rate == 0 || steps == 0 {
        return Ok(None);
    }
    let frames = size / frame as u64;
    let steps = steps.min(frames as usize).max(1); // a very short sound: one step per frame
    let per_step = frames.div_ceil(steps as u64).max(1);
    let mut loudest = vec![0f32; steps];
    let mut buf = vec![0u8; frame * 16384];
    let mut seen = 0u64;
    let mut left = frames * frame as u64;
    while left > 0 {
        let want = buf.len().min(left as usize);
        let got = read_full(wav, &mut buf[..want])?;
        if got == 0 {
            break;
        }
        let whole = got - got % frame;
        for f in buf[..whole].chunks_exact(frame) {
            let step = ((seen / per_step) as usize).min(steps - 1);
            for s in f.chunks_exact(width) {
                let v = level(format, s);
                if v > loudest[step] {
                    loudest[step] = v;
                }
            }
            seen += 1;
        }
        left -= got as u64;
        if got < want {
            break;
        }
    }
    let top = loudest.iter().cloned().fold(0f32, f32::max);
    let scaled = loudest
        .iter()
        .map(|v| {
            if top > 0.0 {
                (v / top * 100.0).round() as u8
            } else {
                0
            }
        })
        .collect();
    Ok(Some((frames as f64 / rate as f64, scaled)))
}

/// How loud one sample is, 0–1.
fn level(format: Sample, s: &[u8]) -> f32 {
    match format {
        Sample::Int16 => (i16::from_le_bytes([s[0], s[1]]) as f32 / 32768.0).abs(),
        Sample::Int24 => {
            let v = i32::from_le_bytes([0, s[0], s[1], s[2]]) >> 8;
            (v as f32 / 8_388_608.0).abs()
        }
        Sample::Int32 => {
            (i32::from_le_bytes([s[0], s[1], s[2], s[3]]) as f32 / 2_147_483_648.0).abs()
        }
        Sample::Float32 => f32::from_le_bytes([s[0], s[1], s[2], s[3]]).abs().min(1.0),
    }
}

/// Reads until `buf` is full or the file ends; how many bytes came.
fn read_full(r: &mut impl Read, buf: &mut [u8]) -> std::io::Result<usize> {
    let mut n = 0;
    while n < buf.len() {
        match r.read(&mut buf[n..])? {
            0 => break,
            k => n += k,
        }
    }
    Ok(n)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A WAV with these samples (one per frame and channel), as 16-bit PCM or 32-bit float.
    fn write_wav(
        path: &Path,
        rate: u32,
        channels: u16,
        float: bool,
        samples: &[f32],
        extra_chunk: bool,
    ) {
        let width: u16 = if float { 4 } else { 2 };
        let mut data = Vec::new();
        for &v in samples {
            if float {
                data.extend_from_slice(&v.to_le_bytes());
            } else {
                data.extend_from_slice(&((v * 32767.0) as i16).to_le_bytes());
            }
        }
        let mut out = Vec::new();
        out.extend_from_slice(b"RIFF");
        out.extend_from_slice(&0u32.to_le_bytes());
        out.extend_from_slice(b"WAVE");
        out.extend_from_slice(b"fmt ");
        out.extend_from_slice(&16u32.to_le_bytes());
        out.extend_from_slice(&(if float { 3u16 } else { 1u16 }).to_le_bytes());
        out.extend_from_slice(&channels.to_le_bytes());
        out.extend_from_slice(&rate.to_le_bytes());
        out.extend_from_slice(&(rate * (width * channels) as u32).to_le_bytes());
        out.extend_from_slice(&(width * channels).to_le_bytes());
        out.extend_from_slice(&(width * 8).to_le_bytes());
        if extra_chunk {
            out.extend_from_slice(b"LIST");
            out.extend_from_slice(&3u32.to_le_bytes());
            out.extend_from_slice(b"abc\0"); // odd size, padded
        }
        out.extend_from_slice(b"data");
        out.extend_from_slice(&(data.len() as u32).to_le_bytes());
        out.extend_from_slice(&data);
        std::fs::write(path, out).unwrap();
    }

    #[test]
    fn quiet_then_loud_reads_as_low_then_high() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("vocals.wav");
        // 2 s of stereo at 1000 Hz: the first second quiet, the second loud
        let samples: Vec<f32> = (0..4000)
            .map(|i| if i < 2000 { 0.05 } else { 0.8 })
            .collect();
        write_wav(&path, 1000, 2, false, &samples, true);

        let (seconds, steps) = peaks(&path, 10).unwrap().unwrap();

        assert_eq!(seconds, 2.0);
        assert_eq!(steps.len(), 10);
        assert!(steps[..5].iter().all(|&v| v < 10), "{steps:?}");
        assert!(steps[5..].iter().all(|&v| v == 100), "{steps:?}");
    }

    #[test]
    fn float_wavs_and_missing_or_odd_files() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("music.wav");
        let samples: Vec<f32> = (0..1200).map(|i| (i as f32 / 1200.0) * 0.5).collect();
        write_wav(&path, 100, 1, true, &samples, false);
        std::fs::write(dir.path().join("vocals.wav"), "not a wav").unwrap();

        let wave = of_run(dir.path()).unwrap();

        assert_eq!(wave.seconds, Some(12.0));
        assert_eq!(wave.speech, None);
        let music = wave.music.unwrap();
        assert_eq!(music.len(), STEPS);
        assert_eq!(music.last(), Some(&100));
        assert!(music[0] < music[STEPS / 2], "it grows");
        assert_eq!(peaks(&dir.path().join("none.wav"), 10).unwrap(), None);
    }
}
