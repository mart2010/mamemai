# The 22.7s turn is the LLM writing too much, not the devices

**Requirements:** TR-951, TR-952, TR-308, TR-309
**Status:** open — three levers identified, none measured yet

## Problem

Total turn latency (median 22.7s) was first attributed to device placement
(TTS/embeddings on CPU) and then, once streaming was fixed and generation was
actually timed, to something nobody had measured before: LLM generation itself
is 73% of the turn (16.6s), driven by reply *length* (1832–2486 characters at a
flat 29.4 tok/s) rather than any stage's latency. A 2500-character answer is 22s
of dead air on its own, and INV-4 (no barge-in) means the user has no way to
escape it.

## Scope

Three levers, in order of expected size, none measured yet:

1. **Bound reply length.** Nothing in the persona prompt or `ProcessTurn` bounds
   it today; FR-101's spoken-reply framing arguably should.
2. **Compact the rolling context sooner.** `rolling_window_size` defaults to 50
   turns and `server.py` never overrides it — LLM first-token latency grew
   2.4× across one 16-turn session (2.16s → 5.15s) because compaction can't
   fire until turn 50, and its summarisation call is awaited inline before
   `TurnResult` returns, landing entirely inside the user's silence.
3. **A faster or smaller model**, weighed against TR-952's language-drift
   reasons for staying on `llama3.1:8b`.

## Out of scope

Streaming synthesis to the client — already shipped (TR-316, see Findings).
Device placement (CPU vs GPU for TTS/embeddings) — settled, see the archived
2026-08-04 device-placement investigation in
`docs/archive/PLAN_2026-07-16_to_2026-08-08.md`.
