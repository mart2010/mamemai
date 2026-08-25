# Backlog

_Last swept: 2026-08-25 (mechanical migration from PLAN.md — not yet audited against code)._

Items not yet promoted to an active change — not-yet-designed features, known
leftovers, calibration constants waiting on real usage data, and limitations
accepted by explicit decision. A line here becomes a `docs/changes/<slug>/` folder
(see [docs/changes/README.md](changes/README.md)) once someone actually starts on it.

---

## Phase 9 — Live Voice-Command Wiring (not yet designed)

Placeholder for the next design session (per the Phase 8 close-out, 2026-07-07).
Nothing in this section is a committed decision.

- [ ] LLM tool-calling / intent detection so GA can actually trigger, mid-conversation:
      `UpdateIdleConsolidationMinutes`, `DeactivatePersona`/`ReactivatePersona`, and
      voice / `speaking_rate` / `voices`-map changes via `EditPersona`. Phase 8 built
      the data model and use cases; none are wired to live conversation yet.
      *New evidence since this was written (Phase 12, 2026-07-13)*: bracket-tag emission
      on `aya-expanse` has a real reliability ceiling — the front-loaded few-shot fixed
      `[PERSONA:]`/`[FOCUS:]` (8/8), and Ollama tool/function-calling (`aya-expanse`
      lists `tools` as a capability) remains an unexplored alternative signaling channel
      worth evaluating in this design.
