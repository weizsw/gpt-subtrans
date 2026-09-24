from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, replace
from datetime import timedelta
from difflib import SequenceMatcher

import regex

from PySubtrans.Helpers.Localization import _
from PySubtrans.Helpers.Text import JoinWords
from PySubtrans.Transcription.AudioChunker import AudioChunk
from PySubtrans.Transcription.WordTiming import WordTiming
from PySubtrans.Transcription.TranscriptionSegment import TranscriptionSegment

# Sentence-ending punctuation across CJK and latin scripts
SENTENCE_END_CHARS = frozenset('。！？!?\n…')

# Clause punctuation (and a period, which is not a hard boundary) that makes
# a good place to break an over-long utterance
CLAUSE_END_CHARS = frozenset('.,;:，、；：-–—')

# Widest gap that can fall within a line when the speaker is unknown or changes
DEFAULT_MERGE_ELIGIBLE_GAP_SECONDS = 0.5

# A pause within one speaker's turn is not a break, so it is given more room
DEFAULT_SAME_SPEAKER_MERGE_ELIGIBLE_GAP_SECONDS = 1.0

# Space left between a line extended into a pause and the line after it
DEFAULT_MIN_GAP_SECONDS = 0.05

# Split-point scoring for over-long utterances: the pause at a boundary is
# the primary signal, weighted by how central the boundary is. The floor
# lets zero-pause boundaries still resolve by centrality; the bonuses and
# penalty nudge toward clause ends and away from stranding short words.
PAUSE_SCORE_FLOOR = 0.1
SENTENCE_END_BONUS = 0.5
CLAUSE_END_BONUS = 0.15
SHORT_WORD_PENALTY = 0.05
SHORT_WORD_CHARS = 3

# A word made of nothing but punctuation and spacing
PUNCTUATION_ONLY = regex.compile(r'^[\p{P}\s]+$')

# A character that is spoken, and so can be matched between a transcript and its words
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

# A word is capped at this multiple of its estimated speaking time
WORD_CAP_MULTIPLE = 4.0
MIN_WORD_CAP_SECONDS = 1.0

# A word lasting this multiple of its estimate is an aligner failure, and is not used for timing
UNRELIABLE_WORD_MULTIPLE = 8.0
MIN_UNRELIABLE_WORD_SECONDS = 2.0


def SpanLabel(span : AudioChunk|TranscriptionSegment) -> str:
    """Human-readable start-end label for a chunk or segment, in seconds."""
    return f"{span.start.total_seconds():.1f}s-{span.end.total_seconds():.1f}s"


def CompactText(text : str) -> str:
    """Text with all whitespace removed, for comparing transcripts that space words differently."""
    return ''.join(text.split())


def CutText(text : str, lengths : list[int]) -> list[str]:
    """
    Cut text into pieces holding the given numbers of non-whitespace characters.
    The last piece takes whatever remains.
    """
    pieces : list[str] = []
    position = 0

    for length in lengths[:-1]:
        seen = 0
        end = position
        while end < len(text) and seen < length:
            if not text[end].isspace():
                seen += 1
            end += 1

        pieces.append(text[position:end].strip())
        position = end

    pieces.append(text[position:].strip())
    return pieces


def EstimateSpeechSeconds(text : str) -> float:
    """Roughly how long text takes to say, from its character count and script."""
    syllabic = len(SYLLABIC_CHAR.findall(text))
    other = sum(1 for char in text if char.isalnum()) - syllabic
    return max(MIN_SPEECH_SECONDS, syllabic * SYLLABIC_SECONDS_PER_CHAR + other * OTHER_SECONDS_PER_CHAR)


def EndsSentence(text : str) -> bool:
    """Whether text ends with sentence punctuation, ignoring closing quotes and brackets."""
    text = CLOSING_CHARS.sub('', text)
    return bool(text) and text[-1] in SENTENCE_END_CHARS


def SentenceRanges(text : str) -> list[tuple[int, int]]:
    """Ranges of the text ending at sentence punctuation, with any closing quotes or brackets."""
    ranges : list[tuple[int, int]] = []
    start = 0
    index = 0

    while index < len(text):
        if text[index] in SENTENCE_END_CHARS:
            end = index + 1
            while end < len(text) and (text[end] in SENTENCE_END_CHARS or CLOSING_CHARS.match(text[end])):
                end += 1
            ranges.append((start, end))
            start = index = end
        else:
            index += 1

    if start < len(text):
        ranges.append((start, len(text)))

    return ranges


