"""The dub's captions and subtitles follow the times the take check heard each word."""
import re

from localdub.mix import subtitles
from localdub.shorts import captions
from localdub.voice import script_times


def heard(*words):
    """Words as the recogniser returns them: (word, start, end)."""
    return [{"word": w, "start": a, "end": b} for w, a, b in words]


def test_the_script_takes_the_times_of_what_was_heard():
    got = script_times("A kilo is twelve leva.", heard(("a", 0.0, 0.1), ("kilo", 0.15, 0.5), ("is", 0.55, 0.7),
                                                       ("twelve", 0.75, 1.1), ("lever", 1.2, 1.6)), "en", 1.8)

    assert [w["word"] for w in got] == ["A", "kilo", "is", "twelve", "leva."]  # the script's words, as written
    assert [(w["start"], w["end"]) for w in got] == [(0.0, 0.1), (0.15, 0.5), (0.55, 0.7), (0.75, 1.1), (1.2, 1.6)]


def test_numbers_match_spelled_out_and_a_missed_word_fits_in_between():
    spelled = script_times("It costs 25 leva", heard(("it", 0.0, 0.2), ("costs", 0.3, 0.6), ("twenty", 0.7, 1.0),
                                                     ("five", 1.0, 1.3), ("leva", 1.4, 1.8)), "en", 2.0)
    assert spelled[2] == {"word": "25", "start": 0.7, "end": 1.3}

    missed = script_times("We buy two jars", heard(("we", 0.0, 0.2), ("two", 1.0, 1.2), ("jars", 1.3, 1.7)), "en", 2.0)
    assert missed[1] == {"word": "buy", "start": 0.2, "end": 1.0}
    nothing = script_times("Hello there", [], "en", 2.0)  # nothing heard: shared out by length
    assert nothing[0]["start"] == 0.0 and nothing[-1]["end"] == 2.0


def line(**take):
    return {"id": 1, "start": 10.0, "end": 14.0, "src": "Килограмът е дванадесет лева.",
            "tgt": "A kilo is twelve leva.", "placed": 10.5,
            "words": [{"word": "Килограмът", "start": 10.0, "end": 11.0}],
            "take": {"seconds": 2.0, "ok": True, **take}}


def times(ass: str) -> list[tuple[str, str, str]]:
    return re.findall(r"Dialogue: 0,([\d:.]+),([\d:.]+),Cap,,0,0,0,,(.*)", ass)


def test_dub_captions_in_a_short_start_when_their_words_are_spoken():
    words = heard(("a", 0.0, 0.1), ("kilo", 0.15, 0.5), ("is", 0.55, 0.7), ("twelve", 0.75, 1.1), ("leva", 1.2, 1.6))

    timed = times(captions([line(words=words)], 10.0, 4.0, "", "dub", "en"))
    shared = times(captions([line()], 10.0, 4.0, "", "dub", "en"))  # a take checked before word times

    assert timed == [("0:00:00.50", "0:00:01.25", "A kilo is"), ("0:00:01.25", "0:00:02.40", "twelve leva.")]
    assert shared[0][0] == "0:00:00.50" and shared != timed


def test_dub_subtitles_follow_the_words_too(tmp_path):
    words = heard(("a", 0.0, 0.1), ("kilo", 0.15, 0.5), ("is", 0.55, 0.7), ("twelve", 0.75, 1.1), ("leva", 1.2, 1.6))
    subtitles([line(words=words)], tmp_path / "x.bg.srt", tmp_path / "x.en.srt", "en")

    assert (tmp_path / "x.en.srt").read_text(encoding="utf-8").splitlines()[:3] == [
        "1", "00:00:10,500 --> 00:00:12,100", "A kilo is twelve leva."]
