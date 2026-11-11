import shutil

import pytest

from localdub.llm import parse_json
from localdub.shorts import _chunks
from localdub.text import Speller, build_segments, replace_words, text_of

from fixture import words_with_times


def test_parse_json_repairs():
    assert parse_json('{"a": [1, 2,], "b": "x",}') == {"a": [1, 2], "b": "x"}
    assert parse_json('{"frames": ["a sign saying "FOR RENT" on the door", "kitchen"], "interest": 3}') == \
        {"frames": ['a sign saying "FOR RENT" on the door', "kitchen"], "interest": 3}
    assert parse_json('{"frames": ["x"], interest: 2}') == {"frames": ["x"], "interest": 2}
    assert parse_json("not json") is None


def test_caption_chunks():
    w = "Bravo, Mr. Petrov! We found the cheapest stall in all of Plovdiv.".split()
    assert [" ".join(w[i] for i in g) for g in _chunks(w)] == \
        ["Bravo,", "Mr. Petrov!", "We found the", "cheapest stall in", "all of Plovdiv."]
    w = "Като повечето амбициозни младежи от провинцията".split()
    assert all(len(" ".join(w[i] for i in g)) <= 18 for g in _chunks(w))


def test_sentences_from_word_times():
    segs = build_segments(words_with_times(), 12, 15)
    assert text_of(segs[0]["words"]) == "Днес отиваме на пазара в центъра на града."
    assert all(s["end"] - s["start"] <= 12.5 for s in segs)
    assert all(s["budget"] >= 1 and s["slot"] > 0 for s in segs)


def test_replace_words_keeps_times():
    segs = build_segments(words_with_times(), 12, 15)
    s = segs[2]
    start, end = s["start"], s["end"]
    replace_words(s, text_of(s["words"]).replace("ефтини", "евтини"))
    assert "евтини" in text_of(s["words"]) and s["words"][0]["start"] == start and s["words"][-1]["end"] == end


@pytest.mark.skipif(not shutil.which("hunspell"), reason="hunspell is needed")
def test_speller_checks_hyphen_parts():
    sp = Speller("bg_BG", ["ютубър"])
    unknown = sp.unknown(["най-ефтината", "най-евтината", "ютубър", "къща"])
    assert Speller.is_misspelled("най-ефтината", unknown)
    assert not Speller.is_misspelled("най-евтината", unknown)
    assert not Speller.is_misspelled("ютубър", unknown)