@dataclass
class AlignedWord:
    """A word matched to the transcript, with the range of transcript characters it matched."""
    word : WordTiming
    start : int
    end : int


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

    spans : dict[int, tuple[int, int]] = {}
    matcher = SequenceMatcher(None, word_chars, text_chars, autojunk=False)
    for word_index, text_index, size in matcher.get_matching_blocks():
        for step in range(size):
            owner = owners[word_index + step]
            offset = offsets[text_index + step]
            first, last = spans.get(owner, (offset, offset))
            spans[owner] = (min(first, offset), max(last, offset))

    return [AlignedWord(words[index], first, last + 1) for index, (first, last) in sorted(spans.items())]


def CutPoints(text : str, aligned : list[AlignedWord], start : int, end : int) -> list[int]:
    """
    Divide text[start:end] among aligned words, so each word owns a slice holding its matched characters.

    Characters the words missed go to the word before them, up to the last punctuation in the gap.
    The rest go to the word after, so punctuation stays with the text it closes.
    Returns one more cut than there are words.
    """
    cuts = [start]
    for previous, word in zip(aligned, aligned[1:]):
        gap = text[previous.end:word.start]
        last_punctuation = max((index for index, char in enumerate(gap) if not SPOKEN_CHAR.match(char) and not char.isspace()),
                               default=-1)
        cuts.append(previous.end + last_punctuation + 1)

    cuts.append(end)
    return cuts


