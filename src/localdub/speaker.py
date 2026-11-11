"""Speaker fingerprints: how much a voice sounds like him.

A speaker-verification model turns a few seconds of speech into a vector (an "embedding"); recordings
of the same person point in nearly the same direction, so the cosine similarity of two vectors says
how alike two voices are (1.0 = identical). It is trained to ignore what is being said and in which
language, which is what makes it useful here: his Bulgarian speech in the video and the English
clone can be compared directly.

localdub builds his voiceprint from his own speech in the video (sentences cut from the separated
vocals, averaged), then uses it to
  - choose the voice sample: clone a few test sentences from each candidate sample, keep the sample
    whose clones sound most like him;
  - choose between takes of a line: among takes that pass the check and fit, keep the most similar.

The model is WavLM x-vectors (microsoft/wavlm-base-plus-sv) through `transformers`, installed with
`pip install -e .[voice]`. Without it localdub still works and falls back to the cleanest sample.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

RATE = 16000
CALIBRATION = {  # sentences each candidate voice sample is cloned with, per target language
    "en": [
        "Hi everyone, welcome back to the channel, today we have something really fun for you.",
        "I honestly did not expect it to be this cheap, but here we are.",
        "So we packed our bags, grabbed the camera, and went straight to the city centre.",
        "Let me know in the comments what you would have done in my place.",
    ],
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    return round(float(np.dot(unit(a), unit(b))), 4)


class WavLMEmbedder:
    """x-vectors from microsoft/wavlm-base-plus-sv (or another WavLMForXVector model)."""

    def __init__(self, model: str, device: str):
        import torch
        from transformers import AutoFeatureExtractor, WavLMForXVector
        self.torch = torch
        self.device = device
        self.fe = AutoFeatureExtractor.from_pretrained(model)
        self.model = WavLMForXVector.from_pretrained(model).to(device).eval()

    def embed(self, clips: list[np.ndarray], batch: int = 8) -> list[np.ndarray]:
        out = []
        for i in range(0, len(clips), batch):
            part = [c if len(c) >= RATE // 2 else np.pad(c, (0, RATE // 2 - len(c))) for c in clips[i:i + batch]]
            inputs = self.fe(part, sampling_rate=RATE, return_tensors="pt", padding=True)
            with self.torch.no_grad():
                emb = self.model(**{k: v.to(self.device) for k, v in inputs.items()}).embeddings
            out += [unit(e) for e in emb.cpu().numpy()]
        return out


class SpectralEmbedder:
    """For the tests only: a crude average spectrum, so that clips of the same tone come out alike.
    It is not a speaker model; never use it for real videos."""

    def embed(self, clips: list[np.ndarray], batch: int = 8) -> list[np.ndarray]:
        out = []
        for c in clips:
            n = 1024
            frames = [c[i:i + n] for i in range(0, max(1, len(c) - n), n // 2)] or [np.pad(c, (0, n - len(c)))]
            spec = np.mean([np.abs(np.fft.rfft(np.pad(f, (0, n - len(f))))) for f in frames], axis=0)
            bands = np.add.reduceat(spec, np.linspace(0, len(spec) - 1, 33).astype(int)[:-1])
            out.append(unit(np.log1p(bands)))
        return out


def get_embedder(settings: dict):
    """The configured speaker model, or None (with the reason logged) if it can't be loaded."""
    cfg = settings.get("speaker", {})
    if not cfg.get("enabled", True):
        return None
    model = cfg.get("model", "microsoft/wavlm-base-plus-sv")
    if model == "test-spectral":
        return SpectralEmbedder()
    try:
        return WavLMEmbedder(model, cfg.get("device", "cpu"))
    except ImportError:
        log("   speaker similarity off: install it with  pip install -e .[voice]")
    except Exception as e:  # noqa: BLE001  (download or load failure)
        log(f"   speaker similarity off: {e}")
    return None


def embed_files(embedder, paths: list[Path]) -> list[np.ndarray]:
    from .voice import read_audio
    return embedder.embed([read_audio(p, RATE) for p in paths])


def voiceprint(embedder, segs: list[dict], vocals: Path, max_clips: int = 24) -> np.ndarray | None:
    """His average fingerprint from up to `max_clips` sentences of 2-8 s spread over the video."""
    from .voice import read_audio
    audio = read_audio(vocals, RATE)
    good = [s for s in segs if 2.0 <= s["end"] - s["start"] <= 8.0 and not s.get("flags")]
    if not good:
        good = [s for s in segs if s["end"] - s["start"] >= 1.0]
    if not good:
        return None
    step = max(1, len(good) // max_clips)
    clips = [audio[int(s["start"] * RATE):int(s["end"] * RATE)] for s in good[::step][:max_clips]]
    clips = [c for c in clips if len(c) > RATE // 2]
    if not clips:
        return None
    return unit(np.mean(embedder.embed(clips), axis=0))
