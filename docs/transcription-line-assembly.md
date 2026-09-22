# Transcription line assembly: findings and plan

Notes from investigating why transcription produces poorly shaped subtitle lines, and what to do about it. Measurements come from transcribing *Fist of Fury 1991 II* (93 minutes, Cantonese) with OpenRouter (`microsoft/mai-transcribe-2`, diarized, 30–180s chunks) and Gemini (`gemini-3.5-transcribe`, diarized, provider-recommended chunks), captured with `transcribe.py --capture` and analysed with `scripts/replay_transcription.py`.

Figures are from captures re-taken after the word-ordering fix below. See "Baseline" for the numbers and how they compare with the pre-fix captures.

## What each provider actually returns

| | OpenRouter / MAI Transcribe 2 | Gemini 3.5 Transcribe | Muse | Qwen Local |
|---|---|---|---|---|
| words | 15,483 | 6,578 (4 healthy chunks) | none | from forced aligner |
| parts | 1,160, every one with a speaker | **none** | turns | none |
| word order | **not monotonic in time** (24 words in 16 of 38 chunks) | monotonic (1 exception) | n/a | monotonic, no overlaps |
| word timing resolution | ~0.02s | **0.1s steps**, 71% of words exactly 0.1s long | n/a | n/a |
| word/transcript similarity | 1.00 in every chunk | 0.94–0.97 | n/a | n/a |

The two providers are structurally different, and neither generalises to the other. Any design has to serve both.

### Word timings are a best-effort signal

This is the central point. An engine's word timings are not a measurement in the sense the code has been treating them as:

- **Order does not follow time.** OpenRouter returns words in transcript order with start times that are not monotonic. Sorting by start time therefore destroys the text. Fixed by removing the sort from every client; see "Word order" below.
- **Text does not match the transcript.** Gemini's word stream is 0.94–0.97 similar to its own chunk transcript, so building lines from the words drops or alters a few percent of what the engine transcribed. (The earlier 79–92% figure was measured on a capture with a degenerate chunk in it.)
- **Spans can be nonsense.** OpenRouter parts include a 19.1s part holding nine characters of text, and 44 of 1,160 parts overlap the part before them by more than 0.1s. Some overlaps are the same line transcribed twice, once in Cantonese and once in Mandarin, with different speakers.
- **Whole chunks can be garbage.** Each Gemini capture, at its recommended ~18-minute chunks, contained one broken chunk out of five. One capture returned 85 characters for 18 minutes of dialogue. The other fell into a repetition loop, repeating one passage dozens of times, with word timings cycling over the same 70 seconds. Both runs reported success.

### Parts are better structured, where they exist

OpenRouter's parts are punctuated, read correctly, and carry a speaker label on every one. After merging, the same audio gives 1,125 lines from parts against 1,562 from words: the word path produces 39% more lines, and much of the merging machinery exists to undo that fragmentation.

Only 18% of parts exceed `max_line_duration`, and 2% exceed 44 characters, so most parts need no splitting at all. 31% are under `min_line_duration`, so sliver merging is still needed whichever source is used.

## Baseline

Measured with `replay_transcription.py` at default settings (`min_line_duration` 0.8s, gaps 0.5s / 1.0s, `max_newlines` 2). "Splittable" means a three-row line has a row boundary where both halves would reach `min_line_duration`, estimated from character share.

| Capture | Lines | Under 0.8s | Three-row | Splittable |
|---|---|---|---|---|
| OpenRouter, words, pre-fix | 1,553 | 307 | 42 | 6 |
| OpenRouter, words | 1,562 | 285 | 23 | 6 |
| OpenRouter, parts only | 1,125 | 189 | 12 | 1 |
| Gemini, pre-fix, 4 healthy chunks | 939 | 327 | 1 | 0 |
| Gemini, 4 healthy chunks | 875 | 313 | 1 | 0 |

Pre-fix and post-fix OpenRouter are separate transcriptions, so the difference combines the ordering fix with run-to-run variation. Unscrambled words roughly halve the three-row lines.

### Why short lines survive

Each remaining short line classified by its neighbours:

| | Cut off by gaps both sides | Trails a full line (by design) | Blocked by size limits | Overlapping part |
|---|---|---|---|---|
| OpenRouter, words | 179 | 86 | 14 | 6 |
| OpenRouter, parts | 96 | 57 | 30 | 6 |
| Gemini | 291 | 22 | 0 | 0 |

Merging is doing what it is designed to do. Nearly all of Gemini's short lines are one- or two-character interjections, isolated in time, whose duration is a 0.1s timing step per character. No merge setting can reach them. Making them readable would mean extending a short line's display time into the silence after it, which is a separate decision from merging.

### Settings sweeps

Line count / lines under 0.8s:

| Setting | OpenRouter words | OpenRouter parts | Gemini (healthy) |
|---|---|---|---|
| defaults | 1,562 / 285 | 1,125 / 189 | 875 / 313 |
| `same_speaker_merge_eligible_gap` 0.75 | 1,579 / 307 | 1,125 / 189 | 919 / 346 |
| `same_speaker_merge_eligible_gap` 1.5 | 1,532 / 241 | 1,122 / 184 | 834 / 257 |
| `merge_eligible_gap` 0.3 | 1,610 / 346 | 1,162 / 236 | 939 / 387 |
| `merge_eligible_gap` 0.8 | 1,527 / 234 | 1,097 / 149 | 817 / 235 |