class TranscriptionLineBuilder:
    """
    Turns transcribed chunks into timed subtitle lines.

    Provider sub-segments are the lines when there are any; word timings
    only split the ones over the character or duration limit. Without
    sub-segments, the chunk transcript is cut into parts and timed by its
    words, so the transcript supplies the text and the words only the
    timing. With words and no transcript, words group into utterances at
    pauses, speaker changes and sentence punctuation, and over-long
    utterances are split at their best pause. Brief slivers merge into
    their neighbours.
    No provider or audio dependencies.
    """
    def __init__(self, max_line_chars : int, max_line_seconds : float, min_split_chars : int = 3,
                 min_line_seconds : float = 0.8, merge_eligible_gap : float = DEFAULT_MERGE_ELIGIBLE_GAP_SECONDS,
                 same_speaker_merge_eligible_gap : float = DEFAULT_SAME_SPEAKER_MERGE_ELIGIBLE_GAP_SECONDS,
                 max_newlines : int = 2, can_merge_different_speakers : bool = True,
                 min_gap : float = DEFAULT_MIN_GAP_SECONDS):
        self.max_line_chars : int = max_line_chars
        self.max_line_seconds : float = max_line_seconds
        self.min_split_chars : int = min_split_chars
        self.min_line_seconds : float = min_line_seconds
        self.merge_eligible_gap : float = merge_eligible_gap
        self.same_speaker_merge_eligible_gap : float = same_speaker_merge_eligible_gap
        self.max_newlines : int = max_newlines
        self.can_merge_different_speakers : bool = can_merge_different_speakers
        self.min_gap : float = min_gap

    def LinesForSegment(self, segment : TranscriptionSegment) -> list[TranscriptionSegment]:
        """
        Turn a transcribed chunk into timed subtitle lines.

        Provider sub-segments become rebased lines, split by word timings
        where they run over a limit. Without sub-segments, the chunk
        transcript is cut into parts timed by its words, which are then
        treated the same way. Words alone, with no transcript, group into
        lines. Either way brief slivers are merged back into their
        neighbours. A chunk with nothing but a span stays one line.
        """
        if segment.parts:
            return self._lines_from_parts(segment, self._timing_words(segment.words))

        if segment.text.strip():
            return self._lines_from_transcript(segment, self._timing_words(segment.words))

        if segment.words:
            lines = self._group_words([self._capped_word(word) for word in segment.words], segment)
            return lines or [segment]

        self.WarnIfOverlong(segment)
        return [segment]

    def WarnIfOverlong(self, line : TranscriptionSegment) -> bool:
        """
        Flag engine-coarse spans no splitter can break up. Word-timed lines
        are already capped by grouping; over-long lines can only come from
        untimed engine segments, whose boundaries deserve a human glance.
        Returns True when a warning was logged.
        """
        duration = (line.end - line.start).total_seconds()
        if duration > self.max_line_seconds:
            logging.warning(_("Long transcription line ({:.1f}s, no word timings to split it): '{}'").format(
                duration, line.text[:120]))
            return True
        return False

    def MergeSlivers(self, lines : list[TranscriptionSegment], limit : timedelta|None = None) -> list[TranscriptionSegment]:
        """
        Merge brief adjacent lines, preserving pauses and dialogue turns.

        A run too long for one subtitle is divided into even pieces. A brief
        line left over afterwards is extended into the pause after it where
        there is room; the last line may extend up to `limit`, when given.

        Lines are taken in time order. Subtitles play in time order whatever
        order the engine emitted them in, so that is the order in which
        neighbours must be judged.
        """
        lines = sorted(lines, key=lambda line: line.start)
        if len(lines) < 2:
            return self._extend_into_pauses(lines, limit)

        merged : list[TranscriptionSegment] = []
        for run in self._sliver_runs(lines):
            merged.extend(self._merge_run(chunk) for chunk in self._balanced_chunks(run))

        return self._extend_into_pauses(merged, limit)

    def _capped_word(self, word : WordTiming) -> WordTiming:
        """
        Limit a word to a generous multiple of the time its text takes to say, and never beyond a whole line.
        Engines occasionally stamp a word across most of a chunk.
        """
        cap = min(self.max_line_seconds, max(MIN_WORD_CAP_SECONDS, WORD_CAP_MULTIPLE * EstimateSpeechSeconds(word.text)))
        if (word.end - word.start).total_seconds() <= cap:
            return word

        return WordTiming(text=word.text, start=word.start, end=word.start + timedelta(seconds=cap), speaker=word.speaker)

    def _timing_words(self, words : list[WordTiming]) -> list[WordTiming]:
        """
        Words fit to time a transcript: capped, and without those whose span is too long for their text to be real.
        """
        reliable = [word for word in words if (word.end - word.start).total_seconds()
                    <= max(MIN_UNRELIABLE_WORD_SECONDS, UNRELIABLE_WORD_MULTIPLE * EstimateSpeechSeconds(word.text))]
        return [self._capped_word(word) for word in reliable]

    def _lines_from_parts(self, segment : TranscriptionSegment, words : list[WordTiming]) -> list[TranscriptionSegment]:
        """
        Rebase the provider's sub-segments into lines, splitting any over a limit.
        """
        parts = [part for part in segment.parts if part.text.strip()]
        return self._fit_parts(segment, parts, self._assign_words(parts, words))

    def _fit_parts(self, segment : TranscriptionSegment, parts : list[TranscriptionSegment],
                   assigned : list[list[WordTiming]]) -> list[TranscriptionSegment]:
        """
        Turn chunk-relative parts and their words into lines, then merge slivers.
        """
        lines : list[TranscriptionSegment] = []
        for part, part_words in zip(parts, assigned):
            lines.extend(self._fit_part(part, part_words, segment))

        # Providers that segment for us still strand fragments, which
        # translate badly in isolation
        merged = self.MergeSlivers(lines, limit=segment.end)
        for line in merged:
            self.WarnIfOverlong(line)

        return merged or [segment]

    def _assign_words(self, parts : list[TranscriptionSegment], words : list[WordTiming]) -> list[list[WordTiming]]:
        """
        Share words out among the parts that transcribe them, matching text in order.

        Timings are not consulted, since they are the unreliable half.
        Each word is respelled with the slice of part text it covers, so a part's words spell it exactly,
        including characters and punctuation the words themselves left out.
        """
        text = '\n'.join(part.text for part in parts)

        ranges : list[tuple[int, int]] = []
        position = 0
        for part in parts:
            ranges.append((position, position + len(part.text)))
            position += len(part.text) + 1

        return self._assign_to_ranges(text, ranges, AlignWords(text, words))

    @staticmethod
    def _assign_to_ranges(text : str, ranges : list[tuple[int, int]],
                          aligned : list[AlignedWord]) -> list[list[WordTiming]]:
        """
        Give each range of the text the aligned words that start in it, respelled with their slices of it.
        """
        assigned : list[list[WordTiming]] = []
        index = 0

        for start, end in ranges:
            members : list[AlignedWord] = []
            while index < len(aligned) and aligned[index].start < end:
                if aligned[index].start >= start:
                    members.append(aligned[index])
                index += 1

            cuts = CutPoints(text, members, start, end)
            assigned.append([WordTiming(text=text[cuts[i]:cuts[i + 1]].strip(), start=member.word.start,
                                        end=member.word.end, speaker=member.word.speaker)
                             for i, member in enumerate(members)])

        return assigned

    def _lines_from_transcript(self, segment : TranscriptionSegment, words : list[WordTiming]) -> list[TranscriptionSegment]:
        """
        Cut the chunk transcript into parts timed by its words, then treat them as provider parts.

        The transcript is the text: words drop characters and punctuation, so they only give the timing.
        Parts end at sentence punctuation where the transcript has it, and otherwise at pauses and speaker changes.
        Parts no word can be matched to are placed between their neighbours by their share of characters.
        """
        text = segment.text.strip()
        aligned = AlignWords(text, self._extend_to_punctuation(words))

        if any(char in SENTENCE_END_CHARS for char in text):
            ranges = self._split_at_speaker_changes(text, SentenceRanges(text), aligned)
        else:
            ranges = self._pause_ranges(text, aligned)

        assigned = self._assign_to_ranges(text, ranges, aligned)
        parts = [TranscriptionSegment(text=text[start:end].strip(), speaker=self._majority_speaker(part_words))
                 for (start, end), part_words in zip(ranges, assigned)]

        if not aligned:
            logging.info(_("Chunk {}: no word timings match the transcript, so lines are placed by length").format(
                SpanLabel(segment)))

        self._time_parts(parts, assigned, segment.end - segment.start)

        kept = [index for index, part in enumerate(parts) if part.text]
        return self._fit_parts(segment, [parts[index] for index in kept], [assigned[index] for index in kept])

    @staticmethod
    def _extend_to_punctuation(words : list[WordTiming]) -> list[WordTiming]:
        """
        Extend each word to the end of any punctuation-only words after it.
        Engines that time punctuation place it where the utterance ends, so a part closed by it ends there too.
        """
        extended : list[WordTiming] = []
        spoken : int|None = None
        for word in words:
            if not PUNCTUATION_ONLY.match(word.text):
                spoken = len(extended)
            elif spoken is not None and word.end > extended[spoken].end:
                previous = extended[spoken]
                extended[spoken] = WordTiming(text=previous.text, start=previous.start, end=word.end, speaker=previous.speaker)
            extended.append(word)

        return extended

    @staticmethod
    def _split_at_speaker_changes(text : str, ranges : list[tuple[int, int]],
                                  aligned : list[AlignedWord]) -> list[tuple[int, int]]:
        """Divide ranges where the speaker of their words changes."""
        split : list[tuple[int, int]] = []
        index = 0

        for start, end in ranges:
            members : list[AlignedWord] = []
            while index < len(aligned) and aligned[index].start < end:
                if aligned[index].start >= start:
                    members.append(aligned[index])
                index += 1

            cuts = CutPoints(text, members, start, end)
            for position in range(1, len(members)):
                previous, word = members[position - 1].word, members[position].word
                if previous.speaker is not None and word.speaker is not None and previous.speaker != word.speaker:
                    split.append((start, cuts[position]))
                    start = cuts[position]

            split.append((start, end))

        return split

    def _pause_ranges(self, text : str, aligned : list[AlignedWord]) -> list[tuple[int, int]]:
        """Ranges of an unpunctuated text, cut where its words pause or change speaker."""
        if not aligned:
            return [(0, len(text))]

        cuts = CutPoints(text, aligned, 0, len(text))
        ranges : list[tuple[int, int]] = []
        start = 0

        for index in range(1, len(aligned)):
            if self._is_hard_boundary(aligned[index - 1].word, aligned[index].word):
                ranges.append((start, cuts[index]))
                start = cuts[index]

        ranges.append((start, len(text)))
        return ranges

    @staticmethod
    def _majority_speaker(words : list[WordTiming]) -> str|None:
        """The speaker of most of the words, if any carry one."""
        speakers = Counter(word.speaker for word in words if word.speaker is not None)
        return speakers.most_common(1)[0][0] if speakers else None

    def _time_parts(self, parts : list[TranscriptionSegment], assigned : list[list[WordTiming]], duration : timedelta) -> None:
        """
        Set each part's chunk-relative span from its words.

        A run of parts without words shares the time between its timed neighbours by characters.
        Between timed parts, each is given only as long as its text takes to say, so it does not stretch over silence.
        When no part is timed, each fills its share of the chunk, up to the longest a line may last.
        """
        if not any(assigned):
            total = sum(len(CompactText(part.text)) for part in parts) or 1
            longest = timedelta(seconds=self.max_line_seconds)
            position = 0
            for part in parts:
                part.start = duration * (position / total)
                position += len(CompactText(part.text))
                part.end = min(duration * (position / total), part.start + longest)
            return

        for part, words in zip(parts, assigned):
            if words:
                part.start = min(word.start for word in words)
                part.end = max(word.end for word in words)

        index = 0
        while index < len(parts):
            if assigned[index]:
                index += 1
                continue

            run_end = index
            while run_end < len(parts) and not assigned[run_end]:
                run_end += 1

            after = parts[index - 1].end if index > 0 else timedelta(0)
            before = parts[run_end].start if run_end < len(parts) else duration
            gap = max(timedelta(0), before - after)
            run = parts[index:run_end]
            total = sum(len(CompactText(part.text)) for part in run) or 1

            position = 0
            for offset, part in enumerate(run):
                part.start = after + gap * (position / total)
                position += len(CompactText(part.text))
                room = after + gap * (position / total) - part.start if offset + 1 < len(run) else before - part.start
                speech = timedelta(seconds=EstimateSpeechSeconds(part.text))
                part.end = part.start + (min(speech, room) if room > timedelta(0) else speech)

            index = run_end

    def _fit_part(self, part : TranscriptionSegment, words : list[WordTiming],
                  segment : TranscriptionSegment) -> list[TranscriptionSegment]:
        """
        Rebase a part, splitting it at its words when it runs over a limit.

        The text always comes from the part; words only decide where it is
        cut and when each piece starts and ends. A part whose words fit on
        one line keeps its text but takes the words' span, which corrects a
        provider span that runs far past its speech.
        """
        line = self._rebase_part(part, segment)
        duration = (line.end - line.start).total_seconds()
        if not words or (duration <= self.max_line_seconds and len(line.text) <= self.max_line_chars):
            return [line]

        pieces = self._fit_utterance(self._attach_punctuation(words))
        texts = CutText(line.text, [sum(len(CompactText(word.text)) for word in piece) for piece in pieces])

        lines : list[TranscriptionSegment] = []
        for piece, text in zip(pieces, texts):
            start, end = self._clamped_span(segment, *self._span(piece))
            lines.append(TranscriptionSegment(start=start, end=end, text=text, speaker=line.speaker,
                                              language=line.language, confidence=line.confidence))

        return lines

    @staticmethod
    def _attach_punctuation(words : list[WordTiming]) -> list[WordTiming]:
        """
        Fold punctuation-only words into the word before them, so a split never starts with one.
        The word keeps its own end: punctuation is not spoken, so its timing means nothing.
        """
        attached : list[WordTiming] = []
        for word in words:
            if attached and PUNCTUATION_ONLY.match(word.text):
                previous = attached[-1]
                attached[-1] = WordTiming(text=previous.text + word.text, start=previous.start,
                                          end=previous.end, speaker=previous.speaker)
            else:
                attached.append(word)

        return attached

    def _group_words(self, words : list[WordTiming], segment : TranscriptionSegment) -> list[TranscriptionSegment]:
        """
        Group chunk-relative word timings into subtitle lines. Words first
        split into utterances at real pauses, speaker changes and sentence
        punctuation; utterances that breach the character or duration
        limit are then split at their best pause, so a limit never
        strands a short tail. Offsets are rebased onto the chunk start.
        """
        lines : list[TranscriptionSegment] = []
        for utterance in self._split_utterances(words):
            for run in self._fit_utterance(utterance):
                lines.append(self._line_from_words(run, segment))

        return self.MergeSlivers(lines, limit=segment.end)

    def _split_utterances(self, words : list[WordTiming]) -> list[list[WordTiming]]:
        """Cut words at boundaries that apply regardless of line length."""
        utterances : list[list[WordTiming]] = []
        current : list[WordTiming] = []

        for word in words:
            if current and self._is_hard_boundary(current[-1], word):
                utterances.append(current)
                current = []
            current.append(word)

        if current:
            utterances.append(current)

        return utterances

    def _eligible_gap(self, first_speaker : str|None, second_speaker : str|None) -> float:
        """The widest gap that still leaves two lines eligible to be one."""
        if first_speaker is not None and first_speaker == second_speaker:
            return self.same_speaker_merge_eligible_gap

        return self.merge_eligible_gap

    def _is_hard_boundary(self, previous : WordTiming, word : WordTiming) -> bool:
        """A long pause, a speaker change or the end of a sentence always starts a new line."""
        gap = (word.start - previous.end).total_seconds()
        speaker_changed = (word.speaker is not None and previous.speaker is not None
                           and word.speaker != previous.speaker)
        return (gap >= self._eligible_gap(previous.speaker, word.speaker)
                or speaker_changed
                or bool(previous.text and previous.text[-1] in SENTENCE_END_CHARS))

    @staticmethod
    def _span(words : list[WordTiming]) -> tuple[timedelta, timedelta]:
        """The earliest start and latest end of a run of words, which need not be in time order."""
        return min(word.start for word in words), max(word.end for word in words)

    def _fits(self, words : list[WordTiming]) -> bool:
        """Whether a run of words is within the duration and character limits."""
        start, end = self._span(words)
        seconds = (end - start).total_seconds()
        return seconds <= self.max_line_seconds and len(JoinWords([w.text for w in words])) <= self.max_line_chars

    def _fit_utterance(self, words : list[WordTiming]) -> list[list[WordTiming]]:
        """Split an over-long utterance at its best pauses until every piece fits."""
        if len(words) < 2 or self._fits(words):
            return [words]

        index = self._best_split_index(words)
        if index is None:
            return self._greedy_split(words)

        return self._fit_utterance(words[:index]) + self._fit_utterance(words[index:])

    def _best_split_index(self, words : list[WordTiming]) -> int|None:
        """
        Choose the boundary to split an utterance at. The pause at each
        boundary is the primary signal, weighted by closeness to the time
        midpoint, with bonuses for clause and sentence punctuation and a
        penalty for stranding a short word. Both halves must reach the
        minimum split length; None when no boundary qualifies.
        """
        start, end = self._span(words)
        half_span = (end - start).total_seconds() / 2.0
        best_index : int|None = None
        best_score : float = float('-inf')

        for index in range(1, len(words)):
            head = JoinWords([w.text for w in words[:index]])
            tail = JoinWords([w.text for w in words[index:]])
            if len(head) < self.min_split_chars or len(tail) < self.min_split_chars:
                continue

            previous, word = words[index - 1], words[index]
            pause = max(0.0, (word.start - previous.end).total_seconds())
            position = (previous.end - start).total_seconds()
            centrality = 1.0 - abs(position - half_span) / half_span if half_span > 0.0 else 1.0
            score = (pause + PAUSE_SCORE_FLOOR) * max(0.0, centrality)

            last = previous.text[-1] if previous.text else ''
            if last in SENTENCE_END_CHARS:
                score += SENTENCE_END_BONUS
            elif last in CLAUSE_END_CHARS:
                score += CLAUSE_END_BONUS
            if sum(1 for c in previous.text if c.isalnum()) <= SHORT_WORD_CHARS:
                score -= SHORT_WORD_PENALTY

            if score > best_score:
                best_index, best_score = index, score

        return best_index

    def _greedy_split(self, words : list[WordTiming]) -> list[list[WordTiming]]:
        """Fallback when no balanced split qualifies: break where the limit is breached."""
        pieces : list[list[WordTiming]] = []
        current : list[WordTiming] = []

        for word in words:
            if current and not self._fits(current + [word]):
                pieces.append(current)
                current = []
            current.append(word)

        if current:
            pieces.append(current)

        return pieces

    def _line_from_words(self, words : list[WordTiming], segment : TranscriptionSegment) -> TranscriptionSegment:
        """
        Build one absolute-timed line from a run of chunk-relative words.

        Words are kept in the order the engine emitted them, which is the
        order they were spoken in; their timings are a best-effort signal
        and need not run in step with it, so the span takes the earliest
        start and latest end rather than the first and last word's.
        """
        start, end = self._clamped_span(segment, *self._span(words))
        return TranscriptionSegment(start=start, end=end, text=JoinWords([w.text for w in words]),
                                    speaker=words[0].speaker or segment.speaker,
                                    language=segment.language)

    def _rebase_part(self, part : TranscriptionSegment, segment : TranscriptionSegment) -> TranscriptionSegment:
        """
        Rebase a chunk-relative sub-segment onto absolute media time.
        """
        start, end = self._clamped_span(segment, part.start, part.end)
        if part.confidence is not None and part.confidence < 0.4:
            logging.info(_("Chunk {}: low-confidence segment ({:.0%} no-speech probability): '{}'").format(
                SpanLabel(segment), 1.0 - part.confidence, part.text[:120]))
        return TranscriptionSegment(start=start, end=end, text=part.text.strip(),
                                    speaker=part.speaker or segment.speaker,
                                    language=part.language or segment.language,
                                    confidence=part.confidence)

    def _clamped_span(self, segment : TranscriptionSegment, start_offset : timedelta,
                      end_offset : timedelta) -> tuple[timedelta, timedelta]:
        """
        Rebase chunk-relative offsets onto the segment start, enforcing a
        minimum duration and never running past the segment end.
        """
        start = segment.start + start_offset
        end = segment.start + end_offset

        if start > segment.end:
            start = segment.end
        if end <= start:
            end = start + timedelta(seconds=self.min_line_seconds)
        if end > segment.end:
            end = segment.end
        return start, end

    def _extend_into_pauses(self, lines : list[TranscriptionSegment], limit : timedelta|None) -> list[TranscriptionSegment]:
        """
        Extend each brief line to the minimum duration where the pause after it allows.

        A line is only extended when it can reach the minimum while leaving
        min_gap before the next line; otherwise it is left for merging.
        Only the end moves, so a subtitle never appears before its speech.
        """
        min_duration = timedelta(seconds=self.min_line_seconds)
        min_gap = timedelta(seconds=self.min_gap)

        extended : list[TranscriptionSegment] = []
        for index, line in enumerate(lines):
            following = lines[index + 1].start - min_gap if index + 1 < len(lines) else limit
            target = line.start + min_duration

            if self._is_sliver(line) and following is not None and target <= following:
                line = replace(line, end=target)

            extended.append(line)

        return extended

    def _is_sliver(self, line : TranscriptionSegment) -> bool:
        return (line.end - line.start).total_seconds() < self.min_line_seconds

    @staticmethod
    def _is_dialogue(line : TranscriptionSegment) -> bool:
        return line.text.startswith('- ') and '\n' in line.text

    def _sliver_runs(self, lines : list[TranscriptionSegment]) -> list[list[TranscriptionSegment]]:
        """
        Group lines into runs that belong together.

        Merging rescues material too brief to read, so only a fragment can
        take one on: a line long enough to read already keeps to itself.

        A fragment joins the fragment in front of it. One left standing alone,
        because what came before it was a full line, takes the line behind it
        instead, whatever that line's length. The exception is another
        speaker's line with room before it: the fragment is a complete turn,
        and is better extended into the pause than made into dialogue.

        A line that overlaps the one before it always joins it, whatever the
        lengths or speakers, so overlapping speech is shown together. Only the
        line before counts, so one line with a runaway span cannot draw in
        everything after it.
        """
        runs : list[list[TranscriptionSegment]] = [[lines[0]]]

        for line in lines[1:]:
            run = runs[-1]
            stranded = len(run) == 1
            takes_fragment = self._is_sliver(line) or (stranded and not self._extends_instead(run[-1], line))

            if line.start < run[-1].end or (self._is_sliver(run[-1])
                                            and takes_fragment
                                            and self._merge_eligible(run, line)):
                run.append(line)
            else:
                runs.append([line])

        return runs

    def _extends_instead(self, fragment : TranscriptionSegment, line : TranscriptionSegment) -> bool:
        """Whether a fragment followed by another speaker's line has room to be extended instead of merged."""
        speakers_differ = fragment.speaker is not None and line.speaker is not None and fragment.speaker != line.speaker
        target = fragment.start + timedelta(seconds=self.min_line_seconds)
        return speakers_differ and target <= line.start - timedelta(seconds=self.min_gap)

    def _merge_eligible(self, run : list[TranscriptionSegment], line : TranscriptionSegment) -> bool:
        """
        Whether a line belongs with a run, by speaker and by the pause before it.

        The pause is measured from the end of the run, so it is the speaker of
        the turn ending there that the line either continues or interrupts.
        """
        speaker = run[-1].speaker

        if not self.can_merge_different_speakers and speaker is not None and line.speaker is not None:
            if speaker != line.speaker:
                return False

        end = max(part.end for part in run)
        return (line.start - end).total_seconds() < self._eligible_gap(speaker, line.speaker)

    def _balanced_chunks(self, run : list[TranscriptionSegment]) -> list[list[TranscriptionSegment]]:
        """Divide a run into the fewest even pieces that each read comfortably."""
        if len(run) < 2 or self._chunk_fits(run):
            return [run]

        for count in range(2, len(run)):
            chunks = self._split_evenly(run, count)
            if all(self._chunk_fits(chunk) for chunk in chunks):
                return chunks

        return [[line] for line in run]

    def _chunk_fits(self, chunk : list[TranscriptionSegment]) -> bool:
        """
        Whether merging a chunk would stay within the limits a line is held to.

        The merge is performed and measured rather than predicted: how many
        turns render as dialogue depends on the order they fold together in.
        """
        merged = self._merge_run(chunk)

        if (merged.end - merged.start).total_seconds() > self.max_line_seconds:
            return False

        if len(merged.text) > self.max_line_chars:
            return False

        return merged.text.count('\n') <= self.max_newlines

    @staticmethod
    def _split_evenly(run : list[TranscriptionSegment], count : int) -> list[list[TranscriptionSegment]]:
        """Divide a run into `count` pieces differing in length by at most one."""
        base, remainder = divmod(len(run), count)
        chunks : list[list[TranscriptionSegment]] = []
        start = 0

        for index in range(count):
            size = base + (1 if index < remainder else 0)
            chunks.append(run[start:start + size])
            start += size

        return chunks

    def _merge_run(self, run : list[TranscriptionSegment]) -> TranscriptionSegment:
        """
        Combine a run of lines into one, grouping consecutive lines by speaker.

        A turn becomes one piece of text however many fragments it arrived in,
        so only a change of speaker puts a dialogue marker on a new line.
        """
        turns : list[list[TranscriptionSegment]] = []
        for line in run:
            if turns and self._same_turn(turns[-1][-1], line):
                turns[-1].append(line)
            else:
                turns.append([line])

        texts = [JoinWords([line.text for line in turn]) for turn in turns]
        dialogue = len(turns) > 1

        if dialogue:
            text = '\n'.join(turn if turn.startswith('- ') else f'- {turn}' for turn in texts)
        else:
            text = texts[0]

        speakers = {line.speaker for line in run if line.speaker is not None}
        return TranscriptionSegment(
            start=min(line.start for line in run), end=max(line.end for line in run), text=text,
            speaker=None if dialogue else (speakers.pop() if speakers else None),
            language=next((line.language for line in run if line.language), None))

    def _same_turn(self, first : TranscriptionSegment, second : TranscriptionSegment) -> bool:
        """
        Whether two adjacent lines belong to one speaker's turn.

        A line that already carries dialogue markers stands on its own,
        whatever its speaker: it is more than one turn by itself.

        Overlapping lines are separate turns whatever their speakers, because
        diarization does not tell overlapping voices apart. The markers also
        stop a translator reading two utterances as one sentence.
        """
        if self._is_dialogue(first) or self._is_dialogue(second):
            return False

        if second.start < first.end:
            return False

        return first.speaker is None or second.speaker is None or first.speaker == second.speaker
