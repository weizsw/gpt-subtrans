from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

import regex

from PySubtrans.Helpers.Speech import SPOKEN_CHAR
from PySubtrans.Transcription.WordTiming import WordTiming

# Punctuation that opens what follows it, such as Spanish question marks, quotes and brackets
OPENING_CHAR = regex.compile(r'[\p{Ps}\p{Pi}¿¡]')


class WordCoverage(Enum):
    """
    How much of its transcript a provider's words can be relied on to spell.

    COMPLETE words miss at most the odd character, so they time a part by themselves.
    PARTIAL words can miss whole stretches, so a part is given room for the text they missed.
    """
    COMPLETE = 'complete'
    PARTIAL = 'partial'


@dataclass
class AlignedWord:
    """A word matched to the transcript, with the range of transcript characters it matched and how many it matched."""
    word : WordTiming
    start : int
    end : int
    matched : int


def AlignWords(text : str, words : list[WordTiming]) -> list[AlignedWord]:
    """
    Match words to the transcript they came from, character by character.

    Only spoken characters are compared, so punctuation and spacing on either side do not matter.
    Words with no matching character are left out.
    Matching keeps order on both sides, so the result is in transcript order.
    """
    offsets = [index for index, char in enumerate(text) if SPOKEN_CHAR.match(char)]
    owners = [index for index, word in enumerate(words) for char in word.text if SPOKEN_CHAR.match(char)]
    word_chars = ''.join(char for word in words for char in word.text if SPOKEN_CHAR.match(char))
    text_chars = ''.join(text[offset] for offset in offsets)

    spans : dict[int, tuple[int, int, int]] = {}
    matcher = SequenceMatcher(None, word_chars, text_chars, autojunk=False)
    for word_index, text_index, size in matcher.get_matching_blocks():
        for step in range(size):
            owner = owners[word_index + step]
            offset = offsets[text_index + step]
            first, last, count = spans.get(owner, (offset, offset, 0))
            spans[owner] = (min(first, offset), max(last, offset), count + 1)

    return [AlignedWord(words[index], first, last + 1, count) for index, (first, last, count) in sorted(spans.items())]


def CutPoints(text : str, aligned : list[AlignedWord], start : int, end : int) -> list[int]:
    """
    Divide text[start:end] among aligned words, so each word owns a slice holding its matched characters.

    Characters the words missed go to the word before them, up to the last closing punctuation in the gap.
    The rest go to the word after, so punctuation stays with the text it closes, and opening punctuation with the text it opens.
    Returns one more cut than there are words.
    """
    cuts = [start]
    for previous, word in zip(aligned, aligned[1:]):
        gap = text[previous.end:word.start]
        last_punctuation = max((index for index, char in enumerate(gap)
                                if not SPOKEN_CHAR.match(char) and not char.isspace() and not OPENING_CHAR.match(char)),
                               default=-1)
        cuts.append(previous.end + last_punctuation + 1)

    cuts.append(end)
    return cuts