The parts path is insensitive to the same-speaker gap: 85% of adjacent OpenRouter parts change speaker, and only 38 same-speaker pairs have a gap in the range the setting moves. Widening `merge_eligible_gap` to 0.8s cuts short lines by 18–25% on every source, at the cost of a few more three-row lines (OpenRouter words: 26, 10 of them splittable).

## Fixed

### Word order (commit "Keep transcribed words in the order they were spoken")

Every client ended its parser with `words.sort(key=lambda w: w.start)`. The sort came from the Qwen client, where words come from a forced aligner and the timings are trustworthy, and was copied to the API clients where they are not.

Evidence gathered per client rather than assumed:

- **OpenRouter** — reproduced on a raw 180s payload: 242 words, starts non-monotonic, text correct in array order, corrupted after sorting. Affected 16 of 38 chunks.
- **Qwen Local** — ran locally: ascending, zero overlaps, sorting changes nothing. No-op.
- **Gemini** — 8 overlaps in 6,730 words; its divergence from the transcript is dropped content, not reordering. Effectively a no-op.
- **Muse** — sorts whole turns, so it reorders complete utterances by timings known to be unreliable.
- **OpenAI** — not verified; same API shape as OpenRouter.

Line spans now take the earliest start and latest end of their words rather than the first and last, since nothing orders them by time any more.

### Sliver merging (commits on the same branch)

- Merge thresholds became provider settings: `merge_eligible_gap`, `same_speaker_merge_eligible_gap`, `can_merge_different_speakers`, offered only when the provider will actually label speakers.
- A gap that is a real break is the same gap that blocks a merge, so a line is never split at a point the merge step immediately undoes.
- A run is divided into balanced chunks bounded by `max_newlines`, duration and characters, rather than chaining fragments without limit.
- Only a fragment may take on a following fragment; a line already long enough to read keeps to itself. A fragment stranded by that rule adopts the line behind it.
- A run is merged by grouping consecutive lines by speaker, so a speaker resuming after an interruption is one turn rather than two dialogue rows.

## Open problems

### Muse: long lines over stretches of silence

The primary Muse failure mode. `_parse_muse_payload` infers a turn's end from **the next turn's start** whenever `endMs` is missing, so a turn followed by thirty seconds of silence becomes a thirty-second part. No fix identified; needs its own investigation with a Muse capture.

### Line source is the wrong way round

`LinesForSegment` takes the words branch whenever `segment.words` is non-empty, so for a provider that returns both, the good segmentation is discarded in favour of the best-effort signal. It should be the other way round.

### Gemini: degenerate chunks go undetected

A chunk that comes back near-empty or stuck in a repetition loop is accepted as a successful transcription. A sanity check (characters per second against the chunk length, or repeated n-grams) could reject and retry it. Smaller chunks may also make it less likely. Not investigated.

## Plan: parts as the unit of truth

Use the provider's parts when it gives them, and derive parts from the chunk transcript when it does not. Word timings become a timing and boundary signal only — never a text source.

### 1. Prefer parts over words

`LinesForSegment` takes `segment.parts` when present. Word timings are then used only to split a part that exceeds `max_line_duration` or `max_line_chars` — 18% of them for OpenRouter — and to tighten a span that is obviously wrong.

### 2. Derive parts for providers that return none

For Gemini there are no parts, but there is a chunk transcript that is better than the word stream. Split that transcript into parts:

- **Boundaries from punctuation** where the transcript has it, which is where it is most reliable, falling back to pauses in the word stream.
- **Timings from the nearest word we can actually place.** Per-word times are not needed — only where a part starts and ends. Match transcript text against the word stream and take the time of the nearest confidently matched word; interpolate across the unmatched few percent.

Full character-level alignment is not required and should be avoided: the useful question is only "what is the closest time we actually know" for each boundary.

### 3. Keep merging, on better input

Sliver merging stays: 31% of OpenRouter's parts are below `min_line_duration` even before any splitting. It should operate on parts rather than on word-grouped fragments, which should reduce how much work it has to do.

### Verification

Replay the captures in `test_results/` and compare against the Baseline tables above. `--source parts` already previews step 1 for OpenRouter. Leave out Gemini's degenerate chunk, or the repetition loop dominates every figure.

## Tools

Temporary, to be removed once line assembly is settled:

- `PySubtrans/Transcription/TranscriptionCapture.py` — captures raw provider segments, hooked in `TranscriptionCoordinator._accept_chunk`.
- `transcribe.py --capture PATH` — writes a capture during a normal run.
- `scripts/replay_transcription.py` — replays a capture through the line builder with overridden settings; `--compare SETTING v1 v2 …` sweeps one, `--source parts|words` forces the line source, `--quiet` prints only the summary. It also reports word-order violations and word/transcript similarity per capture.