- [ ] **Prerequisite**: discovery/registry for "what's voice-configurable" — a small
      declarative registry (e.g. a `VoiceConfigurableField` descriptor: entity, field,
      type, validator, use case to invoke) that both `ONBOARDING_SCRIPT` and the
      tool-calling layer read from, replacing hand-written prose that is already stale
      (`idle_consolidation_minutes`/`speaking_rate` exist but aren't mentioned there).
      Likely a prerequisite for the item above, needs its own design session — see
      `project_mamemai_open_questions` item 15.
- [ ] `StartSession`'s two hardcoded, unwired constructor defaults
      (`session_tail_turns=10`, `session_continuation_threshold_hours=24.0` in
      `services/session.py`) — candidate promotion to `User` fields under the same
      DB-attribute placement rule (open-questions item 14).
- [ ] VAD threshold **voice**-configurability — client-side; needs a new server→client
      WS message, since the client is fully stateless. Partially addressed 2026-08-04:
      `[vad] onset_frames/silence_frames/preroll_frames` are now client *config-file*
      settings (TR-201), so tuning no longer requires a code edit. Voice-configurability
      is still open and is the harder half — these live on the client, outside the DB
      where every other user-facing setting sits (FR-701).

---

## Wizard (Phase 7) leftovers

- [ ] `--client` flow (`cli.py` still raises `NotImplementedError`)
- [ ] `--uninstall` flow (same)
- [ ] Kokoro voice-pack pre-download: the wizard now downloads Whisper models, Piper
      voices, and the embedding model (`steps.py`), but Kokoro voice packs are still
      lazily fetched from HF on first live use — fails on an offline/locked-down server
      (`HF_HUB_OFFLINE=1`), and the standing cause of the 5 skipped integration tests
      (es/it/pt/ja/zh-cn voice packs).
- [ ] Corporate-proxy / `SSL_CERT_FILE` handling for wizard downloads should be
      automatic (or at least an actionable error), not a manual env-var/`--system-certs`
      ritual — the raw failure is a misleading httpx traceback (details in the archive
      and `project_gpu_workstation_environment` memory).
- [ ] Install-time CUDA-compat verification: `CheckPrerequisites`/`RunHealthChecks`
      should catch CUDA library mismatches (the `nvidia-cublas-cu12` vs CUDA-13
      `nvidia-cublas` class of bug) rather than relying on the pyproject pin never
      drifting.
- [ ] `ServerWebSocketHealthCheck` only checks "something is listening on the port",
      not "mamemai-server actually started with working STT/TTS" — a subprocess-launch
      check needs the server's own venv, deferred.
- [ ] 2 chmod-related setup tests fail on Windows (unfixable there; run on Linux).

*Stale gap closed, for the record*: the old "no wizard step collects Postgres
connection details" gap is gone — `ConfigureDatabaseConnection` exists and pre-fills
on re-runs (FR-706).

---

## Calibration — blocked on real usage data

All instrumented and shippable with placeholder values; revisit once real sessions
accumulate:

- [ ] Merge/disambiguate upsert thresholds (0.93 / 0.75) — the hypernym vs.
      same-name-different-domain cases sit uncomfortably close (0.86–0.87); real
      embedding-calibration numbers are in the archived Phase 3 notes.
- [ ] Tutor retention ranking + half-life parameters — assessment writes
      `persona_state` from day one; selection keeps ranking by `engagement_level`
      until data justifies flipping `settings["ranking"] = "retention"`.
- [ ] `episode_anchor_threshold` (0.6) and `elicitation_cap` (2).
- [ ] Threshold promotion to persona-scoped, voice-configurable fields (Phase 9
      candidate) — blocked on the same calibration data.
- [ ] `authored_protection_threshold` (0.75) — FR-310 concept-origin gate (2026-07-20),
      still untested against real usage. `min_concept_engagement_turns` is **no longer a
      calibration item** as of 2026-08-02: it went to 1 and stopped being a threshold at
      all, because the question it answers (provenance — did the user introduce this?) is
      binary. What used to be tuned by raising it is now the engagement ladder, which is
      also not a threshold. Nothing left to calibrate on either.
- [ ] `min_user_turns`/`min_user_words` (2/40) — `ConsolidateMemory`'s extraction floor
      (FR-307/TR-707, 2026-07-20).

---

## Deferred by explicit decision — waiting for real tutoring sessions

- [ ] Pronunciation-signal mining: faster-whisper's per-segment `avg_logprob` /
      `no_speech_prob` / language-probability distribution, currently discarded.
- [ ] Any constrained-STT experiment.
- [ ] `User.secondary_languages` dead-column removal (never populated; superseded by
      installed languages — glossary already marks it "removal pending").

---

## Known gaps / accepted limitations (revisit when relevant)

- **No session wish reaches selection** (2026-08-02, FR-502 suspended): every persona
  gets its default batch. Deliberate — see [BRIEF_inband_tags.md](BRIEF_inband_tags.md).
- **One persona, two topics, one conversation** (2026-08-02, FR-112 retired): an
  uninterrupted single-persona stretch covering unrelated topics consolidates as a single
  `Conversation` — one worthiness verdict, one summary, extraction over mixed content.
  A persona switch is now the only mid-session split. Accepted; offline segmentation
  waits for real multi-topic sessions to calibrate against.
- **TPRS cross-session story recap** no longer persists (the consolidation gates for
  strategy personas removed episode extraction); no replacement mechanism —
  deliberate open gap.
- **Replay idempotency race** (Phase 5): `TurnLogReplayer`'s check-then-insert isn't
  atomic across the live + offline DB connections; a reconnect landing in a narrow
  window could duplicate one session's rows. Fix sketch if it ever bites: a
  `replayed_sessions(session_id UUID PRIMARY KEY)` claim table with
  `INSERT ... ON CONFLICT DO NOTHING RETURNING`.
- **Acoustic echo** on the client — no echo cancellation; mic muting during playback
  is the current mitigation (INV-4 no-barge-in makes this tolerable).
- **Markdown stripping** is pattern-based and still incomplete for exotic LLM output.
- **Coqui TTS licence conflict** (MPL-2.0 Exhibit B vs AGPL-3.0) — deferred; Piper and
  Kokoro are the working engines.
- **Korean unsupported**: Kokoro has no Korean pipeline; MeloTTS rejected on dependency
  footprint. Revisit if a lighter Korean TTS appears.
- **Client-side first-launch onboarding proposal** (fold server address, port, and
  language into one client-side questionary wizard instead of the server-driven
  `select_language` flow) — archived Phase 4 "revisit" note, still undecided.
- **Laptop `server/` venv frozen**: `uv.lock` now pins numpy 2.5.1 (fixed 2026-07-17,
  see the Windows support item in the archive), but `uv sync` still can't complete on
  this laptop — `curated-tokenizers` needs a C/C++ compiler and this laptop has no admin
  rights to install one. Always `uv run --no-sync` here regardless. Locally,
  `tests/unit/infrastructure/test_config.py` fails collection (`platformdirs` missing,
  never installed since `uv sync` has never completed on this laptop) — `--ignore` it;
  it runs on the workstation.
