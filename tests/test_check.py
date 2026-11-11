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
