# Handover: speaker-aware line merging

Branch `speaker-aware-line-merging`, off `main`. PR [#451](https://github.com/machinewrapped/llm-subtrans/pull/451) is open but only covers the first two commits — four more are local and unpushed. Nothing has been force-pushed or rewritten; pushing the branch is a fast-forward.

```
a48eeea Document transcription line assembly findings and plan
0fb5119 Add temporary capture and replay tools for line assembly
7c1bff6 Keep transcribed words in the order they were spoken
d5a52f0 Only a fragment may take on a following fragment
60cb3f8 Fix dialogue merging and forward line limits from callers      <- in PR
47b503f Make line merging a transcription provider setting            <- in PR
ceaf29f Speaker-aware line merging for transcription and preprocessing <- in PR
```

613 tests pass, pyright clean, as of the last commit.

## How this started

A user report: three same-speaker transcription lines (879ms/479ms/1399ms, gaps of 1.56s/0.88s) that should have merged but didn't. Root cause was two hardcoded constants in `TranscriptionLines.py` (`MIN_LINE_SECONDS`, `PAUSE_SPLIT_SECONDS`) that never used the project's real settings and never considered speaker identity as a reason to be *more* lenient, only less.

## What we've done

### 1. Speaker-aware merging (`ceaf29f`, `47b503f`, `60cb3f8`, `d5a52f0`)

- Removed the two hardcoded constants. Reused `min_line_duration` for the sliver threshold. Replaced the pause threshold with a pair of settings: `merge_eligible_gap` (default 0.5s, unknown/changed speaker) and `same_speaker_merge_eligible_gap` (default 1.0s, confirmed same speaker). One threshold now decides both hard boundaries and merge eligibility, so a line is never split at a point the merge step immediately undoes.
- `MergeSlivers` now runs on the `parts` path too (previously only on word-grouped lines), so diarized providers that return sub-segments instead of word timings (Muse, sometimes OpenAI) also get sliver merging.
- A run is divided into balanced chunks bounded by `max_newlines`, duration and characters, instead of chaining fragments onto one line without limit. Fixed a real pathological case: 4 short dialogue turns were landing on one subtitle with 3 newlines; now split into a pair of pairs.
- **Only a fragment may host a following fragment.** A line already long enough to read (`>= min_line_duration`) no longer absorbs trailing fragments — this was producing a near-5-second, 3-row subtitle where one full sentence had two short interjections stapled onto it. A fragment stranded because its predecessor won't host it now adopts the line behind it instead (generalised from a special case that only applied to the first line in a chunk).
- **Merging groups consecutive lines by speaker** rather than folding pairwise. The old fold was sticky: once a pair rendered as dialogue, every later line became another dialogue row regardless of speaker, so a speaker resuming after a one-word interruption came out as two separate turns (`- a / - b / - b` instead of `- a / - b b`). Caught by a Codex review on the PR, along with two related bugs (below).
- The three merge settings (`merge_eligible_gap`, `same_speaker_merge_eligible_gap`, `can_merge_different_speakers`) moved from global Options into **each transcription provider's own settings namespace**, offered only when `TranscriptionProvider.supports_diarization` is true (a `diarize` flag for Gemini/Muse/OpenRouter, the selected model for OpenAI, never for Qwen Local — since without speaker labels they decide nothing).
- `GetOptions` takes an `OptionsScope` (`ALL` / `PER_RUN`) instead of the dialog filtering the returned schema by an `advanced_settings` key list after the fact. Each provider now says inline what belongs in the per-run Transcribe dialog, matching how `TranslationProvider.GetOptions` already reads. `advanced_settings` and the string-based filtering are gone entirely — nothing matches settings by key name anywhere now.

### 2. Review fixes (`60cb3f8`, Codex review on PR #451)

Three findings, all confirmed against the code before fixing:
- `_chunk_fits` predicted how many newlines a merge would produce instead of measuring it, and the prediction was wrong in both directions depending on fold order (over-counted `A,A,B`, under-counted `A,B,B`). Fixed by performing the merge and counting `\n` in the result.
- `_merge_eligible` collapsed a run's speaker to `None` once it held two speakers, losing the same-speaker gap for a continuation. Fixed to use the speaker of the turn the pause is actually measured from (`run[-1]`).
- `TranscriptionDialog._build_command` and `scripts/transcribe.py` forwarded only 3 of the 5 settings `TranscriptionLineBuilder` reads (`max_characters`, `max_line_duration`, `min_split_chars` — missing `min_line_duration` and `max_newlines`), so GUI and CLI runs were silently stuck on hardcoded 0.8s/2 regardless of what the user configured. Fixed by forwarding all five.

### 3. Word ordering bug, found independently (`7c1bff6`)

While capturing real transcription output to investigate a user report of "three short turns in 2.4 seconds," found that every transcription client ends its parser with `words.sort(key=lambda w: w.start)`. This assumes word start times are a reliable ordering key. For an API transcript they are not:

- **OpenRouter**, verified on a raw 180s payload: 242 words, start times not monotonic, but the array order is the correct transcript order. Sorting by start time shuffles the text. Affected 16 of 38 chunks in a feature-length test transcription (scrambled/duplicated characters: `了了`, `就 / 就 / 不` instead of continuous text).
- **Qwen Local**, verified by running it locally: ascending, zero overlaps, sort is a no-op. This is where the sort originated (forced aligner, timings trustworthy) and was then copied to the API clients where the assumption doesn't hold.
- **Gemini**, verified from a real capture: 8 overlaps in 6,730 words, sort is effectively a no-op there too. (Gemini's actual problem is different — see below.)
- **Muse** — not independently verified (no capture taken). `_parse_muse_payload` infers a missing turn's end from *the next turn's start*, then sorts by start anyway. Removing the sort means trusting the array order over possibly-bad Muse timings, which the user's practical experience of Muse ("timings are actually really bad in practice") supports, but this is not measured.
- **OpenAI** — not verified at all. Same API shape as OpenRouter, extrapolated by analogy, not tested. User explicitly deprioritized this ("fuck OpenAI, their models are ancient").

Fixed by removing the sort from all five clients. `_line_from_words` now takes `min(start)`/`max(end)` across a line's words instead of the first and last word's times, since nothing orders them by time now.

Added a test (`test_word_order_survives_unordered_timings`) that pins the actual defect: four words with deliberately jumbled timings must come back in array order. Fails against the pre-fix code.

### 4. Temporary diagnostic tooling (`0fb5119`)

- `PySubtrans/Transcription/TranscriptionCapture.py` — hooks `TranscriptionCoordinator._accept_chunk`, writing each raw provider `TranscriptionSegment` (before the line builder touches it) to JSON. Rewritten after every chunk so an aborted run still leaves a usable capture. Triggered by `transcription_capture_path` in coordinator settings or the `TRANSCRIPTION_CAPTURE_PATH` env var.
- `scripts/transcribe.py --capture PATH` — CLI flag wiring the above.
- `scripts/replay_transcription.py` — loads a capture and re-runs it through `TranscriptionLineBuilder` with settings overridden from the command line, including `--compare SETTING v1 v2 …` to sweep one setting across values without re-transcribing. Reports line count, lines under `min_line_duration`, lines at the newline limit.

Both explicitly marked `TEMPORARY` in their docstrings, with removal instructions, pending a decision on whether they become permanent (see Outstanding below).

### 5. Write-up (`a48eeea`)

`docs/transcription-line-assembly.md` — the findings above plus the parts-first plan (next section), with a pointer added from `docs/architecture.md`'s `TranscriptionLines` entry.

## What we've rejected

- **Widening Muse's merge-gap defaults** (0.8s/1.5s instead of the shared 0.5s/1.0s). Initially added, then removed after the user pointed out the reasoning was backwards: Muse's turns are rarely slivers in the first place (its failure mode is turns that are too *long*, covering silence — see below), so unreliable boundaries on already-long segments argue for *less* merge latitude, not more. No provider currently overrides the shared defaults.
- **A base-class settings-injection mechanism** (`DEFAULT_LINE_ASSEMBLY_SETTINGS` dict + `setdefault` loop + a `saved` constructor parameter) for giving providers their own defaults for the three merge settings. Built, then torn out as overcomplicated — each provider now just declares the three keys in its own settings dict with `settings.get_float(key, default)`, identically to every other provider setting. This also fixed a real latent bug the machinery had introduced: cherry-picking keys into a fresh dict per provider was silently discarding saved values for keys not in that dict.
- **`ScopedOptions`/`LineAssemblyOptions`/`SetupOptions` base-class methods** for filtering `GetOptions` output by scope. Built, then dissolved after the user compared it unfavourably to how `TranslationProvider.GetOptions` actually reads (inline `if` conditions building the dict directly, no post-hoc filtering by key). Converted all 5 transcription providers to the same inline-conditional style. This also let us delete `advanced_settings` entirely, which was the same string-matching pattern one layer up.
- **A `bool` parameter for `GetOptions` scope** — replaced with an `OptionsScope(str, Enum)` (`ALL`/`PER_RUN`) per explicit user instruction ("Don't use a bool arg, use an enum type").
- **Various naming**: `PAUSE_SPLIT_SECONDS`/`MIN_LINE_SECONDS` → `max_gap_for_merge` → split into `merge_eligible_gap` / `same_speaker_merge_eligible_gap` after several rounds of "sounds like it overrides X" / "eligibility is the right frame" feedback. `merge_different_speakers` → `can_merge_different_speakers` (reads as permission, not instruction).
- **Editorializing docstrings/comments** — several were written with explanatory rationale beyond what the code needs, then trimmed to state the fact and no more, per explicit feedback ("useless editorial commentary").

## What we know

- OpenRouter (MAI Transcribe 2) returns word timings **and** parts for the same audio; parts are structurally better (punctuated, 100% speaker coverage, 33% fewer lines than the word path produces) but were never used because `LinesForSegment` prefers `words` whenever present.
- Gemini returns **words only, never parts**. Its word stream covers only 79–92% (similarity 0.88–0.96) of its own chunk transcript — i.e. building lines from words throws away 8–21% of what Gemini actually transcribed. This is a content gap, not an ordering problem (unlike OpenRouter).
- Muse's known failure mode (from user's practical experience, not this session's capture — no Muse capture was taken) is turns stretching across silence, traced to `_parse_muse_payload` inferring a missing `endMs` as the *next turn's start time*.
- The word-order sort was safe to remove for every client we could measure (OpenRouter fixed a real bug; Qwen and Gemini were no-ops). Muse and OpenAI are unverified extrapolations.
- **Re-captured after the word-order fix** (2026-09-22). The Baseline section of `docs/transcription-line-assembly.md` has the full tables. In short:
  - OpenRouter words: 1,562 lines, 285 short, 23 three-row, 6 of them splittable (pre-fix: 1,553 / 307 / 42 / 6). Parts only: 1,125 / 189 / 12 / 1.
  - The fix is confirmed on real data: 24 words out of time order in 16 chunks, and word/transcript similarity 1.00 in every chunk.
  - Remaining short lines are mostly cut off by gaps on both sides, or trail a full line (the "only a fragment hosts" rule). Merging is working as designed.
- **Gemini is unreliable at its recommended ~18-minute chunks.** Each capture has one broken chunk out of five, and both runs reported success. The old capture's first chunk is near-empty. The new capture's third chunk is a repetition loop. Measure with the broken chunk removed.
- Gemini word timings are in 0.1s steps, 71% of them exactly 0.1s long. Its short lines (313 of 875) are nearly all isolated one- or two-character interjections that no merge setting can reach.

## What we don't know

- Whether to extend a short line's display time into the silence after it. That is the only lever left for Gemini's isolated interjections, and a product decision rather than a merge setting.
- Whether `merge_eligible_gap` should be 0.8s. That cuts short lines by 18–25% on every source, at the cost of a few more three-row lines. Not decided.
- Whether OpenAI's word timings are monotonic/reliable — never captured or probed, only assumed by analogy to OpenRouter (same underlying API shape, per project code, but unverified).
- Whether Muse's array order is trustworthy where its timings are known bad — no capture taken, only inferred from the shape of `_parse_muse_payload`.
- What a real Muse capture's silence-stretching lines actually look like in volume/severity — flagged as needing its own investigation, not started.
- Whether the "prefer parts over words" plan (see below) will interact cleanly with the balanced-chunking/sliver-merge code as it exists now, or needs its own adjustments — untested, plan only.

## What still needs doing

Ordered roughly by what the user asked for next vs. what's still open-ended:

1. **Push the 4 unpushed commits and update PR #451**, or open a follow-up PR — not yet done, not yet asked for explicitly.
2. ~~Re-capture both OpenRouter and Gemini~~ Done 2026-09-22; see "What we know". New open item: detect and retry degenerate Gemini chunks (see `docs/transcription-line-assembly.md`, Open problems).
3. **Implement the parts-first plan** from `docs/transcription-line-assembly.md`:
   - ~~`LinesForSegment` prefers `segment.parts` when present~~ Done 2026-09-22, including overlap merging with dialogue markers and time-ordered merging; results in the doc. Only OpenRouter is affected.
   - Before step 2, take a Qwen capture (local, free): Qwen has the same text + words shape as Gemini, is the CLI default, and has the most trustworthy words, so it is the provider most likely to regress under step 2.
   - For providers with no parts (Gemini), derive parts from the chunk transcript: split on punctuation primarily, falling back to word-stream pauses; take each part boundary's time from the nearest word we can confidently match against the transcript (via something like `difflib.SequenceMatcher`), interpolating across unmatched stretches. User was explicit that full character-level alignment is unnecessary — only boundary times are needed.
   - Sliver merging should end up operating on parts rather than word-grouped fragments.
4. **Muse investigation** — take a Muse capture, quantify the silence-stretching problem, and figure out whether there's a fix (e.g. capping an inferred end at some maximum duration, or requiring corroborating evidence before trusting a missing `endMs`).
5. **Decide the fate of the temporary tooling** — `TranscriptionCapture.py`, the `--capture` flag, and `replay_transcription.py` are explicitly marked temporary. Either promote them to permanent developer tooling or remove them once line assembly work concludes. Not decided yet.
6. **OpenAI verification**, if it's ever worth doing given the user's stated lack of interest in that provider — lowest priority by explicit user sentiment, not technical merit.

## Files touched (for orientation)

Core logic: `PySubtrans/Transcription/TranscriptionLines.py`, `TranscriptionProvider.py`, `TranscriptionCoordinator.py`, and all 5 files under `PySubtrans/Transcription/Providers/Clients/`.

Settings/UI: `PySubtrans/Options.py` (three keys removed again — they live on providers now, not in global settings), `GuiSubtrans/SettingsDialog.py`, `GuiSubtrans/Widgets/TranscriptionDialog.py`.

Tests: `tests/PySubtransTests/test_Transcription.py` (bulk of new coverage), `tests/IntegrationTests/test_Transcription{Muse,OpenAI,OpenRouter,Gemini,Qwen}.py` (per-run scope tests replacing the old `advanced_settings` schema tests), `tests/GuiTests/test_TranscriptionDialog.py`.

New: `PySubtrans/Transcription/TranscriptionCapture.py`, `scripts/replay_transcription.py`, `docs/transcription-line-assembly.md`, this file.

Scratch (not committed, gitignored under `test_results/`): captures `fist_of_fury_{openrouter,gemini}.json` (current) and `fist_of_fury_{openrouter,gemini}_presort_fix.json` (pre-fix, kept for comparison), with matching `.vtt` outputs, `test_results/raw_probe.py`, `test_results/gemini_probe.py`, `test_results/qwen_probe.py` — one-off probes used to verify the word-order claims per client. Worth keeping around if the Muse/OpenAI verification work happens later, otherwise safe to delete.
