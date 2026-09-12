from dataclasses import dataclass, field, field

from PySubtrans.Helpers.Localization import _
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import SubtitleError
from PySubtrans.Transcription.AudioExtractor import AudioExtractor

from collections.abc import Callable, Generator
from datetime import timedelta

@dataclass
class AudioChunk:
    """
    A coherent span of audio to transcribe as one unit.

    Timings are absolute offsets from the start of the source media.
    """
    start : timedelta = field(default_factory=lambda: timedelta(seconds=0))
    end : timedelta = field(default_factory=lambda: timedelta(seconds=0))


class AudioChunker:
    """
    Splits media into coherent audio-only chunks for transcription.

    Cuts land inside detected silence where possible so chunks hold
    complete utterances (better for both accuracy and speaker continuity).
    A hard cap guarantees no chunk exceeds backend length limits.
    Chunks never overlap: each engine returns flat text per chunk, so
    overlap would transcribe the same speech twice.
    """
    def __init__(self, settings : SettingsType|None = None):
        self.settings : SettingsType = settings or SettingsType()
        self.ValidateChunkBounds(self.min_chunk_seconds, self.max_chunk_seconds)

        self.extractor : AudioExtractor = AudioExtractor(self.settings)

    @staticmethod
    def ValidateChunkBounds(min_chunk_seconds : float, max_chunk_seconds : float) -> None:
        """Reject chunk settings that cannot produce a complete plan."""
        if min_chunk_seconds > max_chunk_seconds:
            raise SubtitleError(_(
                "Minimum chunk length cannot exceed maximum chunk length"
            ))

    @property
    def min_chunk_seconds(self) -> float:
        """Minimum chunk length; shorter spans merge into neighbours."""
        return self.settings.get_float('min_chunk_seconds') or 4.0

    @property
    def max_chunk_seconds(self) -> float:
        """
        Hard cap per chunk. The local engine returns flat text per chunk
        with no word timings, so chunk boundaries ARE the subtitle timings:
        keep the cap low enough that worst-case lines stay usable.
        """
        return self.settings.get_float('max_chunk_seconds') or 60.0

    @property
    def lookahead_seconds(self) -> float:
        """
        How far past the cap to scan for a silence before hard-cutting.

        Hard cuts can land mid-sentence or mid-word, so an over-long
        stretch extends to the next natural pause when one is nearby.
        """
        return self.settings.get_float('lookahead_seconds') or 30.0

    def PlanChunks(self, media_path : str, track_index : int = 0,
                   duration_cb : Callable[[timedelta], None]|None = None) -> list[AudioChunk]:
        """
        Return the ordered chunk plan for a media file (no audio extracted yet).
        """
        return list(self.PlanChunksStream(media_path, track_index, duration_cb))

    def PlanChunksStream(self, media_path : str, track_index : int = 0,
                         duration_cb : Callable[[timedelta], None]|None = None) -> Generator[AudioChunk, None, None]:
        """
        Yield chunks as silence detection streams in, so the caller can
        transcribe finalized chunks while later ones are still being planned.

        A chunk is finalized once silences past its decision window (including
        the extension lookahead) have arrived; boundaries match PlanChunks
        exactly, including tail absorption into the last chunk.
        """
        duration = self.extractor.GetDuration(media_path)
        total = duration.total_seconds()
        if total <= 0:
            raise SubtitleError(_("Media file has no playable duration"))

        if duration_cb:
            duration_cb(duration)

        # Media shorter than the minimum chunk is transcribed whole
        if total < self.min_chunk_seconds:
            yield AudioChunk(start=timedelta(seconds=0), end=duration)
            return

        silences = self.extractor.DetectSilencesStream(media_path, track_index)
        pending : tuple[timedelta, timedelta]|None = next(silences, None)
        buffered : list[tuple[timedelta, timedelta]] = []
        stream_done : bool = pending is None

        def fill(limit : timedelta) -> None:
            """Buffer silence events up to the planning horizon."""
            nonlocal pending, stream_done
            while not stream_done and pending is not None and pending[0] <= limit:
                buffered.append(pending)
                pending = next(silences, None)
                if pending is None:
                    stream_done = True

        cursor = timedelta(seconds=0)
        silence_index = 0

        while cursor < duration and (duration - cursor).total_seconds() >= self.min_chunk_seconds:
            target = cursor + timedelta(seconds=self.max_chunk_seconds)
            window = min(target, duration)

            # Prefer a silence inside the window, then one a little past the cap
            fill(window)
            cut = self._next_silence_cut(buffered, silence_index, cursor, window)

            if cut is None and target < duration:
                lookahead = target + timedelta(seconds=self.lookahead_seconds)
                fill(lookahead)
                cut = self._next_silence_cut(buffered, silence_index, cursor, lookahead, after=target)

            if cut is not None:
                # Cut at the start of the silence and resume after it, so
                # pauses are not transcribed as leading dead air
                end, next_cursor, silence_index = cut
            elif target >= duration:
                end = next_cursor = duration
            else:
                end = next_cursor = target

            # A remainder too small to stand alone is absorbed into this chunk
            if (duration - next_cursor).total_seconds() < self.min_chunk_seconds:
                end = next_cursor = duration

            yield AudioChunk(start=cursor, end=end)
            cursor = next_cursor

    def _next_silence_cut(self, silences : list[tuple[timedelta, timedelta]], index : int,
                           cursor : timedelta, limit : timedelta,
                           after : timedelta|None = None) -> tuple[timedelta, timedelta, int]|None:
        """
        Score candidate silences within (after, limit] by position times
        gap length, returning the chosen silence's start and end and the
        index to resume scanning from.
        A long pause earlier beats a short one nearer the cap, so chunks
        break on coherent boundaries instead of arbitrary times; position
        still counts, so dialogue fills toward the cap (fewer requests,
        stable speaker identities for diarization). Latest wins ties.
        Spans below the minimum length are passed over as cut candidates —
        their audio stays inside the surrounding chunk, so no speech is
        ever dropped; only the cut point moves later.

        Example: a 5s gap at 20s scores 20*5=100, while a 1s gap at 55s
        scores 55*1=55 — so the long pause wins despite being earlier.
        """
        lower = after or cursor
        best : tuple[timedelta, timedelta, int]|None = None
        best_score = -1.0

        while index < len(silences):
            silence_start, silence_end = silences[index]
            if silence_start <= cursor:
                index += 1
                continue

            if silence_start > limit:
                break

            span = (silence_start - cursor).total_seconds()
            if silence_start > lower and span >= self.min_chunk_seconds:
                gap = (silence_end - silence_start).total_seconds()
                score = span * gap
                if score >= best_score:
                    best_score = score
                    best = (silence_start, silence_end, index + 1)

            index += 1

        return best