# Transcription line assembly: findings and plan

Notes from investigating why transcription produces poorly shaped subtitle
lines, and what to do about it. Measurements come from transcribing
*Fist of Fury 1991 II* (93 minutes, Cantonese) with OpenRouter
(`microsoft/mai-transcribe-2`, diarized, 30–180s chunks) and Gemini
(`gemini-3.5-transcribe`, diarized, provider-recommended chunks), captured
with `transcribe.py --capture` and analysed with `scripts/replay_transcription.py`.

Both captures predate the word-ordering fix below, so their word streams are
scrambled; re-capture before drawing new conclusions from them.

## What each provider actually returns

| | OpenRouter / MAI Transcribe 2 | Gemini 3.5 Transcribe | Muse | Qwen Local |
|---|---|---|---|---|
| words | 15,468 | 6,730 | none | from forced aligner |
| parts | 1,164, every one with a speaker | **none** | turns | none |
| word order | **not monotonic in time** | monotonic (8 overlaps in 6,730) | n/a | monotonic, no overlaps |
| median word duration | 0.079s | 0.100s | n/a | n/a |

The two providers are structurally different, and neither generalises to the
other. Any design has to serve both.

### Word timings are a best-effort signal

This is the central point. An engine's word timings are not a measurement in
the sense the code has been treating them as:

- **Order does not follow time.** OpenRouter returns words in transcript order
  with start times that are not monotonic. Sorting by start time therefore
  destroys the text. Fixed by removing the sort from every client; see
  "Word order" below.
- **Text does not match the transcript.** Gemini's word stream covers only
  79–92% of the characters in its own chunk transcript (similarity 0.88–0.96).
  Building lines from the words discards 8–21% of what the engine transcribed.
- **Spans can be nonsense.** OpenRouter parts include a 116.6s part and a
  19.12s part holding nine characters of text.

### Parts are better structured, where they exist

OpenRouter's parts are punctuated, read correctly, and carry a speaker label
on every one. For the same audio they give 1,164 lines against the 1,553 the
word path produces — the word path fragments 33% harder, and much of the
merging machinery exists to undo that fragmentation.

Only 18% of parts exceed `max_line_duration`, and 3% exceed 44 characters, so
most parts need no splitting at all. 31% are under `min_line_duration`, so
sliver merging is still needed whichever source is used.

## Fixed

### Word order (commit "Keep transcribed words in the order they were spoken")

Every client ended its parser with `words.sort(key=lambda w: w.start)`. The
sort came from the Qwen client, where words come from a forced aligner and the
timings are trustworthy, and was copied to the API clients where they are not.

Evidence gathered per client rather than assumed:

- **OpenRouter** — reproduced on a raw 180s payload: 242 words, starts
  non-monotonic, text correct in array order, corrupted after sorting.
  Affected 16 of 38 chunks.
- **Qwen Local** — ran locally: ascending, zero overlaps, sorting changes
  nothing. No-op.
- **Gemini** — 8 overlaps in 6,730 words; its divergence from the transcript
  is dropped content, not reordering. Effectively a no-op.
- **Muse** — sorts whole turns, so it reorders complete utterances by timings
  known to be unreliable.
- **OpenAI** — not verified; same API shape as OpenRouter.

Line spans now take the earliest start and latest end of their words rather
than the first and last, since nothing orders them by time any more.

### Sliver merging (commits on the same branch)

- Merge thresholds became provider settings: `merge_eligible_gap`,
  `same_speaker_merge_eligible_gap`, `can_merge_different_speakers`, offered
  only when the provider will actually label speakers.
- A gap that is a real break is the same gap that blocks a merge, so a line is
  never split at a point the merge step immediately undoes.
- A run is divided into balanced chunks bounded by `max_newlines`, duration and
  characters, rather than chaining fragments without limit.
- Only a fragment may take on a following fragment; a line already long enough
  to read keeps to itself. A fragment stranded by that rule adopts the line
  behind it.
- A run is merged by grouping consecutive lines by speaker, so a speaker
  resuming after an interruption is one turn rather than two dialogue rows.

## Open problems

### Muse: long lines over stretches of silence

The primary Muse failure mode. `_parse_muse_payload` infers a turn's end from
**the next turn's start** whenever `endMs` is missing, so a turn followed by
thirty seconds of silence becomes a thirty-second part. No fix identified;
needs its own investigation with a Muse capture.

### Line source is the wrong way round

`LinesForSegment` takes the words branch whenever `segment.words` is non-empty,
so for a provider that returns both, the good segmentation is discarded in
favour of the best-effort signal. It should be the other way round.

## Plan: parts as the unit of truth

Use the provider's parts when it gives them, and derive parts from the chunk
transcript when it does not. Word timings become a timing and boundary signal
only — never a text source.

### 1. Prefer parts over words

`LinesForSegment` takes `segment.parts` when present. Word timings are then
used only to split a part that exceeds `max_line_duration` or
`max_line_chars` — 18% of them for OpenRouter — and to tighten a span that is
obviously wrong.

### 2. Derive parts for providers that return none

For Gemini there are no parts, but there is a chunk transcript that is better
than the word stream. Split that transcript into parts:

- **Boundaries from punctuation** where the transcript has it, which is where
  it is most reliable, falling back to pauses in the word stream.
- **Timings from the nearest word we can actually place.** Per-word times are
  not needed — only where a part starts and ends. Match transcript text against
  the word stream and take the time of the nearest confidently matched word;
  interpolate across the unmatched 8–21%.

Full character-level alignment is not required and should be avoided: the
useful question is only "what is the closest time we actually know" for each
boundary.

### 3. Keep merging, on better input

Sliver merging stays: 31% of OpenRouter's parts are below `min_line_duration`
even before any splitting. It should operate on parts rather than on
word-grouped fragments, which should reduce how much work it has to do.

### Verification

Re-capture both providers after the ordering fix, then use
`scripts/replay_transcription.py` to compare line counts, lines under
`min_line_duration`, and lines stacked at `max_newlines`, before and after.

## Tools

Temporary, to be removed once line assembly is settled:

- `PySubtrans/Transcription/TranscriptionCapture.py` — captures raw provider
  segments, hooked in `TranscriptionCoordinator._accept_chunk`.
- `transcribe.py --capture PATH` — writes a capture during a normal run.
- `scripts/replay_transcription.py` — replays a capture through the line
  builder with overridden settings; `--compare SETTING v1 v2 …` sweeps one.
