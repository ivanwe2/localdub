"""The take check: what Nemotron heard against the script."""
from localdub.voice import judge, normalize


def passes(script, heard):
    return judge(script, heard)[0]


def test_numbers_and_abbreviations():
    assert normalize("Mr. Smith paid 200%") == ["mister", "smith", "paid", "two", "hundred", "percent"]
    assert passes("Bravo, Mr. Petrov!", "Bravo, mister Petrof.")
    assert passes("Seven hundred!", "Seven hundred.")


def test_cyrillic_and_word_breaks():
    assert passes("Bam.", "Бам.")
    assert passes("Sports centre", "Спортсентре.")
    assert passes("It ran non-stop.", "It ran nonstop.")
    assert passes("Kotsev, jump!", "Kocev jump.")


def test_clipped_endings_fail():
    assert not passes("We found it in the city centre for a reasonable price, which is great.",
                      "We found it in the city centre for a reasonable price, which is")
    assert not passes("Welcome to Plovdiv.", "Welcome to")
    assert not passes("We found it in the city centre for a video.", "We found it in the city")


def test_numbers_are_spelled_in_the_dub_language():
    assert judge("Das kostet 200.000 Euro, also 15 % mehr.",
                 "Das kostet zweihunderttausend Euro, also fünfzehn Prozent mehr.", "de")[0]
    assert judge("Il a payé 2 000 euros.", "Il a payé deux mille euros.", "fr")[0]
    assert judge("Hoy vamos con el Sr. García.", "Hoy vamos con el señor García.", "es")[0]
    assert judge("Это стоит 300 рублей.", "Это стоит триста рублей.", "ru")[0]
    assert not judge("Это стоит 300 рублей.", "Это стоит", "ru")[0], "a clipped ending still fails"


def test_without_number_words_digits_are_left_out_on_both_sides(monkeypatch):
    from localdub import languages
    monkeypatch.setattr(languages, "num2words", None)

    assert normalize("Wir haben 12 Zimmer gebucht", "de") == ["wir", "haben", "zimmer", "gebucht"]
    assert judge("Wir haben gestern genau 12 Zimmer für die ganze Gruppe gebucht.",
                 "Wir haben gestern genau zwölf Zimmer für die ganze Gruppe gebucht.", "de")[0]


def test_english_numbers_keep_their_old_spelling():
    from localdub.languages import spell_number, written_number
    assert spell_number(205, "en") == "two hundred five"
    assert (written_number(200000, "en"), written_number(200000, "de"), written_number(200000, "fr")) == \
        ("200,000", "200.000", "200 000")
    assert spell_number(5, "bg") is None
