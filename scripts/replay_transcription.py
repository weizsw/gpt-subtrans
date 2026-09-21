"""
TEMPORARY tool for tuning transcription line assembly.

Replays a capture written by TranscriptionCapture through the line builder,
so the effect of a settings change can be seen without transcribing again.

    set TRANSCRIPTION_CAPTURE_PATH=capture.json     # then transcribe once
    python scripts/replay_transcription.py capture.json
    python scripts/replay_transcription.py capture.json --min-line-duration 1.2
    python scripts/replay_transcription.py capture.json --compare min_line_seconds 0.6 0.8 1.0

Remove this script, PySubtrans/Transcription/TranscriptionCapture.py and its
call in TranscriptionCoordinator._accept_chunk once line assembly is settled.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySubtrans.Options import Options
from PySubtrans.Transcription.TranscriptionCapture import LoadCapture
from PySubtrans.Transcription.TranscriptionLines import TranscriptionLineBuilder
from PySubtrans.Transcription.TranscriptionSegment import TranscriptionSegment


def BuildLines(segments : list[TranscriptionSegment], **overrides) -> list[TranscriptionSegment]:
    """Run the line builder over captured segments with the given settings."""
    options = Options()
    settings = {
        'max_line_chars': options.get_int('max_characters') or 120,
        'max_line_seconds': options.get_float('max_line_duration') or 4.0,
        'min_split_chars': options.get_int('min_split_chars') or 3,
        'min_line_seconds': options.get_float('min_line_duration') or 0.8,
        'max_newlines': options.get_int('max_newlines') or 2,
    }
    settings.update({key: value for key, value in overrides.items() if value is not None})

    builder = TranscriptionLineBuilder(**settings)

    lines : list[TranscriptionSegment] = []
    for segment in segments:
        lines.extend(builder.LinesForSegment(segment))

    return lines


def Timecode(value : timedelta) -> str:
    """Readable m:ss.mmm for a media offset."""
    total = value.total_seconds()
    return f"{int(total // 60):02d}:{total % 60:06.3f}"


def Describe(segments : list[TranscriptionSegment]) -> None:
    """Print a summary of what the provider gave us."""
    words = sum(len(segment.words) for segment in segments)
    parts = sum(len(segment.parts) for segment in segments)
    speakers = {part.speaker for segment in segments for part in segment.parts if part.speaker}
    speakers |= {word.speaker for segment in segments for word in segment.words if word.speaker}

    print(f"{len(segments)} chunks, {words} word timings, {parts} parts, "
          f"{len(speakers)} speakers{' ' + repr(sorted(speakers)) if speakers else ''}")


def Report(lines : list[TranscriptionSegment], min_line_seconds : float, max_newlines : int) -> None:
    """Print each line, flagging the ones worth looking at."""
    short = 0
    stacked = 0

    for line in lines:
        duration = (line.end - line.start).total_seconds()
        newlines = line.text.count('\n')
        flags = ''
        if duration < min_line_seconds:
            flags += ' [short]'
            short += 1
        if newlines >= max_newlines:
            flags += f' [{newlines + 1} rows]'
            stacked += 1

        text = line.text.replace('\n', ' / ')
        print(f"[{Timecode(line.start)} --> {Timecode(line.end)}] {duration:5.2f}s{flags} {text}")

    print(f"\n{len(lines)} lines, {short} under {min_line_seconds}s, {stacked} at the newline limit")


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a captured transcription through the line builder")
    parser.add_argument('capture', help="Capture file written by TranscriptionCapture")
    parser.add_argument('--min-line-duration', type=float, help="Below this a line is a fragment to be merged")
    parser.add_argument('--max-line-duration', type=float, help="Longest a merged line may be")
    parser.add_argument('--max-characters', type=int, help="Longest a merged line may be, in characters")
    parser.add_argument('--max-newlines', type=int, help="Most rows a merged line may have, less one")
    parser.add_argument('--merge-eligible-gap', type=float, help="Widest gap that can be merged across")
    parser.add_argument('--same-speaker-gap', type=float, help="As above, for one speaker continuing")
    parser.add_argument('--no-merge-speakers', action='store_true', help="Keep separate speakers on separate lines")
    parser.add_argument('--compare', nargs='+', metavar=('SETTING', 'VALUE'),
                        help="Builder setting and the values to compare, e.g. --compare min_line_seconds 0.6 1.0")
    args = parser.parse_args()

    segments = LoadCapture(args.capture)
    Describe(segments)

    overrides = {
        'min_line_seconds': args.min_line_duration,
        'max_line_seconds': args.max_line_duration,
        'max_line_chars': args.max_characters,
        'max_newlines': args.max_newlines,
        'merge_eligible_gap': args.merge_eligible_gap,
        'same_speaker_merge_eligible_gap': args.same_speaker_gap,
        'can_merge_different_speakers': False if args.no_merge_speakers else None,
    }

    if args.compare:
        setting, *values = args.compare
        for value in values:
            print(f"\n=== {setting} = {value} ===")
            trial = dict(overrides)
            trial[setting] = float(value) if '.' in value else int(value)
            lines = BuildLines(segments, **trial)
            Report(lines, float(trial.get('min_line_seconds') or 0.8), int(trial.get('max_newlines') or 2))
        return 0

    print()
    lines = BuildLines(segments, **overrides)
    Report(lines, overrides['min_line_seconds'] or 0.8, overrides['max_newlines'] or 2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
