# TR-203/TR-204's VAD invalidates how TR-957 was measured

**Requirements:** TR-957, TR-951, TR-201/TR-203/TR-204/TR-205/TR-958
**Status:** in progress — the waste/truncation causes are fixed; the benchmark
re-measurement itself is still open

## Problem

TR-957 benchmarked Whisper `small`/`medium`/`large-v3-turbo` on clean,
complete, Kokoro-synthesised audio — pauses intact, nothing clipped. Real mic
audio, after VAD, is not that input: intra-utterance silence is excised, the
leading edge was clipped before VAD locked on, and trailing quiet syllables were
dropped. TR-957's conclusion (`medium` over `small`) still holds, but the margin
is narrower than the benchmark implies, and part of `small`'s original failure
may have been mangled input rather than model capacity.

## Scope

Once real live-session diagnostics exist (the onset-gate/silence-frame fixes
below print exactly the counters needed), retune the VAD constants from live
`[vad] ignored`/`[stt] skipped` counts and re-read TR-957's STT-model margin
against genuinely representative input.

## Out of scope

The onset-gate and silence-frame fixes themselves — already shipped (see Tasks).
This change is specifically the re-measurement that was blocked on those fixes
existing.
