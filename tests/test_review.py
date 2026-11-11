from localdub import review


def test_review_files_round_trip(tmp_path):
    segs = [{"id": 1, "start": 1.0, "end": 3.0, "bg": "Днес отиваме.", "en": "Today we go.", "slot": 2.5, "budget": 37},
            {"id": 2, "start": 3.5, "end": 5.0, "bg": "Найемите са високи.", "en": "Rents are high.", "slot": 2.0,
             "budget": 30}]
    report = {2: {"learned": [], "fixes": [["Найемите", "Наемите"]], "review": [], "unknown": ["найемите"]}}
    p1 = tmp_path / "r1.txt"
    assert review.write_transcript_review(p1, segs, report, None) == 1
    p2 = tmp_path / "r2.txt"
    review.write_translation_review(p2, segs)
    text = p2.read_text(encoding="utf-8")
    text = text.replace("status: pending", "status: approved").replace("glossary:\n", "glossary:\nнаем = rent\n")
    text = text.replace("EN: Rents are high.", "EN: The rent is high.")
    p2.write_bytes(("﻿" + text).replace("\n", "\r\n").encode("utf-8"))  # edited on Windows
    parsed = review.parse(p2)
    assert parsed["status"] == "approved"
    assert parsed["glossary"] == {"наем": "rent"}
    assert parsed["en"] == {1: "Today we go.", 2: "The rent is high."}
