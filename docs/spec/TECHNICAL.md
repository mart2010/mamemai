# Technical Specification

*Last verified against code: 2026-08-06*

Internal contracts: architecture, protocol, formats, data model, algorithms. Terms per
[GLOSSARY.md](GLOSSARY.md); conventions per [SPEC.md](SPEC.md). File references anchor
each area to its implementation.

## Invariants

Cross-cutting hard rules. Violating any of these is always a defect; changing one is a
design discussion, never a patch.

- **INV-1 — Live/offline boundary.** The live conversation path may read the DB
  (session start, recall, lazy selection fetch) and performs **no memory writes**:
  embedding generation for storage, LLM extraction/synthesis, upserts, episode/concept/
  procedure writes and brief generation happen only in the offline pipeline or the
  bundle installer. Turn content reaches the DB only via the JSONL session log and
  offline replay.
  **Configuration writes are the one exception, and are enumerated**: a matched
  *Directive* may write the `personas` or `users` row it names — and nothing else.
  FR-701 makes voice the only configuration surface and requires every
  voice-configurable setting to be a DB-backed `User`/`AssistantPersona` attribute, so a
  setting changed by voice *must* be written from the live path. A single-row `UPDATE`
  of a configuration column is not memory formation: bounded, synchronous, no model
  involved, and it cannot grow the memory store.
- **INV-2 — Dependency rule.** `domain/` imports nothing external; `services/` imports
  domain only and defines all ports; `infrastructure/` implements ports. Inner layers
  never import from outer ones.
- **INV-3 — Single user per schema.** One `User` row, one connection at a time, no
  auth, within the one Postgres schema a running deployment targets (`[database]
  schema`, wizard-decided, immutable — TR-5xx). Several independent schemas may coexist
  in one database (one per family member), but nothing about concurrency changes: a
  deployment/config always targets exactly one schema, and design decisions may rely on
  single-user *within it*.
- **INV-4 — No barge-in.** The response plays to completion; the mic re-opens only on
  `speaking_end`.
- **INV-5 — Session logs are kept forever.** No rotation or cleanup logic anywhere.
- **INV-6 — Persona-state opacity.** `persona_state` is written only by the owning
  persona's assessment strategy (via `update_persona_state`; upserts structurally
  exclude the column) and read only by that persona's selection strategy.
  `AssistantPersona.settings` has the same opacity one level up. No generic code path
  branches on the contents of either. `Directive` (FR-207) is the deliberate opposite —
  a first-class model, not a `Concept` field, whose whole point is for generic code
  (`ProcessTurn`) to read and act on it: not persona-authored opaque data, but mamemai's
  own operational routing.
  **Vocabulary corollary**: `domain/` and `services/` must not name a concrete persona's
  domain — not in code, not in comments. Generic code that has learned to talk about one
  persona's subject matter is generic code about to start branching on it, so the ban
  applies one level before a branch exists. Enforced mechanically by
  `server/tests/unit/test_no_persona_leak.py`; each persona's own vocabulary belongs in
  its `infrastructure/<persona>/` strategy package. Generic `infrastructure/` is not yet
  covered — see docs/BRIEF_persona_contract.md.
- **INV-7 — Voices anchor.** Every `voices` map contains the `"default"` role, and its
  value is a single voice (never a `|` pool). Enforced by the entity.
- **INV-8 — Description/embedding co-update.** On any content change to a
  concept/procedure description (or episode summary), the embedding is recomputed in
  the same operation. Under consolidation gates neither changes (both kept verbatim).
- **INV-9 — Persona scope & cascade.** Concept/procedure similarity search during
  upsert is always scoped to the owning persona — a *write*-path invariant, and
  unaffected by FR-303's declared read scope: widening what a persona may read never
  widens what it may change. `ON DELETE CASCADE` from personas to
  concepts/procedures is load-bearing (clean persona teardown), not an oversight.
- **INV-10 — Episodes are persona-independent and primary-language.** Episodes carry no
  persona scope or language field; summaries are always written in
  `User.primary_language`. A persona that declares `extracts_episodes = false` (FR-407)
  never produces episodes at all.
- **INV-11 — Insertion order is curriculum order.** Ascending SERIAL id (within a
  type) / `created_at` (across types) is the contract bundle install writes and tutor
  selection reads. Nothing may reorder or renumber installed content.
- **INV-12 — Content sources never claim knowledge.** Bundle items and enrichment
  proposals always enter at `UNSEEN`; only generic consolidation moves engagement.
- **INV-13 — Language of first introduction is fixed.** `Concept.language` /
  `Procedure.language` never changes on upsert; descriptions stay in that language.
