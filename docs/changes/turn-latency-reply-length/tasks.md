# Tasks

- [x] **Streaming to the client** (TR-316, fixed 2026-08-04) — see Findings
      below. `ProcessTurn.execute` now delivers each synthesised segment
      through an `emit_audio` sink from inside the generation loop instead of
      returning a completed `audio_chunks` list.
- [ ] **Bound reply length** — measure, then decide whether/how to cap it.
- [ ] **Compact `rolling_window_size` sooner**, and make the compaction call
      not block the turn it fires on (or overlap it with playback).
- [ ] **Evaluate a faster/smaller model** against TR-952's language-drift
      constraints.
- [ ] After any of the above ships: re-measure `[latency] First audio sent` and
      whether playback ever starves, live.

## Findings — streaming (2026-08-03, found; 2026-08-04, fixed as TR-316)

`TurnResult.audio_chunks` was a `list[bytes]`; every sentence was synthesised
before `execute` returned, so `[latency] Total to first audio` (median ~9.6s)
measured when the server *synthesised* its first chunk, not when the user heard
anything — the real wait was the total-turn figure, median 22.7s of dead air
with no barge-in to escape it.

**Drift, not a deliberate simplification**: `git show 5e83853^` — the server
before the domain-layer refactor — synthesised each sentence inside the token
loop and sent it immediately. The refactor replaced that with
`TurnResult.audio_chunks` and the behaviour was lost in the move.

Fixed 2026-08-04: `ProcessTurn.execute` takes an optional `emit_audio` sink and
delivers each segment through it from inside the generation loop; the handler's
sink is `ws.send`. Metric renamed `[latency] First audio sent`, since it now
measures what its old name claimed. Six new tests assert **when** audio left
relative to generation (interleaved event log), verified discriminating by
running the same assertion against a deferred-delivery ordering (returns False)
and the streaming ordering (returns True). 601 server green.

**Expected effect, not yet live-measured**: generation is 16.6s of the 22.7s
turn and the first sentence completes early in it, so most of the wait should
now be playback the user is already hearing. Check `First audio sent` and
whether playback ever starves on the next live session.

## Findings — reply length is the dominant cost (2026-08-04)

Measured against the real model and config (`llama3.1:8b`, `num_ctx=16384`):

| stage | time | share of turn |
| --- | --- | --- |
| STT (`medium`, 8 threads) | 4.8 s | 21% |
| **LLM generation** | **16.6 s** | **73%** |
| TTS (Kokoro, CPU) | 3.5 s | 15% |
| embedding (recall gate) | 0.07 s | 0.3% |

First token is 0.20s. Generation runs at a flat **29.4 tok/s** across runs, and
the replies were **1832 / 2486 / 887 characters**. So the dead air is not
latency in any stage — it is token count × a generation rate that is already
what this GPU does. Device placement doesn't touch it. (Note: the live
`[latency] LLM first token` figures, 2.16s → 5.15s across a session, are prompt
*processing* growing with context — a different quantity from the 0.20s bare-
prompt figure here, and both are dwarfed by generation either way.)
