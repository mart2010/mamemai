# Tasks

- [x] **Onset gate** — an utterance opens only after `onset_frames` consecutive
      speech frames (default 3, ~90 ms) instead of the first one. Direct fix for
      the 49-of-65-empty finding below: a noise blip can no longer arm a turn.
      `preroll_frames` ring buffer (default 10, ~300 ms) carries pre-onset
      frames and flushes them on open, fixing the leading-edge clip too.
- [x] **`silence_frames` 25 → 50 (~750 ms → ~1.5 s), configurable** — the
      speaker's own pace, not a software property; `[vad]` in the client's
      `mamemai.toml` (TR-201).
- [x] **Server-side floor** (`[stt] min_utterance_ms`, default 300, TR-958) —
      backstop for client/server version skew, deliberately low (a one-word
      French answer is ~300 ms of *voiced* audio).
- [ ] **Not measured yet.** Retune from real diagnostics: count `[vad] ignored`
      lines against `[stt] skipped` and against real turns from one live
      session, then re-read TR-957's STT-model margin against that.

## Findings (2026-08-03, before the fixes above)

TR-957 benchmarked clean, complete, Kokoro-synthesised utterances. Live
sessions don't produce that input: four of sixteen user turns came back
truncated mid-clause, always at a natural thinking pause — a ~780 ms silence
threshold is shorter than a normal reasoning-aloud pause.

**49 of 65 `end_utterance` events (75%) transcribed to empty**, each paying a
full `medium`-model STT pass plus language detection (detected as `en` at
p=0.51 in a French session). Room noise re-triggers VAD the moment TR-205
restores the mic after playback; the observed ~1.8 s spacing is one noise burst
plus the silence timeout — three quarters of this box's STT budget went to
silence, which reframes TR-957's `cpu_threads` tuning: the machine wasn't short
of cores, it was spending them on nothing.

## Findings (2026-08-04, after the fixes above)

**Waste and truncation addressed** — see the shipped tasks above. The pre-roll
ring buffer is the only silence Whisper now receives; **intra-utterance
silence is still excised** (TR-203's "silence frames are still not sent" while
an utterance is open), so the "Whisper never receives the utterance" half of
the original finding stands, and sending a short pre-roll of natural
intra-utterance silence (so Whisper sees more natural audio) remains
**unmeasured and unadopted** — this is the open question TECHNICAL.md's TR-203
entry points here for.

**Not yet measured**: the burst structure that produced the original 49 empty
transcriptions is still unknown (~1.8 s spacing implies noise intermittent over
about a second, not one short blip; 3 consecutive frames may or may not be the
right onset bar). The client had no tests at all before this fix — both defects
lived in a `sounddevice` callback closure. The VAD state machine is now
`mamemai_client/vad.py`, pure (bytes/booleans, no mic, no socket), with 22
tests; 8 more server-side.
