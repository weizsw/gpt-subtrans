from enum import Enum

import regex

# Sentence-ending punctuation across CJK and latin scripts
SENTENCE_END_CHARS = frozenset('。！？!?\n…')

# A character that is spoken, rather than punctuation, a symbol or spacing
SPOKEN_CHAR = regex.compile(r'[^\p{P}\p{S}\s]')

# Characters that may close a sentence after its end punctuation, such as quotes and brackets
CLOSING_CHARS = regex.compile(r'[\p{Pe}\p{Pf}"\'\s]+$')

# Scripts written with one character per syllable, which take longer to say per character
SYLLABIC_CHAR = regex.compile(r'[\p{Han}\p{Hiragana}\p{Katakana}\p{Hangul}]')

# Speaking rates for estimating how long text takes to say.
# The syllabic rate is the median of OpenRouter parts for Cantonese dialogue.
SYLLABIC_SECONDS_PER_CHAR = 0.2
OTHER_SECONDS_PER_CHAR = 0.07
MIN_SPEECH_SECONDS = 0.3


class SentenceEnds(Enum):
    """
    Which punctuation ends a sentence.

    STRONG is question and exclamation marks, CJK full stops, ellipses and line breaks.
    Text with word timings is left to them to divide at full stops.
    ALL adds full stops, for text with no word timings to divide it.
    """
    STRONG = 'strong'
    ALL = 'all'


def NominalSecondsPerChar(text : str) -> float:
    """The normal time to say one spoken character of the text, from its script."""
    syllabic = len(SYLLABIC_CHAR.findall(text))
    other = sum(1 for char in text if char.isalnum()) - syllabic
    if syllabic + other == 0:
        return SYLLABIC_SECONDS_PER_CHAR

    return (syllabic * SYLLABIC_SECONDS_PER_CHAR + other * OTHER_SECONDS_PER_CHAR) / (syllabic + other)


def EstimateSpeechSeconds(text : str) -> float:
    """Roughly how long text takes to say, from its character count and script."""
    syllabic = len(SYLLABIC_CHAR.findall(text))
    other = sum(1 for char in text if char.isalnum()) - syllabic
    return max(MIN_SPEECH_SECONDS, syllabic * SYLLABIC_SECONDS_PER_CHAR + other * OTHER_SECONDS_PER_CHAR)


def IsSentenceEnd(text : str, index : int, ends : SentenceEnds = SentenceEnds.STRONG) -> bool:
    """
    Whether the character at index ends a sentence.
    A full stop counts only where the text breaks after it, so decimals do not.
    """
    if text[index] in SENTENCE_END_CHARS:
        return True

    return (ends == SentenceEnds.ALL and text[index] == '.'
            and (index + 1 == len(text) or bool(CLOSING_CHARS.match(text[index + 1]))))


def SentenceRanges(text : str, ends : SentenceEnds = SentenceEnds.STRONG) -> list[tuple[int, int]]:
    """Ranges of the text ending at sentence punctuation, with any closing quotes or brackets."""
    ranges : list[tuple[int, int]] = []
    start = 0
    index = 0

    while index < len(text):
        if IsSentenceEnd(text, index, ends):
            end = index + 1
            while end < len(text) and (IsSentenceEnd(text, end, ends) or CLOSING_CHARS.match(text[end])):
                end += 1
            ranges.append((start, end))
            start = index = end
        else:
            index += 1

    if start < len(text):
        ranges.append((start, len(text)))

    return ranges
