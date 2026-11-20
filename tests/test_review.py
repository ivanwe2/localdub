import json

from localdub import review
from localdub.segments import upgrade, upgrade_all
from localdub.text import reply_text


def test_review_files_round_trip(tmp_path):
    segs = [{"id": 1, "start": 1.0, "end": 3.0, "src": "Днес отиваме.", "tgt": "Today we go.", "slot": 2.5, "budget": 37},
            {"id": 2, "start": 3.5, "end": 5.0, "src": "Найемите са високи.", "tgt": "Rents are high.", "slot": 2.0,
             "budget": 30}]
    report = {2: {"learned": [], "fixes": [["Найемите", "Наемите"]], "review": [], "unknown": ["найемите"]}}
    p1 = tmp_path / "r1.txt"
    assert review.write_transcript_review(p1, segs, report, None, "bg") == 1
    assert "BG: Найемите са високи." in p1.read_text(encoding="utf-8")
    p2 = tmp_path / "r2.txt"
    review.write_translation_review(p2, segs, "bg", "en")
    text = p2.read_text(encoding="utf-8")
    text = text.replace("status: pending", "status: approved").replace("glossary:\n", "glossary:\nнаем = rent\n")
    text = text.replace("EN: Rents are high.", "EN: The rent is high.")
    p2.write_bytes(("﻿" + text).replace("\n", "\r\n").encode("utf-8"))  # edited on Windows
    parsed = review.parse(p2, "bg", "en")
    assert parsed["status"] == "approved"
    assert parsed["glossary"] == {"наем": "rent"}
    assert parsed["tgt"] == {1: "Today we go.", 2: "The rent is high."}


def test_review_lines_are_marked_with_the_channels_languages(tmp_path):
    segs = [{"id": 7, "start": 4.0, "end": 6.0, "src": "Wir gehen heute: zum Markt.", "tgt": "Hoy vamos al mercado.",
             "slot": 2.0, "budget": 30}]
    path = tmp_path / "r2.txt"

    review.write_translation_review(path, segs, "de", "es")
    text = path.read_text(encoding="utf-8")
    edited = text.replace("ES: Hoy vamos al mercado.", "es:  Hoy vamos: al mercado.")  # any case, a colon inside

    assert "DE: Wir gehen heute: zum Markt." in text and "# Edit the ES lines; keep the [id] and DE lines" in text
    path.write_text(edited, encoding="utf-8")
    parsed = review.parse(path, "de", "es")
    assert parsed["src"] == {7: "Wir gehen heute: zum Markt."}
    assert parsed["tgt"] == {7: "Hoy vamos: al mercado."}


def test_segments_from_before_090_are_upgraded_on_read():
    old = {"id": 1, "bg": "Днес отиваме.", "en": "Today we go.", "model_en": "Today we are going."}
    new = {"id": 2, "src": "x", "tgt": "y", "en": "a stray old key is left alone when the new one exists"}

    assert upgrade(dict(old)) == {"id": 1, "src": "Днес отиваме.", "tgt": "Today we go.",
                                  "model_tgt": "Today we are going."}
    assert upgrade_all([dict(new)])[0]["tgt"] == "y"


def test_an_old_run_continues_with_its_old_segments(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALDUB_HOME", str(tmp_path))
    from localdub.run import Run
    run_dir = tmp_path / "runs" / "old"
    run_dir.mkdir(parents=True)
    (run_dir / "segments.json").write_text(json.dumps([{"id": 1, "bg": "Здравей.", "en": "Hello."}]), encoding="utf-8")

    segs = Run("old").segs

    assert (segs[0]["src"], segs[0]["tgt"]) == ("Здравей.", "Hello.")


def test_the_translators_reply_may_name_its_text_field_after_the_language():
    assert reply_text({"id": 3, "text": " Hola. "}) == "Hola."
    assert reply_text({"id": 3, "es": "Hola."}) == "Hola."
    assert reply_text({"id": 3, "text": "", "en": "Hello."}) == "Hello."
    assert reply_text({"id": 3}) == ""