- **INV-14 — Persistent language settings switch explicitly only.** No implicit persona
  switching, and no change to any stored language setting (`User.primary_language`, a
  persona's `response_language`), from detected speech language.
- **INV-15 — Episode provenance is fixed.** `origin_conversation_id` is NOT NULL and
  never reassigned; `happened_at` is the temporal anchor, not the conversation date.

## TR-0xx — Architecture & composition

- **TR-001** Monorepo of three independent uv-managed packages: `client/` (capture &
  playback), `server/` (pipeline + memory), `setup/` (install wizard). Each has its own
  venv; Python ≥ 3.13.
- **TR-002** Server layout (`server/src/mamemai_server/`): `domain/` (entities, value
  objects, events, domain protocols), `services/` (use cases + ports), `infrastructure/`
  (Postgres, Ollama/OpenRouter, STT/TTS/embedding, JSONL, bundle TOML, per-persona
  strategy packages e.g. `language_tutor/`). INV-2 governs imports.
- **TR-003** `server.py` is the composition root: loads config, builds all adapters
  once (single long-lived process), wires use cases per connection.
- **TR-004** Strategy registries (`SELECTION/ASSESSMENT/ENRICHMENT_STRATEGY_FACTORIES`)
  map strategy-set names (e.g. `"language_tutor"`) to factories. Bindings are resolved
  per connection (selection) / per offline run (assessment, enrichment) by scanning
  personas' `strategy` fields — a bundle installed between sessions binds without a
  server restart. Unknown names log a warning and bind nothing — where **unknown means
  known to no registry at all**, not merely absent from the one being built: a persona
  implements the capabilities it has, and a name registered elsewhere (e.g.
  `cognitive_coach`, which has selection and assessment strategies but deliberately no
  enrichment one) is declining that capability, not misconfigured.
- **TR-005** One registry entry serves every persona of that class (any target
  language); everything persona-specific comes from the persona's rows and `settings`,
  never from strategy code.
- **TR-006** Two independent Postgres connections: `conn` for the live path (event-loop
  thread), `offline_conn` for the background pipeline thread — a shared connection
  would interleave live queries into the offline transaction or block the loop. Both
  are opened against the same `[database] schema` (TR-5xx, INV-3) via `search_path`;
  nothing ever mixes two schemas on one connection or one deployment.
- **TR-007** `ConsolidateMemory`, `EnrichMemory`, and replay are synchronous by design
  and must be dispatched via `asyncio.to_thread` when called from the event loop; brief
  generation streams async and is awaited directly.
- **TR-008** The one `User` row is bootstrapped by the server on startup when missing,
  seeded with `[user] name` (wizard-time, the same alias that became this install's
  schema slug) or, absent that key, the literal default `"user-1"`.

## TR-1xx — WebSocket protocol

`ws://localhost:<ws_port>` (default 8765), `max_size=None`.

| # | Message | Direction | Semantics |
|---|---|---|---|
| **TR-101** | binary frame | client→server | PCM **int16** 16 kHz mono; accumulated in the utterance audio buffer (ignored until onboarding is done) |
| **TR-102** | `{"type": "end_utterance"}` | client→server | Flush the buffer to STT and run the turn; ignored when the buffer is empty or onboarding incomplete |
| **TR-103** | `{"type": "select_language", "supported": [codes]}` | server→client | Sent on connect iff `User.primary_language` is null; `supported` = the installed languages (TR-951 `[languages]`), not all of `SUPPORTED_LANGUAGES` |
| **TR-104** | `{"type": "language_selected", "language": code}` | client→server | Completes onboarding (FR-003); ignored once onboarding is done |
| **TR-105** | binary frame | server→client | PCM **float32** 16 kHz synthesised audio, one frame per synthesised segment |
| **TR-106** | `{"type": "speaking_end"}` | server→client | Sent after each turn's chunks (even when the turn produced nothing); re-enables client VAD |

- **TR-107** Unparseable text frames are ignored; unknown `type` values are ignored.
  (Forward-compatible: new message types must not break old peers.)

## TR-2xx — Client

`client/src/mamemai_client/client.py`; stateless beyond one config file.

- **TR-201** Config: `mamemai.toml` in the platform config dir
  (`platformdirs.user_config_dir("mamemai")`), `[server]` table: `ws_port` (default
  8765), optional `ssh_host`; `[vad]` table: `onset_frames=3`, `silence_frames=50`,
  `preroll_frames=10` (TR-203/TR-204), all counted in 30 ms frames and each independently
  defaulted, so an absent table or key is the pre-existing config and still gets the
  current defaults. Unusable values (`onset_frames`/`silence_frames` < 1, negative
  `preroll_frames`) raise at load rather than misbehaving mid-session. Missing file →
  clear `FileNotFoundError` guidance. Unlike the server's `[stt]`/`[llm]` knobs, `[vad]`
  is tuned to the **speaker and the room**, not to the hardware.
- **TR-202** Capture: `sounddevice` InputStream, 16 kHz mono float32, blocksize 480
  (30 ms); each block converted to int16 (`×32768`) for VAD and the wire.
- **TR-203** VAD: `webrtcvad`, aggressiveness 2, driving `vad.UtteranceDetector` — a pure
  frame-at-a-time state machine, kept out of the `sounddevice` callback so it is testable.
  An utterance **opens only after `onset_frames` consecutive speech-classified frames**;
  a shorter run is discarded and reported (`ignored_burst_frames` → a `[vad] ignored …`
  line). The gate decides *whether* to open, never what the utterance contains: a ring
  buffer carries the `preroll_frames` frames preceding the onset run plus the run itself
  and flushes them all on open, so raising `onset_frames` costs no audio and the first
  word keeps its attack. While an utterance is open, speech frames are sent immediately
  and **silence frames are still not sent** — the pre-roll lead-in is the only silence
  Whisper receives.
  The onset gate exists because a *single* noise-classified frame can otherwise arm a
  whole turn, burning a full STT pass on room noise re-triggering the mic the moment
  TR-205 restores it after playback. Whether intra-utterance silence should also be
  sent (so Whisper sees natural audio) is open — see docs/PLAN.md.
- **TR-204** End-of-utterance: after speech has been active, **> `silence_frames`
  consecutive silent frames** (default 50, ~1.5 s) sends `end_utterance` and resets.
  Silence before any speech sends nothing.
  How long a speaker pauses mid-thought is a property of the speaker, which is why this is
  configurable (TR-201) rather than a constant. It is a direct latency trade — the server
  cannot begin transcribing until it elapses — so it is set to what a slow talker needs
  and no further.
- **TR-205** Mic muting: an event flag suppresses the VAD callback from the first
  received audio chunk until `speaking_end` (and while the onboarding prompt is open);
  playback is blocking (`sd.play` + wait) per chunk. Muting also **resets the detector**:
  playback is not a pause in the user's speech, so a half-armed onset run or an open
  utterance must not survive across it to be credited to the first room-noise frame after
  `speaking_end`.
- **TR-206** SSH tunnel (split-host): `ssh -N -L {ws_port}:localhost:{ws_port}
  {ssh_host}` as a daemon-thread subprocess, restarted in a loop 3 s after exit. Proxy
  env vars (`HTTP(S)_PROXY`) are stripped from the process environment.
- **TR-207** Onboarding UI: `questionary.select` terminal dropdown over the
  `supported` codes; falls back to the first entry on cancel.

## TR-3xx — Server live turn pipeline

`services/session.py` (`StartSession`, `ProcessTurn`, `EndSession`).

- **TR-301** `StartSession` loads: `User`, GA persona, all Directives (FR-207, via
  `DirectiveRepository.list_all()`), memory brief (skipped during onboarding), and the
  session tail — previous
  session's last `session_tail_turns` (default 10, `[server]` config, TR-951) turns
  iff it ended within `session_continuation_threshold_hours` (24). Fails fast when
  User or GA is missing.
- **TR-302** Turn sequence: STT → log user turn → directive matching (TR-315) →
  recall gate (TR-314; embed turn text, `search` top 5, persona-scoped; skipped on a
  turn that just switched persona) → lazy selection fetch/consume (TR-306; likewise
  skipped) → compose working context → stream LLM → per-sentence TTS → resolve
  markers → log assistant turn → rolling-summary check.
- **TR-303** Working-context composition (`_compose_working_context`): system prompt =
  persona prompt ⊕ onboarding directives (first launch) ⊕ response-language
  instruction (the active persona's fixed `response_language`; suppressed entirely for
  cast personas — non-default `voices` keys — whose own prompt owns language use,
  FR-105) ⊕ memory brief ⊕ a memory-honesty block, present only when `recall_attempted`
  (TR-314) is `True` this turn: recalled memories + an instruction to briefly
  acknowledge drawing on a past conversation naturally (not recite the list) when
  `recalled_memories` is non-empty, or an instruction to say plainly that memory was
  checked and nothing relevant was found when it's empty — a turn recall never even
  ran on (`recall_attempted=False`) gets neither, same as before this existed. No
  persona-listing/switch-instruction block (FR-202/FR-203 retired, FR-207) — the
  system prompt never names another persona. Messages = session tail (as one system
  message) ⊕ rolling summary ⊕ recent turns; a selected item, and — on a blocked
  switch (TR-315) — a `blocked_switch_persona_name` explanation, are each injected as
  a system message immediately before the current user turn. User turns (recent and
  tail) are rendered with their
  detected-language `[lang:code]` prefix (`_render_turn_content`, FR-114) —
  rendering-only, stored content untouched; a turn without a recorded language renders
  untagged.
- **TR-304** `[RETIRED 2026-08-02]`
- **TR-305** Cast voice selection is per-segment and language-based, not tag-based:
  each complete sentence is classified by its own dominant language
  (`LanguageDetector.detect`) and a confident match selects that segment's synthesis
  voice via `_session_voice`; a low-confidence result (see TR-307) keeps whatever voice
  was already active rather than forcing a switch. Deliberately whole-segment, never
  mid-sentence — a sentence quoting a foreign word stays entirely in the voice it
  started in.
  **Detection is a cast feature and runs only for a persona that has a cast** — more than
  one voice to choose between. Candidates are exactly the languages in play for that
  persona: its cast keys, its own response language, and `User.primary_language`; fewer
  than two of those means no detection at all. A single-voice persona never calls the
  detector, because its text has only one voice it could be spoken by.

- **TR-306** Selection batches: fetched lazily on a strategy persona's first active
  turn (`select_items(persona_id)`, a live DB *read*), stored per persona id in working
  memory; one item popped per turn.
  **Pacing is declared by the persona, applied by the pipeline** (`SelectionPacing` on
  `PersonaSelectionPort`). Two rules live outside `ProcessTurn`: `refetch_when_exhausted`
  (default `False` — a batch that runs dry stays dry) and `include_activation_turn`
  (default `False` — the turn a directive switches to a persona brings no content,
  leaving it free to greet); a strategy that declares no `pacing` gets
  `DEFAULT_SELECTION_PACING`. `include_activation_turn` never applies to a *blocked*
  switch regardless of the declaration: that turn activates nobody, it explains that a
  persona is unavailable.
  Generic code still owns the mechanics — when to call, where the batch lives, one item
  per turn, and how it renders into the prompt. Deliberately a small declared policy
  rather than handing the persona the turn: the wider inversion is the persona contract's
  job (docs/BRIEF_persona_contract.md). Items-per-turn is **not** configurable — it
  would ripple through the injection renderer.
  `select_items` still takes `focus`, but nothing supplies it today —
  it is always `None` and every persona gets its default batch. The port keeps the
  parameter, and the strategy side that interprets a wish stays intact, for the user-turn
  detection that replaces the marker (deferred, docs/BRIEF_inband_tags.md).
- **TR-307** Voice resolution (`_session_voice`): `voices[language_code]`, falling back to
  the persona's anchor for an unregistered or undetected code — the anchor is the
  persona's own choice for every language its cast does not cover, and is never
  substituted by the machine's language→voice map; `|`-pools pick index
  `session_id.int % len(pool)` — deterministic per session, no state. Detection
  (`infrastructure/language_detection.py`) returns `None` — leaving the current voice
  unchanged — below a minimum confident-length threshold (placeholder pending real
  tuning); very short segments (a bare greeting, "No.") are genuinely ambiguous to
  statistical language ID, and so is a call with fewer than two usable candidates.
  The detector speaks the caller's vocabulary in both directions: candidates are IETF
  tags (`Language.code`) translated to py3langid's ISO 639-1 keys on the way in and
  translated back on the way out — `zh-cn` is the one supported language where the two
  differ, and it is also a `voices`/`KOKORO_DEFAULT_VOICES` key, so a leaked bare `zh`
  would miss the voice map as silently as an unmapped tag crashed the turn. A candidate
  the model does not know is dropped, never raised: the live path may lose a candidate,
  never a turn.
- **TR-308** Sentence-level synthesis: segments split on `.` `!` `?`, including
  retroactively splitting a multi-sentence chunk that arrives in one piece (a single LLM
  token can carry more than one sentence ending) rather than only checking whether the
  whole chunk ends on one; each segment is markdown/emoji-stripped, cleansed of any
  mimicked `[lang:]` tag (FR-114 — models can imitate the inbound user-turn convention)
  and of any marker-shaped bracket span (FR-106), and number-spelled (num2words for
  en/fr/es/it/pt only) before TTS; empty segments are skipped. Number-spelling is
  **language-specific**, because `.` and `,` swap roles: `en` groups on `,` and takes `.`
  as the decimal, `fr/es/it/pt` take `,` as the decimal and group on space (`es/it/pt`
  also on `.`); spaces — including NBSP and narrow NBSP — always group, never decimalise.
  Grouping requires a separator followed by **exactly three digits**, which is the only
  thing distinguishing "7,840" (7840 in English) from "7,84" (7.84 in French); anything
  not so grouped takes the original bare-digits path unchanged. Years carry no
  separator.
  The marker strip is the **only** thing between a bracket token and the speaker — no
  control tag is resolved upstream — so it is load-bearing rather than a
  backstop, and the first sentence is synthesised as soon as it completes, with no
  lead-in buffer ahead of it — and **sent** as soon as it is synthesised (TR-316).
- **TR-309** Rolling summary: when `total_turn_count % 50 == 0`, the oldest 25 recent
  turns are LLM-summarised into (or merged with) `rolling_summary` and dropped from the
  window.
- **TR-310** `[RETIRED 2026-07-18]`
- **TR-311** Latency instrumentation: `[latency]` stdout lines for STT, first LLM
  token, first TTS chunk, **first audio sent**, total turn, inter-turn gap. Plus one
  `[llm] context:` line per live reply — estimated prompt tokens against the usable
  `num_ctx` budget, flagged when over (TR-952). Unconditional, not warning-only: the
  live context grows all session while the mechanism meant to bound it (TR-309) does not
  fire until turn 50, so the trend is the point, not just the alarm.
- **TR-312** Turn timestamps: the user turn is stamped at `end_utterance` receipt; the
  assistant turn is stamped when the LLM stream finishes — the assistant→user delta is
  the stored response-latency proxy consumed by tutor assessment (TR-806).
- **TR-313** `[RETIRED 2026-07-18]`
- **TR-314** Recall gating (`RecallGate`, FR-309): local, deterministic logic — no LLM
  call, no JSON parsing, no dependency on `[llm].provider`.
  `should_embed(text)` runs first, before any embedding is computed: `DefaultRecallGate`
  (used by GA and any persona without an override) returns `False` unless the text
  contains a literal `"?"` **and** clears `min_words` (6) — together they exclude both
  plain statements and short reflexive questions like "How are you?", since word count
  alone can't tell those apart from a real recall-worthy question of similar length; a
  word-count floor, not a character count, since it tracks "is there searchable content
  here" consistently across languages, though `"?"` itself is an STT-punctuation proxy,
  not perfectly reliable across languages/phrasings (faster-whisper infers it from
  intonation/syntax, doesn't guarantee it — accepted approximation, same posture as
  the word-count floor itself). No persona currently registers an override
  (`RECALL_GATE_FACTORIES` in `server.py` is empty but kept as its own registry,
  parallel to `SELECTION_STRATEGY_FACTORIES` et al. — a future persona-type may still
  need a distinct policy here): the tutor's single-word vocabulary answers don't need
  their own recall search either — the item currently being tested is
  already injected via `LanguageTutorSelectionStrategy.select_items` (a separate
  mechanism, reading `persona_state`/spaced-repetition scheduling directly, FR-501/
  506), independent of recall entirely; a bare "agua" answering the tutor's own
  prompt doesn't need an additional generic memory search on top of that context.
  When `should_embed` is `True`, the turn's
  raw text (not an extracted query — there is no extraction step anymore) is embedded
  and compared, via `domain.model.cosine_similarity`
  (in-process, no DB round trip), against **every** entry in
  `WorkingMemory.recall_history[persona_id]` — the embedding and results of every
  utterance that actually triggered a real search for the active persona this session,
  oldest first — not only the most recent entry: nothing new can enter long-term memory
  mid-session (INV-1), so a repeat of any earlier query, not only the immediately
  preceding one, would deterministically return the same results again.
  `should_search(max_similarity)` receives the highest similarity across that whole
  history (`None` if the history is empty — always search on the first turn) and
  returns `True` when it is `None` or below `dedup_threshold` (0.93, reusing the
  existing merge-threshold "same thing" bar); at or above it, the DB round trip is
  skipped and the best-matching historical entry's cached results are reused as-is,
  appended as a new entry to `recall_history` only when a real search actually runs.
  `search()` is called with every `MemoryType` (no type restriction — the existing
  top-5-by-similarity ranking already surfaces the right items across types). Gate
  resolution mirrors `selection_strategies`: `ProcessTurn._recall_gates.get(persona_id,
  default_recall_gate)` — the one difference from `PersonaSelectionPort` et al. being
  that every persona resolves to *some* gate, never a no-op. `ProcessTurn.execute` step
  4 also records whether `should_embed` cleared at all this turn as `recall_attempted`
  (a plain local bool, not stored on `WorkingMemory`) — carried into `_compose_working_
  context` (TR-303) so the reply can be transparent about a genuine recall miss versus
  a turn recall never even ran on.
  `is_relevant(similarity)` is the gate's third decision, applied to each row `search`
  returns before composition (FR-309). `DefaultRecallGate` keeps rows at or above
  `MIN_RECALL_SIMILARITY` (**0.84**), calibrated against the real database (on the
  `query:`/`passage:`-prefixed embedding scale, TR-508b) rather than guessed. It is
  deliberately *not* the total-error minimum — a false recall makes the assistant claim
  a memory it does not have, the failure FR-309's honesty branch exists to prevent,
  while a lost recall merely answers without citing memory, so the floor is set to
  favour avoiding the former.
  Known limit: a single **global** floor works only because the per-language similarity
  bands happen to overlap. A language-aware floor is the principled fix and is recorded
  as follow-up. What survives the floor is also what gets cached in `recall_history`,
  empty results included: a repeated question that found nothing must not pay for a
  second lookup that cannot find anything either (INV-1).
- **TR-315** Directive matching (`ProcessTurn.execute` step 3b, FR-207): runs
  unconditionally on every non-empty turn, independent of `RecallGate` (TR-314) and
  needing no embedding call at all — a directive phrase is a short, closed-vocabulary
  command, exactly the case where a deterministic rule outperforms a similarity search.
  `domain.model.Directive` — `id`, `target_persona_id`,
  `action`, `language`, `trigger`, `description` — is its own first-class domain object,
  its own `directives` table, not a
  memory type; no `engagement_level`, no embedding column, never touched by recall's
  `search()` at all. The turn's raw text is checked via `services.directives.
  matches_trigger(trigger, text)` against every entry in `WorkingMemory.directives`
  (fetched once at session start via `DirectiveRepository.list_all()` — same
  fetch-once posture as `memory_brief`); among every entry that matches, the one
  requiring the **most keywords** wins, not the first in list order — one persona's
  name can be a strict word subset of another's (e.g. "Coach" vs "Old Coach"), so an
  utterance naming the more specific one satisfies both triggers; list order would
  resolve to whichever was created first, regardless of which name the user actually
  said. Ties (equal
  keyword count) fall back to list order (ascending `Directive.id`). A trigger is
  `{"keywords": [...], "max_words": N}`
  (`Directive.trigger`): fires when every keyword occurs as a case-insensitive
  substring of the text and `len(text.split()) <= max_words`; an empty/missing
  `keywords` list never matches (`services/directives.py`'s `build_trigger`/
  `matches_trigger`). Keywords are the persona's own name split into words plus a
  translated action verb (`_SWITCH_VERB`, one entry per `SUPPORTED_LANGUAGE` code,
  English fallback for an unlisted code); `max_words` is `max(6, len(keywords) + 3)` —
  headroom scales with how many words the persona's own name contributes.
  `PersonaDirectiveSync.sync_created` generates one Directive row per language in
  `persona.languages` (TR-904's session language pair, already folds in the user's
  primary language; falls back to the persona's own `response_language` if that list
  is empty, e.g. a live-created persona via `CreatePersona`); `ensure_return_to_
  general_assistant` generates one per this machine's installed language (FR-705),
  using a second translation table (`_GENERAL_ASSISTANT_NAME`) since that label is
  GA's own fixed wording, not a user-authored persona name. A clearing match whose
  `action` is `"switch_persona"` and whose `target_persona_id` differs from the
  currently active persona (already-active is a silent no-op) fetches the target via
  `PersonaRepository.get()`: `None` (a dangling reference — shouldn't happen,
  `target_persona_id` is FK-backed) is a silent no-op, same as already-active;
  `not is_active` sets `blocked_switch_persona_name = target.name` and does **not**
  call `SwitchPersona` — deactivation doesn't clean up the persona's Directive row
  (only removal cascades, INV-9), so the trigger still matches, but the switch itself
  must be blocked, deterministically, or conversation would silently reactivate a
  deactivated persona. Otherwise it calls the existing `SwitchPersona` use case
  (`services/persona.py`), which updates `WorkingMemory.active_persona` and returns
  the `PersonaSwitched` event carried through to `TurnResult`. Recall (TR-314) and the
  persona-selected-item lazy fetch (TR-306) are both skipped entirely on a *directive
  turn* — `persona_switched is not None or blocked_switch_persona_name is not None` —
  whether the switch actually fired or was blocked, since the user's utterance was a
  directive/switch attempt either way, not a content question; both resume normally
  next turn. A real switch logs the assistant turn under the **new** persona id, same
  as the retired `[PERSONA:]` scheme (TR-310) guaranteed. A blocked switch instead
  passes `blocked_switch_persona_name` to `_compose_working_context`, which inserts a
  turn-scoped `messages` entry — same injection mechanism/position as recalled
  memories/the selected item (TR-303), naming only the one persona the user already
  named, so the active persona (whichever it is — this isn't GA-specific) can explain
  the persona is unavailable in its own words and continue normally; deliberately not
  the persona-listing block FR-202/FR-203 retired (that named every persona, every
  turn, unconditionally — this names at most one, only on the rare turn a blocked
  attempt actually occurs).
  A firing switch also unconditionally sets this turn's `boundary_marker` to
  `ConversationBoundaryType.BREAK` (step 7) — deterministic, never left to LLM judgment,
  same as the switch decision itself, and since TR-304's retirement the **only** source
  of a mid-session break.
  Without this, `replay.py`'s `_group_into_conversations` (TR-403) only re-evaluates a
  conversation group's `persona_id` on a `BREAK`, so a switch mid-session would
  silently let the new persona's turns be attributed to the old persona's conversation
  record for consolidation — breaking FR-303's persona-scoping, TR-704's
  assessment-strategy dispatch (keyed on `conversation.persona_id`), and FR-504's
  episode exemption for tutor sessions.

- **TR-316** Audio streaming: `ProcessTurn.execute` takes an optional `emit_audio`
  sink (`AudioSink = Callable[[bytes], Awaitable[None]]`) and delivers each synthesised
  segment through it **from inside the generation loop**, the moment it exists. The live
  handler's sink is the WebSocket send, so the user starts hearing the reply while the
  model is still writing it. The `await` is inline rather than fire-and-forget: that
  preserves segment order (TR-105 — the client plays in arrival order and cannot
  reassemble) and lets the client's backpressure throttle generation, which costs nothing
  since the user is still listening to what was already sent.
  With a sink supplied, `TurnResult.audio_chunks` stays **empty**; without one, it
  accumulates as before (the LLM quality gates want the whole reply in hand). The
  asymmetry is deliberate: a handler that both supplies a sink and sends `audio_chunks`
  produces silence, which is obvious, rather than every sentence played twice.
  Tests assert **when** audio left relative to generation, not merely that it exists —
  "audio was produced" is not the same claim as "audio was streamed".
  A sink that raises (client gone) aborts the turn with the already-sent segments
  delivered — a truncated reply rather than a silent one; `server.py` logs it and still
  sends `speaking_end` so the mic is restored.
## TR-4xx — Session logs & replay

`infrastructure/json_file.py`, `services/replay.py`.

- **TR-401** One JSONL file per session: `logs/sessions/YYYY-MM-DD_<session_id>.jsonl`
  (date = first append, UTC).
- **TR-402** Turn line: `{"ts": iso, "speaker": "user"|"assistant", "content": str}` +
  optional `"language"` (user turns), `"marker"` (`"break"|"continuation"`), and
  `"persona_id"` (assistant turns). Close line: `{"type": "session_closed", "ts": iso,
  "clean_exit": bool}`.
- **TR-403** Replay grouping (`_group_into_conversations`): a `continuation` marker on
  the session's first assistant turn → the whole session extends the last open
  conversation in the DB (when none exists, it is saved as new); a `break` marker on a
  later assistant turn closes the current group (inclusive) and starts a new one; a
  `break` on the first assistant turn is ignored (a new session is already a boundary).
  Each group's persona is the persona of its first assistant turn, re-evaluated only
  when a `break` closes a group — a persona switch (TR-315) always emits one for
  exactly this reason, so a session that changes persona always splits into separate,
  correctly-persona-scoped conversation records rather than one group frozen on
  whichever persona was active first.
- **TR-404** A file without `session_closed` (crash) is still replayed; the group's
  `ended_at` falls back to its last turn timestamp.
- **TR-405** Replay idempotency: files are scanned newest-first and the scan stops at
  the first session already persisted (monotonic invariant — older files are
  necessarily persisted); unprocessed sessions are then replayed oldest-first.
- **TR-406** Replay runs on every client connect (before the session starts) and as
  step 1 of the offline pipeline.

## TR-5xx — Data model & schema

`server/migrations/001_initial_schema.sql`, `domain/model.py`.

- **TR-501** PostgreSQL 15+ with pgvector; all embeddings `vector(1024)`
  (`multilingual-e5-large`); HNSW cosine indexes on episodes/concepts/procedures.
- **TR-501b** Every table below lives inside one Postgres schema per installation
  (INV-3), not `public` directly: the wizard's `SetupSchema` step runs `CREATE SCHEMA
  IF NOT EXISTS "<schema>"; SET search_path TO "<schema>", public;` before applying
  `001_initial_schema.sql`, and the server connects with the same `search_path` (TR-006).
  `public` stays reachable behind the named schema so pgvector's `vector` type resolves
  from inside every user schema without being reinstalled per schema. This requires
  pinning it explicitly — `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public`,
  not a bare `CREATE EXTENSION IF NOT EXISTS vector` — since an unqualified extension
  installs into whichever schema is first on `search_path` at creation time, which
  would be the *first* family member's own schema, unreachable from every later one.
  The schema name
  itself is validated against `^[a-z][a-z0-9_]*$` wherever it reaches a connection string
  or raw DDL (`domain.slugify.schema_slug` on the wizard side, a duplicate regex check in
  `infrastructure/postgres.py` and `infrastructure/schema_runner.py` — the two packages
  share no code, TR-001), since it is not a bindable query parameter.
- **TR-502** Tables: `users` (singleton *within the schema*, `name` seeded at bootstrap —
  TR-008), `personas`, `conversations`, `turns`
  (composite PK conversation_id+timestamp; `session_id` indexed for replay
  idempotency), `episodes`, `concepts`, `procedures`, `directives` (FR-207 — not a
  memory type; see TR-510), `bundle_installs` (append-only), `memory_brief`
  (singleton, `id = 1`).
- **TR-503** FKs: concepts/procedures/directives → personas `ON DELETE CASCADE`
  (INV-9 — a directive's `target_persona_id` cascades the same way, so removing a
  persona removes its own "switch to me" Directive with no explicit cleanup code);
  conversations → personas `ON DELETE RESTRICT`; turns → conversations
  `ON DELETE CASCADE`; episodes → conversations (provenance, INV-15).
- **TR-504** `EngagementLevel` is an ordered enum `unseen(0) < mentioned(1) <
  explored(2) < integrated(3)`; stored lowercase text; extracted items default to
  `mentioned`.
- **TR-505** `SUPPORTED_LANGUAGES` = the faster-whisper ∩ Kokoro intersection,
  currently `en fr es it pt ja zh-cn` (7; `ko` dropped — no Kokoro Korean pipeline).
  The installed languages (FR-705) are the wizard-selected subset of this list; the
  composition root intersects `[languages].installed` with it (unsupported codes warn
  and are ignored; an empty intersection fails startup; key absent → all supported).
- **TR-506** GA seed: fixed UUID `00000000-0000-0000-0000-000000000001`, `is_system`,
  idempotent insert; `persona_key`/`strategy` are set at creation only and never
  updated by `save()` (like `is_system`).
- **TR-507** `Conversation` aggregate rules: no turns after `ended_at`; consolidation
  requires ended + non-empty + not already consolidated (`mark_consolidated` enforces).
- **TR-508** `list_items` orders by ascending id within each memory type
  (curriculum-order read side, INV-11) and rejects episode queries; `list_all()`
  returns **all** personas, including deactivated ones. *(Note: accepted for now;
  revisit when lifecycle gets a live trigger, FR-204.)*
- **TR-508b** Embedding roles: `EmbeddingService` exposes **`embed_query`** and
  **`embed_passage`**, not one `embed`. `intfloat/multilingual-e5-large` is asymmetric —
  trained with a `query: ` prefix on text being searched *with* and `passage: ` on text
  being searched *for* — and the prefixes are part of the model, not decoration.
  Role, not content, picks the method: a user turn at the recall gate, a `TriggerRecall`
  query and a persona strategy's topic match are **queries**; everything written to a row
  is a **passage**. `MemoryUpserter` uses `embed_passage` on both sides of its
  insert-vs-merge search — that comparison is document-to-document, so one prefix keeps
  both halves on the scale its thresholds were derived against.
  Using `query: ` on *both* sides (e5's symmetric recipe) is deliberately **not** used:
  it lifts absent probes as much as present ones, defeating the separation the
  asymmetric roles exist to provide.
  Changing either prefix string invalidates every stored embedding; the backfill is
  `scripts/reembed_memories.py`, and the text it embeds comes from the shared
  `services.upsert.embeddable_text` so a backfill cannot drift from the live path.
- **TR-509** `search` returns `(similarity, item)` with **similarity = 1 − pgvector
  cosine distance**, merged across requested types, sorted, truncated to `top_n`;
  `persona_id` filters concepts/procedures only (episodes are global). **Nearest, not
  near**: there is no distance floor in the query, so with rows in the table it never
  returns an empty list — how close counts as a match is the caller's policy
  (`RecallGate.is_relevant` for live recall, FR-309; the merge/disambiguate thresholds
  for upsert), and those two want different answers. Fakes of this port must reproduce
  that: an empty result models an empty *database*, never "nothing relevant". Directives
  (FR-207) are never part of this: first-class, their own table (TR-510), not a
  `MemoryType` — nothing to filter out here, unlike the retired Concept-backed scheme.
- **TR-510** `directives` (FR-207): `id`, `target_persona_id` (FK, `ON DELETE CASCADE`,
  TR-503), `action` (text, `"switch_persona"` today), `language`, `trigger` (JSONB —
  `{"keywords": [...], "max_words": N}`), `description`, `created_at`/`updated_at`. No
  `engagement_level`, no embedding column — not memory content (see the `Directive`
  glossary entry). `DirectiveRepository.list_all()` returns every row, ascending id;
  `upsert(directive)` inserts when `id` is `None`, else updates description/action/
  trigger in place (unused by `PersonaDirectiveSync` today — it only ever inserts —
  but there with no extra migration needed for a future editable-trigger feature).

## TR-6xx — Upsert pipeline

`services/upsert.py` (`MemoryUpserter`) — the single shared merge-or-insert path for
consolidation, enrichment, and bundle install.

- **TR-601** Pipeline: embed (`"{name}: {description}"`; episodes embed the summary) →
  similarity search (concepts/procedures: top 5 persona-scoped; episodes: top 1
  global) → threshold decision → merge or insert.
- **TR-602** Two-tier thresholds: similarity ≥ `merge_threshold` (default 0.93) →
  auto-merge; ≥ `disambiguate_threshold` (default 0.75) → LLM binary disambiguation;
  below → insert. Configured via `[memory]` in `mamemai.toml`; defaults are calibration
  placeholders.
- **TR-603** Merge behaviour: LLM synthesis replaces the description (and steps),
  embedding recomputed (INV-8); engagement = max (FR-305); existing `category` wins,
  new one only fills a gap; item id adopted from the existing row.
- **TR-604** Exact-duplicate short-circuit: identical name + description (+ steps)
  skips synthesis and re-embedding — the reinstall fast path.
- **TR-605** `exclude_ids` (concepts/procedures): candidates with these ids are
  filtered out before the threshold decision — the bundle installer passes its own
  same-run insertions (sibling exclusion, FR-604). Top-5 candidate fetch exists so an
  excluded sibling cannot hide a real pre-existing match. Default empty; live
  consolidation never passes it.
- **TR-606** Curated-content protection: `update_description=False` → a match keeps
  description, steps, and embedding verbatim (only engagement/category move) —
  `bundle_install` and `EnrichMemory` pass this for procedure upserts (FR-407); a
  discarded candidate (see TR-609) leaves `item.id` as `None`, the *discarded
  sentinel* callers must check before further use.
- **TR-607** Each `upsert_*` mutates the passed item in place and returns `True` on
  merge, `False` on insert-or-discard (disambiguated by the id sentinel).
- **TR-608** Episode merge synthesises the two summaries and re-embeds;
  `origin_conversation_id` is never reassigned (INV-15).
- **TR-608b** `MemoryRepository` read scope: `list_items(persona_id=None, …)` lists
  across every persona, for a persona declaring `reads_across_personas` (FR-303) — the
  same None-means-unscoped convention `search` already used, and a read path only.
  `list_episodes(limit)` returns episodes most recent first (`happened_at` descending,
  `created_at` breaking ties); episodes are unscoped by nature (INV-10) rather than by
  permission — a dedicated listing exists because similarity `search` alone cannot
  answer "what happened lately".
- **TR-609** `upsert_concept` is origin-aware (FR-310): candidates are searched as
  usual (TR-601/602), then split by `Concept.origin`. If the best *authored* candidate's
  similarity ≥ `authored_protection_threshold` (default 0.75, its own constant — not
  `disambiguate_threshold`, since it answers "protect curated content" rather than "same
  entity") the candidate is written as a touch on that authored item — verbatim
  description/embedding, engagement bumped, `update_description` ignored. Otherwise the
  normal two-tier merge/insert (TR-602) runs scoped to *organic* candidates only. A
  brand-new organic insert (`existing is None`) additionally requires `_has_engagement`:
  at least `min_concept_engagement_turns` (default **1** — provenance is binary) of the
  caller-supplied
  `user_turns` (raw text) must literally name the concept (`_mentioned_in` — whole-word,
  case-insensitive regex match on the candidate's `name`, also matching a parenthetical
  abbreviation split out of it, e.g. "XAI" out of "Explainable AI (XAI)"). Literal
  mention, not embedding similarity to the candidate, is deliberate: several sibling
  concepts extracted from one assistant monologue on a shared topic (e.g. AI/XAI/NLP/
  Transfer Learning) can sit close together in embedding space, so a similarity bar
  would let a couple of broadly-on-topic user turns satisfy engagement for *all* of
  them, not just the one actually followed up on — literal mention is the intersection
  of "what the conversation introduced" with "what the user's own words actually
  named," which similarity-to-topic cannot express. `user_turns=None` (the default;
  `bundle_install`/`EnrichMemory`
  never pass it) skips this check entirely — only live-conversation extraction has turns
  to evaluate.
  On a *merge* into an existing **organic** concept, a qualifying `_has_engagement` also
  promotes engagement one level (`_promote_engagement`, saturating at `INTEGRATED`)
  rather than taking `_max_engagement` — the significance axis of FR-310. Each
  consolidation transaction handles exactly one conversation, so a promotion is by
  construction one *distinct conversation*, never repetition within a sitting; recurrence
  is established by embedding match, so no stemming is needed in any language. A merge
  into an **authored** concept never promotes (`_max_engagement` only), and neither does
  any caller passing `user_turns=None`. `upsert_procedure` has no origin-awareness:
  procedures are always authored (FR-307), so `update_description=False` from the caller
  is their only protection.

## TR-7xx — Offline pipeline

`services/memory.py`, `server.py`.

- **TR-701** Trigger: on disconnect, an idle task sleeps
  `idle_consolidation_minutes × 60` then runs the pipeline; a new connection cancels
  the pending task. Pipeline exceptions are logged, never fatal.
- **TR-702** Order: replay (TR-406) → `ConsolidateMemory` → `EnrichMemory` →
  `GenerateMemoryBrief` (only when ≥ 1 conversation was consolidated). All against the
  offline connection (TR-006). `GenerateMemoryBrief` reads its material through
  `MemoryRepository.list_episodes` and `list_items(persona_id=None, …)` filtered to
  `origin == "organic"` (FR-308), and returns `None` without saving when both are empty.
- **TR-703** Per conversation, one transaction wraps: extraction-floor check (FR-307;
  below floor, `mark_consolidated(worthiness=False)` and nothing else runs) → worthiness
  evaluation → extraction (`extract_episodes` = the conversation persona's own
  declaration, FR-407/INV-10; never requests procedures) → episode upserts (worthy only,
  FR-307) → concept upserts
  (origin/engagement-gated, TR-609, FR-310 — independent of `worthy`) → assessment
  dispatch → `mark_consolidated`. Any raise rolls the whole conversation back for full
  reprocessing (FR-405).
- **TR-704** Assessment dispatch: after upserts, `assess_items(persona_id,
  conversation, touched)` where `touched` = items that hold an id (discarded misses
  excluded); returned `ItemAssessment.persona_state` dicts are persisted byte-for-byte
  via `update_persona_state` (INV-6).
- **TR-705** Enrichment: per persona with a strategy, `propose_items` drafts are forced
  to `UNSEEN` (INV-12); concept drafts get `origin="authored"` (FR-310); procedure
  drafts are upserted with `update_description=False` (FR-407) — both in one
  transaction per persona batch.
- **TR-706** Extraction language rule: the extractor receives
  `User.primary_language` and must write episode summaries in it (INV-10);
  `extract_episodes=False` — passed straight through from the conversation persona's
  `extracts_episodes` declaration (FR-407), with the generic default (true) applied only
  when the persona row is missing — omits the episode request from the prompt entirely.
  The extractor itself only ever sees a bool, never which persona set it or why. The
  `"procedures"` schema key is never requested, for any persona (FR-307) —
  `ExtractionResult` has no `procedures` field.
- **TR-707** Extraction floor (FR-307): `ConsolidateMemory` counts only
  `Speaker.USER` turns. Below `min_user_turns` (default 2) or below `min_user_words`
  (default 40, summed across those turns' `str.split()` word counts) the conversation
  skips worthiness evaluation and extraction entirely — `mark_consolidated(worthiness=False)`
  and no LLM call is made. Both are plain constructor defaults on `ConsolidateMemory`
  (calibration placeholders, like `merge_threshold`), not `[memory]`-configured.
- **TR-708** Worthiness/extraction content-quality rules (FR-307): the shared
  `WORTHINESS_SYSTEM_PROMPT` (`infrastructure/llm/_common.py`, used verbatim by both the
  Ollama and OpenRouter evaluators) explicitly excludes discussion about the assistant's
  own operation (bugs, testing, debugging, capability/configuration questions) from
  "worth storing." The episode section of `_extraction_system_prompt` requires a
  genuine, identifiable time or place, distinct from "during this chat"; `_parse_extraction`
  backs this with a hard rule — an episode with a missing or unparseable `happened_at`
  is dropped, never backdated to the conversation's own timestamp.

## TR-8xx — Language-tutor strategies

`infrastructure/language_tutor/` — reference implementation of the three ports.

- **TR-801** All tutor tunables live in `AssistantPersona.settings` (opaque, INV-6)
  with in-code defaults marked as calibration placeholders:
  `ranking` (`"engagement"`* | `"retention"`), `batch_review_share` (0.5),
  `episode_anchor_threshold` (0.6), `elicitation_cap` (2),
  `initial_half_life_days` (1.0), `half_life_growth` (2.0), `half_life_shrink` (0.5),
  `user_initiated_boost` (2.0), `pair_difficulty` (map keyed by learner language,
  `"*"` fallback), `interest_cluster_threshold` (0.55), `interest_cluster_min_size`
  (3), `enrichment_batch_size` (5).
- **TR-802** Selection pools: new = `UNSEEN` items in curriculum order (`created_at`,
  id); review = engagement > `UNSEEN`, ranked least-known-then-stalest (engagement
  mode) or ascending retention (retention mode) — the curve itself is generic
  (`domain.model.retention`, `2^(−days/half_life)`, raising on a non-positive
  half-life); reading it out of the tutor's own `persona_state` keys and ranking
  missing/invalid SRS state most due (a −1.0 sentinel) stays tutor-side, per INV-6.
- **TR-803** Batch composition: focus topic → similarity-ranked over the mode's pool;
  focus mode review/new → that pool; default mixed → `review_share` of the limit from
  review, rest new, each backfilling the other. Then category interleave (round-robin,
  first-appearance order, stable within category). Focus category filters items but
  falls back to all when it would empty the session.
- **TR-804** Focus interpretation: the verbatim wish + the persona's actually-present
  category values go to a `FocusInterpreter` LLM returning `TutorFocus(mode, category,
  topic)` — the interpreter can only target real taxonomy values.
- **TR-805** Episode pairing: per batch item, top-1 episode similarity ≥ threshold →
  anchor context with the episode summary; otherwise an elicitation hint, at most
  `elicitation_cap` per batch (FR-503).
- **TR-806** Assessment: per touched item, an LLM `PracticeJudge` yields
  `(retrievals, errors, user_initiated)` matched back by name (no judgment = exposure
  only, which still moves the day anchor); current stored `persona_state` is re-read
  from the repository (upsert output never carries it); conversation-mean
  assistant→user latency (TR-312) is folded in weighted low. Half-life: grows
  `×growth` on successful retrieval, shrinks `×shrink` on error, floor 0.5 days,
  initial value boosted for user-initiated items and scaled by pair difficulty;
  `last_practiced_at` is the conversation's date (day granularity).
- **TR-807** Enrichment: seeds = user-initiated concepts (flag from SRS state) with
  embeddings; greedy single-pass cosine clustering at `interest_cluster_threshold`;
  qualifying clusters need `min_size`; **one cluster per run** (largest); cluster
  language = majority vote; the `ClusterProposer` LLM returns up to
  `enrichment_batch_size` drafts.
- **TR-808** The tutor's SRS `persona_state` vocabulary is exactly the `state.py`
  constants (glossary: *SRS state*); mastery/next-due are always derived, never stored.

## TR-9xx — Persona bundle format

`infrastructure/bundle_toml.py`, `services/bundle_install.py`, `bundle_cli.py`. The
format **is** the port (version `BUNDLE_FORMAT_VERSION = 1`); parse-and-reject is the
only validation layer.

- **TR-901** A bundle is a directory: `bundle.toml` manifest + `lessons/*.toml`, lessons
  ordered by filename sort. A bundle must carry a `[persona]` definition, at least one
  lesson, or both; only the bundle with neither is rejected. Persona-only (no `lessons/`
  at all) is first-class — a persona that consumes the memory substrate rather than
  authoring curriculum has nothing to ship. The mirror case, content-only, is also
  supported.
- **TR-902** Manifest: `format_version` (must equal 1), `persona_key` (non-empty
  string), `[bundle]` table with required `name`, `version`, `author` (+ optional
  `description`), optional `[provenance]`; `[bundle]`+`[provenance]` are persisted
  verbatim to the install log (TR-905).
- **TR-903** Optional `[persona]` table: `name`, `system_prompt`, `languages`,
  `response_language`, `voices` (may omit `default` — derived from
  `User.primary_language` via the composition root's language→voice map), optional
  `settings` (copied verbatim), `strategy`, `extracts_episodes` (bool, default true —
  FR-407) and `reads_across_personas` (bool, default false — FR-303); a non-bool for
  either is rejected rather than coerced. Unknown keys are rejected.
  `languages` may be absent or empty (= no language restriction) and `response_language`
  may be absent (= derive from `User.primary_language` at install): a persona that is
  not *about* a language has no target list to give, and a
  bundle cannot know which language its user speaks. Same pair-independence rule that
  already governs `voices["default"]` — an English-authored bundle must not answer a
  French user in English. A bundle that does care still states both.
- **TR-904** Install algorithm: resolve persona by `persona_key` (create from
  `[persona]` when absent — requires completed onboarding AND every bundle target
  language installed (FR-609, checked against `resolve_installed_languages` of the same
  `[languages].installed` the server reads); error when neither persona nor definition
  exists; existing persona + `[persona]` → **update in place**, with a notice naming
  what changed). Persona `languages` = bundle list + primary language appended iff
  absent — the *session language pair* — unless the bundle's list is *empty*, which
  already means "no restriction" and is left empty rather than narrowed to the primary
  language alone. An absent `response_language` is likewise resolved to
  `User.primary_language` at install.
  The update is split by **who can know the value**, not by convenience. The bundle
  refreshes what only its author can know — `system_prompt` (the main thing an upgrade
  delivers), `settings`, `extracts_episodes` (FR-407) and `languages`. It never touches
  what the user can change by voice — `name` (also what the *Directive* triggers are
  built from), `voices`, `speaking_rate`, `response_language` — because a content update
  silently undoing a spoken preference is the worst kind of surprise. `strategy` and
  `persona_key` are untouched either way: identity set at creation, and
  `PersonaRepository.save()` does not carry them on update, so a bundle cannot rebind an
  installed persona to different code. The update is unconditional, not version-gated:
  comparing versions would need a semver ordering the format does not specify, and "the
  bundle you installed is the one in effect" is easier to predict. Same shape as two
  existing rules — `upsert_concept` keeps curated wording immutable while the user's
  engagement moves (TR-609), and `persona_state` is written only by its owner (INV-6):
  each side owns its own fields.
  Then per lesson, one transaction;
  per item, upsert as `UNSEEN` with same-run sibling exclusion (separate id sets per
  memory type). A failed run is recovered by re-running (committed lessons merge via
  TR-604).
- **TR-905** Install log: one `bundle_installs` row per run — persona_key (plain text,
  survives persona deletion), bundle identity, timestamps, inserted/merged counts,
  manifest verbatim. Read by nothing; not a reinstall guard.
- **TR-906** `mamemai-bundle install <path>` runs as its own process (own DB connection,
  embedding model, config); documented caveat: run while the server is idle (a
  concurrent consolidation could race the same persona's upserts — documentation, not
  locking, per INV-3).

## TR-95x — Configuration & models

- **TR-950** The wizard's `ConfigureUserIdentity` step (right after
  `ConfigureDatabaseConnection`) derives the Postgres schema slug from the free-text
  answer to "who is this install for?" via `domain.slugify.schema_slug`: NFKD-normalise
  to ASCII, lowercase, collapse every run of characters outside `[a-z0-9_]` to a single
  `_`, strip a leading digit (not a valid identifier start), fall back to the literal
  `user_1` when nothing usable survives (blank input, or input with no ASCII residue).
  The derived slug is shown back to the user as the default for a second prompt they may
  override outright — `SetupSchema` uses whatever that second prompt returns, not the
  bare derivation, since the schema becomes immutable once created (FR-709, TR-501b). A
  blank answer to the identity question itself falls back to the literal display name
  `"user-1"` (FR-709), independently of what the schema ends up named.
- **TR-951** Server config `mamemai.toml` (platform config dir; wizard-generated):
  `[server] ws_port=8765, log_dir=<platform data dir>/sessions` (absolute,
  `platformdirs.user_data_dir("mamemai")` — session logs are persistent data, INV-5, not
  settings; user-overridable via this same key), `session_tail_turns=10` (FR-109;
  0 disables session-tail injection entirely — not voice-configurable, FR-701 doesn't
  apply, same technical-tuning-knob posture as `[memory]`'s thresholds below);
  `[database] url` (libpq DSN; peer
  auth default on Linux/macOS), `schema` (TR-501b — wizard-decided, immutable, absent
  → `"public"`); `[user] name` (bootstrap seed for `User.name`, TR-008 — absent →
  the literal default `"user-1"`; omitted entirely from a re-run's config that
  never asked, since there is nothing new to seed); `[stt] model_path, device cuda|cpu, compute_type`
  (float16↔cuda, int8↔cpu), `cpu_threads=8` (see TR-957), `min_utterance_ms=300`
  (see TR-958; 0 disables the floor); `[embedding] device` (absent → auto-detect; see
  TR-960 — set it explicitly, and not necessarily to the same device as `[tts]`);
  `[tts] device` (absent →
  Kokoro auto-detect); `[llm]
  model="aya-expanse", ollama_host?` (always: the local Ollama model for the offline
  pipeline, TR-955), `num_ctx=16384` (the context window sent on every *local* Ollama
  call — live and offline alike — needed whatever `provider` is set to, since the offline
  pipeline never leaves local Ollama; see TR-952 for why it is explicit and why 16384;
  not voice-configurable, same technical-tuning-knob posture as the rest of this table),
  plus `provider="ollama"|"openai_compatible"` (default `"ollama"`,
  omitted from written configs in the common case), and, only when `provider =
  "openai_compatible"`: `base_url`, `remote_model` (both required), `api_key` (optional)
  — the live-conversation backend, FR-707; `[memory] merge_threshold=0.93,
  disambiguate_threshold=0.75`; `[languages] installed=[codes]` (the installed
  languages, FR-705 — absent → all of `SUPPORTED_LANGUAGES`). Bootstrap-before-DB
  settings only (FR-701); installed languages qualify because they are a property of
  the installation (which TTS voices were pulled), changeable only via the wizard.
- **TR-952** Models: STT `faster-whisper` (beam 5, auto language); LLM via Ollama
  streaming, or an OpenAI-compatible remote endpoint for live conversation only
  (TR-955) — avoid ~70B-class Ollama models (VRAM eviction/cold-reload; avoid
  reasoning models — `<think>` blocks get spoken); TTS Kokoro (one lazily-created
  pipeline per voice-prefix language, output resampled 24 kHz → 16 kHz float32);
  embeddings `intfloat/multilingual-e5-large` (1024-dim, always local/CPU-or-GPU —
  never remote, TR-955), encoded **with the `query:`/`passage:` prefixes it was trained
  with** (TR-508b).
  **Every local Ollama call sends `num_ctx` explicitly** (`[llm] num_ctx`, TR-951).
  Ollama's own default is 4096 tokens and it truncates longer prompts *silently* — no
  error, no warning, no field in the response — so the model answers about a prompt it
  was never shown; there is no inherent "length cliff" in the model's attention, only
  silent truncation at whatever `num_ctx` is set to. 16384 is sized to the workload,
  not to the model's ceiling (llama3.1:8b reports 131072): the KV cache scales with
  `num_ctx`, so the useful value is min(model ceiling, what VRAM allows, what the
  workload needs).
  **A prompt that still exceeds the budget is announced, never truncated in silence** —
  the defect is the silence, not the overflow. Estimated prompt tokens are compared
  against `num_ctx` minus a flat output reserve before every call whose input grows with
  the conversation (live reply, worthiness, extraction, tutor practice judge); over
  budget prints an `[llm] CONTEXT OVERFLOW` line (TR-311 family). Estimation is
  characters÷3, deliberately not a real tokenizer: it over-counts for these languages,
  which is the right direction for a warning, and the decision it feeds is only ever
  "shout or don't". What each caller does with an over-budget prompt — skip it or ask
  anyway — is FR-307's. A remote endpoint (FR-707) takes no `num_ctx`: its context is
  fixed and it returns an explicit error rather than truncating, so the remote path's
  equivalent obligation is to stay inside a declared budget and surface that error.
- **TR-953** Offline LLM-backed judgment adapters (worthiness, disambiguation,
  synthesis, extraction, tutor judge/cluster) all use the same configured local Ollama
  model/host, regardless of `[llm].provider` (TR-955). OpenRouter twins of this offline
  family exist in `infrastructure/llm/openrouter.py` but are not wired into the
  composition root (deployment-alternative groundwork, distinct from the live
  OpenAI-compatible pair that TR-955 documents as wired in).
  Both providers share their judgment prompts *and* their answer parsing via
  `llm/_common.py` (`WORTHINESS_SYSTEM_PROMPT`, `is_affirmative`), plus their transcript
  windowing (`worthiness_transcripts`, `extraction_transcripts`, `merge_extractions`,
  FR-307), so the four yes/no call sites — worthiness and disambiguation, twice each — and
  the two extraction paths cannot drift apart. `is_affirmative`
  decides on the reply's first word against the supported languages' yes/no vocabulary,
  falls back to a whole-*word* scan (never substrings: "si" is Spanish for yes and lives
  inside "consider"), and treats an unreadable answer as not-yes (FR-307).
- **TR-954** Outbound TLS uses the OS trust store (`truststore`) — corporate-proxy
  resilience for any adapter that still touches the network.
- **TR-955** Live conversation's main reply (`ProcessTurn`'s call to
  `LLMService.complete()`) is the only part of the pipeline `[llm].provider` affects
  (FR-707): `"ollama"` (default) uses `OllamaLLMService` against `[llm].model`/
  `ollama_host`; `"openai_compatible"` uses
  `OpenAICompatibleLLMService` (`infrastructure/llm/openai_compatible.py`, a generic
  `openai.AsyncOpenAI` client against `[llm].base_url`, any OpenAI-compatible provider —
  OpenRouter, OpenAI, a self-hosted server; a missing `api_key` is coalesced to a
  placeholder string, since the client library requires some value even against
  endpoints that don't check it) against `[llm].base_url`/`remote_model`/`api_key`.
  Recall gating (TR-314) does *not* move with this setting — it is not an LLM call at
  all, so it has no live-provider dimension to branch on. Every other LLM-touching port
  (offline judgment adapters, TR-953; `GenerateMemoryBrief`, via a dedicated
  `ServerContext.offline_llm` instance never aliased to the live `llm`; Ollama-backed
  persona-strategy helpers, e.g. the tutor's focus interpreter) always stays on the
  local Ollama model, unaffected by this setting — a GPU-less/CPU-only offline run is
  accepted as slower, not moved off-machine.
- **TR-956** Hallucination filtering in `infrastructure/stt.py`, three lines of defence
  for FR-103, applied per decoded segment so one bad segment never discards the real
  speech beside it. (1) `vad_filter=True` (Silero, bundled with faster-whisper) drops
  non-speech stretches before decoding. (2) Segments with `no_speech_prob >=
  _NO_SPEECH_THRESHOLD` (0.6) are dropped — Whisper's own admission that a decode wasn't
  speech. (3) A **stock-phrase blocklist** (`_is_stock_hallucination`) drops segments
  whose normalised text contains any known caption-boilerplate fragment, regardless of
  `no_speech_prob`. (3) exists because (2) is structurally unable to catch a *confident*
  hallucination — e.g. "Thanks for watching, and I'll see you in the next video." on a
  silent mic can clear the 0.6 threshold. These artifacts are
  decodings of noise, not misheard speech, so no acoustic signal separates them — only
  the text does, and the phrases are stable because they saturate Whisper's caption
  training data. Matching is substring-on-normalised-text; `_normalise` folds
  punctuation, capitalisation and diacritics (`I'll` → `ill`, `Amara.org` → `amara org`,
  `comunità` → `comunita`) for matching only, leaving the returned transcript untouched.
  Entries must be long and specific enough that a single user talking to their own
  assistant never says one by accident — the blocklist is extended from observed output,
  not speculation, because a false positive silently swallows a real turn. Every drop
  prints an `[stt] dropped stock hallucination` line, the only place a false positive
  would surface.
- **TR-957** STT CPU threading: `[stt] cpu_threads` (default `DEFAULT_CPU_THREADS = 8`) is
  passed to `WhisperModel`, overriding CTranslate2's own intra-op auto-detection, which
  under-uses a multi-core box. 8 is a measured constant, not `nproc`: throughput peaks
  around 8 threads and degrades beyond it, since the int8 GEMM saturates memory
  bandwidth before it runs out of cores — so binding to core count would be a slower
  default. Transcripts are byte-identical at every thread setting, so this is free
  latency and never an accuracy trade. Overridable, since the sweet spot is
  hardware-specific.
  The model is `medium`, not `large-v3-turbo`: on CPU the encoder dominates cost, and
  `large-v3-turbo`'s encoder is large-v3-sized despite its lighter decoder, so it runs
  slower than `medium` here for negligible accuracy gain; on a GPU the ranking would
  likely invert (untested — this machine's GPU is AMD, and CTranslate2 has no ROCm
  backend). `beam_size` stays at 5, not reduced: on CPU the encoder pass over a fixed
  30 s mel window dominates and beam width only touches the decoder, so a smaller beam
  buys negligible latency at a real accuracy cost.
- **TR-958** Minimum utterance floor: `end_utterance` whose buffered audio is shorter than
  `[stt] min_utterance_ms` (default 300) is dropped in `server.py` before any STT call,
  with an `[stt] skipped …ms utterance` line. Duration is derived from TR-101's wire format
  (`utterance_duration_ms`: int16 mono 16 kHz = 32 bytes/ms) and so measures **voiced**
  audio, not wall-clock — TR-203 excises inter-word silence, so a 6 s sentence arrives as
  roughly 3 s of audio.
  This is the server-side **backstop**, not the fix: TR-203's onset gate is what stops the
  noise, and this catches what reaches the server anyway — client and server are separately
  installed packages and can be out of step, and a noisier room or a different VAD
  aggressiveness arrives here identically. The floor is deliberately low, set to reject
  what is unambiguously not speech rather than to discriminate finely: a one-word answer is
  only ~300 ms of voiced audio, and a false positive here swallows a real turn silently —
  the same asymmetry that governs TR-956's blocklist. Every drop is printed for the same
  reason: it is the only place a false positive would surface. `0` disables it.
- **TR-959** Kokoro's LSTM dropout is zeroed at load (`_neutralise_lstm_dropout`, applied
  to `KPipeline.model` on every pipeline construction). Kokoro's `DurationEncoder` builds
  each LSTM as `nn.LSTM(..., num_layers=1, dropout=0.1)`; with one layer there is no
  inter-layer gap for dropout to apply to, so it never fires on any backend, in training
  or inference — PyTorch emits its own warning saying so. MIOpen nonetheless builds its
  RNN descriptor from the *argument*, JIT-compiles a dropout kernel whose source includes
  `<rocrand/rocrand_xorwow.h>`, and fails: the PyTorch ROCm wheel does not ship that
  header, so synthesis dies with a bare `miopenStatusUnknownError` and no indication of
  the cause. Zeroing removes that one
  kernel while leaving every other MIOpen path intact — unlike disabling the backend
  wholesale, which would also give up the convolutions. Applied unconditionally, not only
  on ROCm: verified bit-identical output either way, so a device-conditional branch would
  buy a second code path and nothing else.
- **TR-960** Compute-device placement is **per model, not per machine**, and each of
  `[stt] device`, `[tts] device` and `[embedding] device` is set independently.
  Auto-detect is not a safe default once torch can see a GPU, because it picks one for
  everything. TTS (Kokoro) runs on CPU and embeddings run on GPU when available — the
  opposite of what a single "GPU if present" default would pick. The split is a
  property of the models, not a quirk: Kokoro is 82M parameters dominated by
  *sequential* LSTM steps, so it is latency-bound and a wide parallel device buys it
  nothing while adding per-kernel launch overhead and a costly cold JIT-compile pass on
  first GPU use; e5-large is a 560M transformer whose work is one big parallel pass, so
  it benefits from the GPU. `[tts] device` must be explicit even when the value is
  `"cpu"`, and the integration TTS test pins it.
- **TR-961** Episode extraction runs as its **own pass over the user's turns only**
  (`user_turns_only`), separate from the concept pass which keeps the full transcript.
  Two passes, not one, because the two need opposite inputs: concepts need the assistant's
  turns — that is where correct terminology sits against STT-mangled user turns (FR-310's
  hazard) — while episodes must not see them at all. Costs one extra call per conversation
  that extracts episodes; personas with `extracts_episodes=False` (FR-407) skip it
  entirely. `_extraction_system_prompt` gained `extract_concepts` as the mirror of the
  existing `extract_episodes`, so each pass requests only its own array.
  The fabrications this stops are summaries of *assistant* content — the model
  narrating its own turns as autobiographical events — so on a user-only transcript
  they are not discouraged but **unconstructible** — the FR-114 posture, a filter
  rather than a plea.
  Second, independent filter: **`drop_chat_dated_episodes` discards any episode whose
  `happened_at` falls within its own conversation's span** (± 60 s tolerance, to absorb
  timestamp truncation). The prompt already demands a time "distinct from 'during this
  chat'" and cannot enforce it — a model that files the conversation itself as a life
  event dates it with the conversation's own timestamp. Every drop prints its reason.
  The two are complementary, not redundant: an episode invented as a past event and
  dated plausibly in the past is caught only by the user-only-transcript filter; "the
  user was having a conversation with an Italian tutor" survives a user-only transcript
  and is caught only by the date filter.
  An episode with a null `happened_at` is kept — undated is not evidence of being about
  the chat, and dropping it would be a different policy from the one stated here.
  Third, independent filter, added after a live miss neither of the above caught
  (2026-08-06): a session where the user asked nothing but general astronomy questions —
  no assistant fabrication involved, no in-chat timestamp — still produced a stored
  episode claiming the user had personally seen the northern lights, invented purely
  from the topic word "aurores boréales" appearing in their own turns. The extraction
  prompt now asks for an `"evidence"` field per episode: a verbatim quote from the
  user's own turns proving they said it. **`drop_ungrounded_episode_entries` discards
  any episode entry whose `evidence` does not literally occur (case/accent/punctuation-
  insensitive) in the transcript that produced it** — checked against the same window
  text the model was actually shown, before the entry is parsed into a domain `Episode`.
  Same posture as the other two: not a prompt asking the model not to invent, a
  post-hoc check an invented answer cannot pass, since copying a real quote is trivial
  and inventing one that also happens to appear verbatim in the source is not.
