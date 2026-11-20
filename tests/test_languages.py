"""Languages: the table, a channel's languages and speaker, and the `languages` / `channel` commands."""
import json
from argparse import Namespace

import pytest

from localdub import languages
from localdub.settings import DEFAULT_SETTINGS, Profile
from localdub.text import speaker_note


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALDUB_HOME", str(tmp_path))
    return tmp_path


def flags(source=None, target=None, speaker=None):
    return Namespace(source=source, target=target, speaker=speaker)


def events_of(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_the_table_knows_its_languages_and_says_which_it_doesnt():
    assert languages.get("de").name == "German" and languages.get("de").hunspell == "de_DE"
    with pytest.raises(ValueError, match="unknown language 'xx'.*de \\(German\\)"):
        languages.get("xx")


def test_every_dub_language_has_what_dubbing_needs():
    for code in languages.dub_languages(DEFAULT_SETTINGS):
        lang = languages.get(code)
        assert len(lang.calibration) == 4, code  # test sentences for scoring voice samples
        assert lang.numbers and lang.percent and lang.iso3 and lang.hunspell and lang.apt, code


def test_what_can_be_dubbed_into_is_a_setting():
    assert languages.dub_languages({"tts_languages": ["ru", "en", "zz"]}) == ["en", "ru"]  # table order


def test_a_new_channel_gets_its_languages_from_the_flags(home):
    from localdub.run import channel_profile

    profile = channel_profile("market", flags("de", "es", "woman"), DEFAULT_SETTINGS)

    saved = json.loads((home / "channels" / "market" / "profile.json").read_text(encoding="utf-8"))
    assert (saved["source_lang"], saved["target_lang"]) == ("de", "es")
    assert (saved["source_language_name"], saved["target_language_name"]) == ("German", "Spanish")
    assert saved["hunspell_dict"] == "de_DE" and saved["speaker"] == "woman"
    assert profile["target_lang"] == "es"


@pytest.mark.parametrize("given, why", [
    (flags(), "is new: give the video's language"),
    (flags("de", "de"), "from one language into another"),
    (flags("de", "bg"), "the voice model can't speak Bulgarian"),
    (flags("xx", "en"), "unknown language 'xx'"),
])
def test_a_new_channel_without_usable_languages_is_refused(home, given, why):
    from localdub.run import channel_profile

    result = channel_profile("market", given, DEFAULT_SETTINGS)

    assert isinstance(result, str) and why in result
    assert not (home / "channels" / "market" / "profile.json").exists(), "nothing saved"


def test_an_existing_channel_keeps_its_languages(home):
    from localdub.run import channel_profile
    channel_profile("market", flags("de", "es"), DEFAULT_SETTINGS)

    again = channel_profile("market", flags(), DEFAULT_SETTINGS)
    other = channel_profile("market", flags("de", "fr"), DEFAULT_SETTINGS)

    assert again["target_lang"] == "es"
    assert isinstance(other, str) and "dubs de → es; for de → fr use another channel name" in other


def test_a_profile_from_before_languages_is_bulgarian_to_english(home):
    channel = home / "channels" / "old"
    channel.mkdir(parents=True)
    (channel / "profile.json").write_text(json.dumps({"names": {"Митев": "Mitev"}, "voice_ref": "voice.wav"}))

    profile = Profile("old")

    assert (profile["source_lang"], profile["target_lang"], profile["hunspell_dict"]) == ("bg", "en", "bg_BG")
    assert profile["names"] == {"Митев": "Mitev"} and profile["speaker"] == ""


def test_the_speaker_only_goes_into_the_translation_prompt():
    assert speaker_note("woman").startswith("The speaker is a woman: use the matching grammatical gender")
    assert speaker_note("") == "" and speaker_note("other") == ""


def test_the_languages_command_lists_the_table(home, capsys):
    from localdub.run import main

    assert main(["languages", "--json"]) == 0

    listed = {row["code"]: row for row in events_of(capsys)[0]["languages"]}
    assert listed["de"]["dub"] is True and listed["bg"]["dub"] is False
    assert listed["bg"]["dictionary"] == "bg_BG" and listed["de"]["apt"] == "hunspell-de-de"
    assert set(listed) == set(languages.LANGUAGES)


def test_the_languages_command_tells_install_scripts_which_dictionaries_to_install(home, capsys):
    from localdub.run import channel_profile, main
    channel_profile("market", flags("ru", "en"), DEFAULT_SETTINGS)

    assert main(["languages", "--apt", "de", "bg"]) == 0

    assert capsys.readouterr().out.split() == ["hunspell-bg", "hunspell-de-de", "hunspell-ru"]


def test_the_channel_command_shows_and_changes_the_speaker(home, capsys):
    from localdub.run import channel_profile, main
    channel_profile("market", flags("de", "es"), DEFAULT_SETTINGS)

    assert main(["channel", "market", "--speaker", "man", "--json"]) == 0
    shown = events_of(capsys)[0]
    assert main(["channel", "nobody", "--json"]) == 1

    assert shown["event"] == "channel" and shown["speaker"] == "man" and shown["target"] == "es"
    assert Profile("market")["speaker"] == "man"


def test_a_media_language_tag_is_iso_639_2():
    from localdub.run import iso3

    assert (iso3("de"), iso3("bg"), iso3("zz")) == ("deu", "bul", "und")


def test_the_prompts_take_their_examples_from_the_language():
    from localdub.text import number_example, spelling_hint

    assert number_example("en") == '"two hundred thousand", not "200,000"'  # what the prompt always said
    assert number_example("de") == '"zweihunderttausend", not "200.000"'
    assert number_example("bg") == "not as digits"
    assert "фсички" in spelling_hint("bg") and spelling_hint("de") == ""


def test_caption_chunks_keep_abbreviations_of_any_language_whole():
    from localdub.shorts import _chunks

    assert _chunks(["Dr.", "Ivanov", "came."]) == [[0, 1, 2]]
    assert _chunks(["ул.", "Шипка"]) == [[0, 1]]
    assert _chunks(["Sra.", "López"]) == [[0, 1]]
    assert _chunks(["Go.", "Now."]) == [[0], [1]]


def test_a_run_without_its_dictionary_stops_before_any_gpu_work(home, monkeypatch, capsys):
    from localdub import run as run_module
    monkeypatch.setattr(run_module, "dictionary_installed", lambda name: name != "de_DE")
    video = home / "talk.mp4"
    video.write_bytes(b"not decoded before the check")

    rc = run_module.main(["run", str(video), "--channel", "market", "--from", "de", "--to", "en", "--name", "talk"])

    assert rc == 1
    assert "German spelling dictionary (de_DE) isn't installed: apt-get install hunspell-de-de" in capsys.readouterr().err
    assert not (home / "runs" / "talk" / "state.json").exists(), "no run was made"


def test_a_language_without_a_dictionary_skips_spelling_fixes():
    from localdub.text import Speller, spellcheck

    class NoCalls:
        def chat_json(self, *args, **kwargs):
            raise AssertionError("no spelling request without a dictionary")

    profile = {"names": {}, "accepted_words": [], "corrections": {"Wellt": "Welt"}, "hunspell_dict": "",
               "source_language_name": "Klingon", "source_lang": "tlh"}
    segs = [{"id": 1, "words": [{"word": "Hallo"}, {"word": "Wellt."}]}]

    report = spellcheck(segs, NoCalls(), DEFAULT_SETTINGS, profile)

    assert Speller("", []).unknown(["anything"]) == set()
    assert segs[0]["src"] == "Hallo Welt." and report[1]["learned"] == [("Wellt.", "Welt.")], "learned fixes still apply"
    assert report[1]["unknown"] == [] and report[1]["fixes"] == []


def test_check_looks_at_every_channels_dictionary(home, monkeypatch, capsys):
    from localdub import run as run_module
    run_module.channel_profile("market", flags("de", "es"), DEFAULT_SETTINGS)
    run_module.channel_profile("kanal", flags("de", "en"), DEFAULT_SETTINGS)
    monkeypatch.setattr(run_module, "dictionary_installed", lambda name: False)

    run_module.main(["check", "--json"])

    checks = {e["what"]: e for e in events_of(capsys) if e.get("event") == "check"}
    dictionary = checks["hunspell de_DE dictionary (channel kanal, market)"]
    assert dictionary["ok"] is False and dictionary["hint"] == "apt-get install hunspell-de-de"
    assert not any("bg_BG" in what for what in checks), "no fixed Bulgarian check any more"
