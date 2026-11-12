"""What the fake Nemotron hears; shared by the fake nemo-speech program (local backend) and the fake
model server (http backend), so both backends behave the same in the tests.

partNN.wav  a stretch of the video: the fixture's words in [offset, offset + duration)
NNNN-hash-sSEED.wav  a voice take: the text that was voiced, with the last word dropped for some
                     lines (a clipped ending, which the take check must catch)
"""
import wave


def wav_seconds(path_or_file) -> float:
    with wave.open(path_or_file) as w:
        return w.getnframes() / w.getframerate()


def part(words: list[dict], offset: float, seconds: float) -> dict:
    ws = [{"word": w["word"], "start": round(w["start"] - offset, 3), "end": round(w["end"] - offset, 3)}
          for w in words if offset <= w["start"] < offset + seconds]
    return {"text": " ".join(w["word"] for w in ws), "words": ws}


def take(stem: str, voiced: str, seconds: float) -> dict:
    i, seed = int(stem[:4]), int(stem.rsplit("-s", 1)[1])
    words = voiced.split()
    if i % 11 == 0 or (seed == 42 and i % 4 == 0):
        words = words[:-1]
    step = seconds / max(1, len(words))
    return {"text": " ".join(words),
            "words": [{"word": w, "start": round(k * step, 3), "end": round((k + 1) * step, 3)} for k, w in enumerate(words)]}
