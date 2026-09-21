from __future__ import annotations

import logging
from datetime import timedelta

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

# Split-point scoring for over-long utterances: the pause at a boundary is
# the primary signal, weighted by how central the boundary is. The floor
# lets zero-pause boundaries still resolve by centrality; the bonuses and
# penalty nudge toward clause ends and away from stranding short words.
PAUSE_SCORE_FLOOR = 0.1
SENTENCE_END_BONUS = 0.5
CLAUSE_END_BONUS = 0.15
SHORT_WORD_PENALTY = 0.05
SHORT_WORD_CHARS = 3


def SpanLabel(span : AudioChunk|TranscriptionSegment) -> str:
    """Human-readable start-end label for a chunk or segment, in seconds."""
    return f"{span.start.total_seconds():.1f}s-{span.end.total_seconds():.1f}s"


class TranscriptionLineBuilder:
    """
    Turns transcribed chunks into timed subtitle lines.

    Word timings group into utterances at pauses, speaker changes and
    sentence punctuation; utterances over the character or duration limit
    are split at their best pause. Provider sub-segments without word
    timings become rebased lines. Brief slivers merge into their
    neighbours. No provider or audio dependencies.
    """
    def __init__(self, max_line_chars : int, max_line_seconds : float, min_split_chars : int = 3,
                 min_line_seconds : float = 0.8, merge_eligible_gap : float = DEFAULT_MERGE_ELIGIBLE_GAP_SECONDS,
                 same_speaker_merge_eligible_gap : float = DEFAULT_SAME_SPEAKER_MERGE_ELIGIBLE_GAP_SECONDS,
                 max_newlines : int = 2, can_merge_different_speakers : bool = True):
        self.max_line_chars : int = max_line_chars
        self.max_line_seconds : float = max_line_seconds
        self.min_split_chars : int = min_split_chars
        self.min_line_seconds : float = min_line_seconds
        self.merge_eligible_gap : float = merge_eligible_gap
        self.same_speaker_merge_eligible_gap : float = same_speaker_merge_eligible_gap
        self.max_newlines : int = max_newlines
        self.can_merge_different_speakers : bool = can_merge_different_speakers

    def LinesForSegment(self, segment : TranscriptionSegment) -> list[TranscriptionSegment]:
        """
        Turn a transcribed chunk into timed subtitle lines.

        Word timings group into lines; provider sub-segments without word
        timings become rebased lines. Either way brief slivers are merged
        back into their neighbours. A chunk with neither stays one line
        over its true chunk span: coarse but honest, and the text was
        already paid for, so it is kept rather than thrown away.
        """
        if segment.words:
            lines = self._group_words(segment.words, segment)
            return lines or [segment]

        if segment.parts:
            rebased = [self._rebase_part(part, segment) for part in segment.parts if part.text.strip()]

            # Providers that segment for us still strand fragments, which
            # translate badly in isolation
            merged = self.MergeSlivers(rebased)
            for line in merged:
                self.WarnIfOverlong(line)
            return merged or [segment]

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

    def MergeSlivers(self, lines : list[TranscriptionSegment]) -> list[TranscriptionSegment]:
        """
        Merge brief adjacent lines, preserving pauses and dialogue turns.

        A run too long for one subtitle is divided into even pieces.
        """
        if len(lines) < 2:
            return lines

        merged : list[TranscriptionSegment] = []
        for run in self._sliver_runs(lines):
            merged.extend(self._merge_run(chunk) for chunk in self._balanced_chunks(run))

        return merged

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

        return self.MergeSlivers(lines)

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

    def _fits(self, words : list[WordTiming]) -> bool:
        """Whether a run of words is within the duration and character limits."""
        seconds = (words[-1].end - words[0].start).total_seconds()
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
        start = words[0].start
        half_span = (words[-1].end - start).total_seconds() / 2.0
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
        """Build one absolute-timed line from a run of chunk-relative words."""
        start, end = self._clamped_span(segment, words[0].start, words[-1].end)
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
        instead, whatever that line's length.
        """
        runs : list[list[TranscriptionSegment]] = [[lines[0]]]

        for line in lines[1:]:
            run = runs[-1]
            stranded = len(run) == 1

            if (self._is_sliver(run[-1])
                    and (self._is_sliver(line) or stranded)
                    and self._merge_eligible(run, line)):
                run.append(line)
            else:
                runs.append([line])

        return runs

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
            start=run[0].start, end=max(line.end for line in run), text=text,
            speaker=None if dialogue else (speakers.pop() if speakers else None),
            language=next((line.language for line in run if line.language), None))

    def _same_turn(self, first : TranscriptionSegment, second : TranscriptionSegment) -> bool:
        """
        Whether two adjacent lines belong to one speaker's turn.

        A line that already carries dialogue markers stands on its own,
        whatever its speaker: it is more than one turn by itself.
        """
        if self._is_dialogue(first) or self._is_dialogue(second):
            return False

        return first.speaker is None or second.speaker is None or first.speaker == second.speaker
