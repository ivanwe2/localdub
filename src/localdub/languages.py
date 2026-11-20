"""The languages localdub knows: one table, so nothing else in the engine assumes a language.

A channel has a source language (what the video speaks) and a target language (what the dub speaks).
Any language here can be a source if Nemotron transcribes it and a spelling dictionary exists. Which
ones can be a target depends on the voice model, so that is a setting (`tts_languages`, see
settings.py), not part of this table.

Each entry says how the engine handles the language:
  name           English name, used in the prompts ("German")
  iso3           ISO 639-2 code, for the dubbed video's audio-track language tag ("deu")
  hunspell       spelling dictionary ("de_DE"); "" if none exists, then spelling fixes are skipped
  apt            Debian package with that dictionary ("hunspell-de-de")
  numbers        num2words language for spelling out numbers in the take check; None if unsupported
  thousands      thousands separator in written numbers ("200,000" / "200.000" / "200 000")
  percent        how "%" is spoken
  abbreviations  how short forms are spoken, for the take check and for keeping caption chunks whole
  calibration    test sentences a voice sample is cloned with to score it (needed for target languages)
  spelling_hint  examples of the recogniser's spelling-by-ear mistakes, for the spelling prompt
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:  # spells numbers in most languages; without it only English numbers are spelled
    from num2words import num2words
except ImportError:  # pragma: no cover
    num2words = None


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    iso3: str
    hunspell: str
    apt: str
    numbers: str | None
    thousands: str
    percent: str
    abbreviations: dict[str, str] = field(default_factory=dict)
    calibration: tuple[str, ...] = ()
    spelling_hint: str = ""


LANGUAGES: dict[str, Language] = {
    lang.code: lang
    for lang in [
        Language(
            "en", "English", "eng", "en_US", "hunspell-en-us", "en", ",", "percent",
            {"mr": "mister", "mrs": "missus", "ms": "miss", "dr": "doctor", "vs": "versus"},
            (
                "Hi everyone, welcome back to the channel, today we have something really fun for you.",
                "I honestly did not expect it to be this cheap, but here we are.",
                "So we packed our bags, grabbed the camera, and went straight to the city centre.",
                "Let me know in the comments what you would have done in my place.",
            ),
        ),
        Language(
            "de", "German", "deu", "de_DE", "hunspell-de-de", "de", ".", "Prozent",
            {"dr": "doktor", "prof": "professor", "nr": "nummer", "ca": "circa"},
            (
                "Hallo zusammen, willkommen zurück auf dem Kanal, heute haben wir etwas richtig Lustiges für euch.",
                "Ehrlich gesagt hätte ich nicht gedacht, dass es so günstig ist, aber so ist es.",
                "Also haben wir unsere Taschen gepackt, die Kamera geschnappt und sind direkt ins Stadtzentrum gefahren.",
                "Schreibt mir in die Kommentare, was ihr an meiner Stelle gemacht hättet.",
            ),
        ),
        Language(
            "fr", "French", "fra", "fr_FR", "hunspell-fr", "fr", " ", "pour cent",
            {"m": "monsieur", "mme": "madame", "mlle": "mademoiselle", "dr": "docteur"},
            (
                "Salut tout le monde, bon retour sur la chaîne, aujourd'hui on a quelque chose de vraiment sympa pour vous.",
                "Honnêtement, je ne m'attendais pas à ce que ce soit aussi bon marché, mais nous y voilà.",
                "Alors on a fait nos sacs, pris la caméra, et on est allés directement au centre-ville.",
                "Dites-moi dans les commentaires ce que vous auriez fait à ma place.",
            ),
        ),
        Language(
            "it", "Italian", "ita", "it_IT", "hunspell-it", "it", ".", "per cento",
            {"sig": "signor", "dott": "dottor", "prof": "professor"},
            (
                "Ciao a tutti, bentornati sul canale, oggi abbiamo qualcosa di davvero divertente per voi.",
                "Sinceramente non mi aspettavo che costasse così poco, ma eccoci qui.",
                "Così abbiamo preparato le valigie, preso la telecamera e siamo andati dritti in centro.",
                "Scrivetemi nei commenti cosa avreste fatto al mio posto.",
            ),
        ),
        Language(
            "pt", "Portuguese", "por", "pt_BR", "hunspell-pt-br", "pt_BR", ".", "por cento",
            {"sr": "senhor", "sra": "senhora", "dr": "doutor"},
            (
                "Olá a todos, bem-vindos de volta ao canal, hoje temos algo muito divertido para vocês.",
                "Sinceramente, não esperava que fosse tão barato, mas aqui estamos.",
                "Então arrumamos as malas, pegamos a câmera e fomos direto para o centro da cidade.",
                "Me contem nos comentários o que vocês teriam feito no meu lugar.",
            ),
        ),
        Language(
            "es", "Spanish", "spa", "es_ES", "hunspell-es", "es", ".", "por ciento",
            {"sr": "señor", "sra": "señora", "dr": "doctor", "ud": "usted"},
            (
                "Hola a todos, bienvenidos de nuevo al canal, hoy tenemos algo muy divertido para ustedes.",
                "Sinceramente, no esperaba que fuera tan barato, pero aquí estamos.",
                "Así que hicimos las maletas, tomamos la cámara y fuimos directo al centro de la ciudad.",
                "Díganme en los comentarios qué habrían hecho ustedes en mi lugar.",
            ),
        ),
        Language(
            "ru", "Russian", "rus", "ru_RU", "hunspell-ru", "ru", " ", "процентов",
            {},
            (
                "Всем привет, с возвращением на канал, сегодня у нас для вас кое-что очень интересное.",
                "Честно говоря, никто не ожидал, что это будет так дёшево, но вот так.",
                "Так что мы собрали вещи, взяли камеру и поехали прямо в центр города.",
                "Напишите в комментариях, что бы вы сделали на моём месте.",
            ),
        ),
        Language(
            "bg", "Bulgarian", "bul", "bg_BG", "hunspell-bg", None, " ", "процента",
            {"г": "година", "гр": "град", "ул": "улица"},
            spelling_hint=(
                "For Bulgarian, e.g. 'ф' for 'в' as in \"фсички\" -> \"всички\", \"ефтино\" -> \"евтино\", or an "
                "extra 'й' as in \"найемите\" -> \"наемите\". Count forms after numbers are correct as they are."
            ),
        ),
        Language("nl", "Dutch", "nld", "nl_NL", "hunspell-nl", "nl", ".", "procent", {"dhr": "de heer", "mevr": "mevrouw"}),
        Language("pl", "Polish", "pol", "pl_PL", "hunspell-pl", "pl", " ", "procent", {"dr": "doktor", "prof": "profesor"}),
        Language("uk", "Ukrainian", "ukr", "uk_UA", "hunspell-uk", "uk", " ", "відсотків"),
        Language("ro", "Romanian", "ron", "ro_RO", "hunspell-ro", "ro", ".", "la sută", {"dl": "domnul", "dna": "doamna"}),
        Language("cs", "Czech", "ces", "cs_CZ", "hunspell-cs", "cs", " ", "procent"),
        Language("sv", "Swedish", "swe", "sv_SE", "hunspell-sv", "sv", " ", "procent"),
    ]
}


def get(code: str) -> Language:
    """The language with this ISO 639-1 code; a ValueError that lists the known ones otherwise."""
    try:
        return LANGUAGES[code]
    except KeyError:
        known = ", ".join(f"{c} ({lang.name})" for c, lang in LANGUAGES.items())
        raise ValueError(f"unknown language {code!r}; localdub knows: {known}") from None


def dub_languages(settings: dict) -> list[str]:
    """The languages a channel can be dubbed into: the voice model's (settings), in table order."""
    wanted = set(settings.get("tts_languages", []))
    return [code for code in LANGUAGES if code in wanted]


# --- numbers ---------------------------------------------------------------------------------------

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen " \
        "sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + number_words(n % 100))
    for size, word in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= size:
            rest = n % size
            return number_words(n // size) + " " + word + ("" if rest == 0 else " " + number_words(rest))
    return str(n)


def spell_number(n: int, code: str) -> str | None:
    """A number in words in this language ("zweihunderttausend"), or None if localdub can't spell it
    there. English keeps localdub's own spelling, without "and" ("two hundred five")."""
    if code == "en":
        return number_words(n)
    lang = LANGUAGES.get(code)
    if lang is None or lang.numbers is None or num2words is None:
        return None
    try:
        return num2words(n, lang=lang.numbers).replace(",", " ")
    except (NotImplementedError, OverflowError, KeyError):
        return None


def written_number(n: int, code: str) -> str:
    """A number in digits as this language writes it: "200,000", "200.000", "200 000"."""
    sep = LANGUAGES[code].thousands if code in LANGUAGES else ","
    return f"{n:,}".replace(",", sep)
