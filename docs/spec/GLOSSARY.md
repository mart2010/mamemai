# Glossary — the Ubiquitous Language

*Last verified against code: 2026-08-06*

Terms are grouped in three tiers. Within Mamemai documents, code, tests, and
conversation, these terms are used **exactly as defined here** — if a term feels
imprecise during design work, fix it here first, then everywhere else.

Conventions: `code style` marks identifiers that appear verbatim in the codebase.
Cross-references are *italicised*.

---

## 1. Voice-pipeline technical terms

General AI/audio engineering vocabulary, as used in this project.

| Term | Definition |
|---|---|
| **STT** (speech-to-text) | Transcribing audio into text. Mamemai: `faster-whisper`, which also auto-detects the spoken *language* per *utterance*. |
| **TTS** (text-to-speech) | Synthesising audio from text. Mamemai: Kokoro, one multilingual model, one *voice* selected per synthesis call. |
| **LLM** (large language model) | The conversational/reasoning model, streamed token by token. Mamemai: local via **Ollama** (default `aya-expanse`) for everything by default; live conversation only may instead use a remote OpenAI-compatible endpoint (`[llm].provider = "openai_compatible"`, FR-707/TR-955) — the offline memory pipeline and every Ollama-backed strategy helper always stay local regardless. |
| **VAD** (voice activity detection) | Classifying an *audio frame* as speech or silence. Mamemai: `webrtcvad` on the client, aggressiveness 2. |
| **Audio frame** | The smallest audio unit the client processes: 30 ms of 16 kHz mono audio (480 samples). VAD operates per frame. |
| **Audio buffer** | Server-side accumulation of binary audio frames for the current *utterance*, flushed to STT on *end-of-utterance*. |
| **PCM** | Raw uncompressed audio samples. Client→server: PCM int16. Server→client: PCM float32 (Kokoro output resampled 24 kHz → 16 kHz). |
| **Sample rate** | 16 kHz end-to-end on the wire (`SAMPLE_RATE`); Kokoro's native 24 kHz is resampled server-side. |
| **Token (LLM)** | The LLM's streaming output unit. Mamemai consumes the stream incrementally: response *markers* are resolved as tokens arrive, sentences are synthesised as they complete. |
| **Context window** | The LLM's bounded input. Mamemai treats it as the computational analogue of human *short-term memory*, actively managed via *working memory*. |
| **System prompt** | The instruction block prepended to every LLM call — composed per turn from the persona's own prompt plus injected context (see `_compose_working_context`). |
| **Embedding** | A 1024-dim vector representation of text (`multilingual-e5-large`), stored with every *memory item* and used for *similarity search*. |
| **Similarity search** | Nearest-neighbour lookup over embeddings. Mamemai: pgvector cosine distance, reported as **cosine similarity = 1 − distance** in [0, 1]. |
| **pgvector / HNSW** | PostgreSQL extension for vector search / the approximate-nearest-neighbour index used on all three memory tables. |
| **Streaming synthesis** | Speaking the reply sentence-by-sentence while the LLM is still generating, instead of waiting for the full response — the key latency device. |
| **WebSocket** | The single client↔server channel (default port 8765): binary frames for audio, JSON text frames for control messages. |
| **SSH tunnel** | Client-established port forward (`localhost:ws_port → server:ws_port`) in split-host deployments; the only network exposure is SSH. |

## 2. Voice-assistant domain terms

Vocabulary of conversational voice systems generally.

| Term | Definition |
|---|---|
| **Utterance** | One continuous stretch of user speech, delimited by VAD: from *utterance onset* to *end-of-utterance*. The unit STT transcribes. |
| **Utterance onset** | The client-side decision that the user has *started* speaking: `onset_frames` consecutive speech frames (default 3, ~90 ms). A shorter run is room noise and opens nothing. |
| **Pre-roll** | The `preroll_frames` frames (default 10, ~300 ms) captured *before* onset and sent with it, so the transcriber hears the attack of the first word. The only silence inside an utterance that reaches STT. |
| **End-of-utterance** | The client-side decision that the user stopped speaking: **more than `silence_frames` consecutive silent frames** (default 50, ~1.5 s) after speech; signalled to the server as `end_utterance`. Per-speaker, not a constant — it is how long *this* user pauses mid-thought. |
| **Turn** | One utterance and its reply half. Mamemai models both halves: a user `Turn` (transcribed utterance) and an assistant `Turn` (full response text). |
| **Barge-in** | Interrupting the assistant mid-reply by speaking. **Out of scope by design** (INV-4): the reply plays to completion before the mic re-opens. |
| **Mic muting** (half-duplex) | Suppressing VAD/capture while the assistant speaks so it does not hear itself. Client-side: muted from first audio chunk until `speaking_end`. See also *acoustic echo* (open issue when muting windows misalign). |
| **Onboarding** | The first-launch flow: language selection via terminal prompt, then a spoken introduction. Complete once `User.primary_language` is set. |
| **Wake word** | Not used — Mamemai sessions are explicitly started (client launch); the mic is live whenever the assistant isn't speaking. |
| **Latency to first audio** | Time from end-of-utterance to the first synthesised audio chunk — the responsiveness metric the streaming design optimises; instrumented via `[latency]` log lines. |
| **Session** | One client connection lifetime: WebSocket connect → disconnect. Identified by a UUID; produces exactly one *session log* file. **Not** the same as a *conversation*. |
| **Persona** | A configured assistant identity. In generic voice-assistant usage a persona is a prompt+voice; in Mamemai it is a first-class entity — see tier 3. |

## 3. Mamemai domain terms

Mamemai's own model — the terms that carry the design. (Entities/value objects in
`server/src/mamemai_server/domain/`.)

### Actors & personas

| Term | Definition |
|---|---|
| **`User`** | The single human. Owns `primary_language` (null until onboarding; changed only explicitly, INV-14) and `idle_consolidation_minutes`. Single-user is a system-wide assumption (INV-3). (`secondary_languages` is a dead column: never populated — superseded by *installed languages*, from which secondary languages are derivable as installed − primary; removal pending.) |
| **Installed languages** | The wizard-selected subset of `SUPPORTED_LANGUAGES` whose TTS voices were actually pulled at install time; recorded in `mamemai.toml` `[languages].installed` (bootstrap-only, FR-705). Bounds onboarding language selection; adding one means re-running `mamemai-setup`. |
| **Read scope** | Whether a persona may read concept/procedure knowledge owned by *other* personas: `AssistantPersona.reads_across_personas`, default false (FR-303). For a persona that works from the user's existing memory rather than authoring its own — without it, it could only quiz on what it was told in its own sessions. Read-only: writing stays scoped (INV-9). Episodes are outside this entirely, being persona-independent (INV-10). |
| **`AssistantPersona`** | A specialised assistant: own `system_prompt`, `response_language` (no effect for cast personas, whose prompt owns language use — FR-105), `voices` map, `speaking_rate`, `languages` (the *session language pair*), `extracts_episodes` (see **Rehearsal persona**), and own scoped memory. Aggregate root of the persona context. |
| **Session language pair** | `AssistantPersona.languages`: the input languages expected while the persona is active — a tutor's bundle target list + the primary language (appended at install, TR-904); empty = no restriction (the GA accepts any *installed language*). Every target entry must be installed (FR-609). A detected third language during a tutor session is read as a pronunciation-stumble signal, not a language switch. |
| **Language tag** | The `[lang:code]` prefix on every user turn as rendered into the LLM context (FR-114): the STT-detected utterance language, surfaced so the model can reason about what the user actually spoke. Rendering-only — never stored, and stripped from responses before TTS if the model mimics it. |
| **GeneralAssistant (GA)** | The one system persona (`is_system`, fixed UUID `…0001`), the cross-domain catch-all and the persona every session starts on. Cannot be removed or deactivated. |
| **Persona switch** | Changing the `active_persona` mid-session, triggered by a matched *Directive* (FR-207) — deterministic, decided before the LLM is even called for that turn, not by anything the LLM decides or emits (retired: the `[PERSONA:name]` *response prefix marker* scheme). Deliberately distinct from the *voice cast* — switching personas changes who's conversing; the cast is which Kokoro voice narrates a given persona's own segments. |
| **Persona lifecycle** | Non-system personas can be **deactivated** (kept, memory intact; its *Directive* row is left in place, not cleaned up — its trigger keeps matching, but a switch to it is blocked, deterministically, FR-207) or **removed** (deleted — cascades to their concepts/procedures **and** their own *Directive* rows, INV-9; no explicit cleanup code needed, unlike the retired Concept-backed Directive scheme). |
| **Directive** | A user utterance that changes mamemai's own operating state rather than being answered in conversation (FR-207). Persona switching is the first directive type. First-class (`domain.model.Directive`, its own `directives` table — not a `Concept`): `target_persona_id`, `action` (e.g. `"switch_persona"`), `language`, `trigger` (`{"keywords": [...], "max_words": N}`), `description`. Matched by a deterministic keyword/word-count rule against the turn's own utterance (`services/directives.matches_trigger`) — no embedding call, not an LLM decision. One row per language the persona is used in (`persona.languages`, translated action verb + the persona's own name tokens); the "return to GA" directive similarly gets one row per installed language. `PersonaDirectiveSync` keeps Directive rows in sync with persona create (`target_persona_id ON DELETE CASCADE` handles remove) and bootstraps the return-to-GA directives on every server startup. |
| **Voice cast** | A persona's `voices` map: IETF language code → Kokoro voice (`"default"` is the mandatory fixed native anchor, single voice, INV-7). Which voice narrates a segment is decided automatically from that segment's own detected dominant language — no LLM-emitted tag involved. |
| **Rotation pool** | A non-default `voices` key's value of `"a\|b\|c"` form: one voice is picked per session (`session_id % len`), stable within a session, varying across sessions (*HVPT*). |
| **`persona_key`** | Author-namespaced bundle identity (e.g. `mamemai/italian-tutor`), unique, set once at install; null for GA and user-created personas. |
| **`strategy`** | The persona's declared strategy-set name (e.g. `"language_tutor"`), resolved against the composition root's registry; null binds nothing; unknown names warn and bind nothing. |
| **`settings`** | Opaque persona-owned tunables (JSONB), copied verbatim from a bundle; read only by the persona's own strategies (same opacity contract as `persona_state`, INV-6). |

### Conversation & session structures

| Term | Definition |
|---|---|
| **`Conversation`** | A topic-bounded exchange: an ordered list of `Turn`s under one persona. **Derived offline** from session logs via *boundary markers* — it does not exist as an object during the live session. The consolidation unit. |
| **Conversation boundary** | Where one *conversation* ends and the next begins within a *session*. There is exactly one source: a *Persona switch* forces `BREAK` deterministically (TR-315), because a conversation is attributed to one persona for consolidation. Topic-based splitting within one persona's stretch has no replacement yet; that is a known accepted gap, not an oversight. |
| **Session log** | The append-only JSONL file for one session (`logs/sessions/YYYY-MM-DD_<session_id>.jsonl`) — the **only** live-path write (INV-1). Kept forever (INV-5). |
| **Replay** | `TurnLogReplayer`: reading unprocessed session logs and materialising `Conversation` rows in the DB. Runs on every connect (crash recovery) and at the start of the offline pipeline. Idempotent. |
| **Working memory** | The per-session in-RAM state (`WorkingMemory`): user, active persona, *Directive*s, *memory brief*, recent turns, *rolling summary*, *session tail*, *selection batches*. The live analogue of short-term memory. |
| **Session tail** | The last N (10) turns of the previous session, injected at session start when the previous session ended within 24 h — conversational continuity across connections. |
| **Rolling summary** | LLM compaction of the oldest half of `recent_turns`, triggered every 50 turns — bounds the live context. |

### Long-term memory

| Term | Definition |
|---|---|
| **Memory item** | Umbrella for the three long-term types: `Episode` \| `Concept` \| `Procedure`. All carry a 1024-dim embedding. |
| **`Episode`** | Episodic memory: what happened, anchored in real-world time (`happened_at`). Persona-independent; summary always in the user's primary language (INV-10); `origin_conversation_id` is fixed provenance (INV-15). |
| **`Concept`** | Semantic memory: distilled knowledge about one subject, **persona-scoped**. `description` is a tight LLM synthesis (~300 words cap), not an append log. (*Directive*, FR-207, is a separate first-class model now, not a `Concept` field — see that entry.) |
| **`Procedure`** | Procedural memory: how to do something — `description` (always) + `steps` (only when cleanly decomposable). Persona-scoped like `Concept`. |
| **`category`** | Free-text classifier on Concept/Procedure, interpreted **only** in the owning persona's vocabulary (e.g. the tutor's `noun`/`idiom`/`contrast_pair`); generic code passes it through, never enumerates it. |
| **`persona_state`** | Opaque JSONB slot on Concept/Procedure. Single-writer contract (INV-6): written only by the owning persona's *assessment strategy*, read only by its *selection strategy*. |
| **Engagement level** | Generic coarse learning-depth ladder: `unseen → mentioned → explored → integrated` (`EngagementLevel`). Written only by generic consolidation. For **organic** concepts it is the *significance* axis: one step per distinct later conversation in which the user names the item again (FR-310), saturating at `integrated`. For **authored** content it stops at the single `unseen → mentioned` bump — climbing further is the owning persona's business, via `persona_state`, so that a persona which elicits its own material cannot inflate the signal FR-506 ranks on. Merges otherwise keep the max. |
| **Memory brief** | The distilled "what I know about the user" text, regenerated offline after consolidation and injected into the system prompt at every session start. |
| **Language of first introduction** | `Concept.language`/`Procedure.language`: fixed at creation (INV-13); the description is maintained in that language forever, other-language evidence is translated into it during synthesis. |

### Recall & selection (live reads)

| Term | Definition |
|---|---|
| **Recall** | Utterance-triggered memory lookup: the turn's own text is embedded (no separate query extraction) and top-5 similar items (persona-scoped for concepts/procedures) are injected into the turn's context, whenever a `RecallGate` allows it. Reactive and question-gated for every persona (no active overrides): only an utterance that reads as an actual question triggers it at all, never a plain statement (FR-309); the reply is nudged to say so either way, acknowledging a real find or admitting a genuine miss, so it never reads the same as a turn recall didn't run on at all. Never touches *Directive*s (FR-207) at all — first-class, their own table, matched separately and deterministically before recall even runs. |
| **Recall gate** | The persona-scoped policy (`RecallGate`) on whether a turn's recall search runs at all — `should_embed` short-circuits anything that isn't a real, substantial question before any embedding is computed (`DefaultRecallGate`: needs a literal "?" and a word-count floor; every persona uses this, no active overrides), `should_search` skips a fresh DB round trip when the turn's embedding is nearly identical to *any* prior search this session (the whole `recall_history`, not just the last one — nothing new can enter memory mid-session, INV-1, so an earlier match is just as good), reusing that search's cached results instead. Local, deterministic logic — every persona resolves to some gate, unlike the three ports below. |
| **Selection** | Persona-driven proactive injection via `PersonaSelectionPort`: a batch of `SelectedItem`s fetched lazily on the persona's first active turn, consumed **one item per turn**. Proactive counterpart to recall. |
| **Selection batch** | The fetched item list per persona in working memory. Key presence = already fetched; one item is popped per turn. Whether an exhausted batch is re-queried is the persona's own *selection pacing*, not a property of the batch. (A *focus* change would replace it, but nothing supplies a focus today — TR-304.) |
| **Selection pacing** | `SelectionPacing`, declared on a `PersonaSelectionPort` and applied by generic code: `refetch_when_exhausted` and `include_activation_turn` (TR-306) — declared, not hard-coded, so no persona's UX choices are binding on any other; a strategy declaring nothing gets sensible defaults. Distinct from *selection* itself — pacing is *when and how often* content is served, selection is *which* content. |
| **Focus** | The user's expressed session wish ("just review old words"), passed **verbatim** to `select_items(focus=…)` and interpreted only by the strategy. `None` = default learning path. **Currently always `None`**: the `[FOCUS: …]` response marker that used to carry it was retired with the in-band channel (FR-502/TR-304), and detecting the wish on the *user's* turn instead is deferred. The term and the strategy-side machinery stay because the feature is coming back, not because anything feeds them today. |
| **`SelectedItem.context`** | Free text composed by the selection strategy (episode anchor or elicitation hint), injected verbatim — generic code never interprets it. |
| **Retention** | Derived-at-selection-time recall estimate, `2^(−days_since / half_life_days)`; never stored. The *formula* is generic (`domain.model.retention`) — any spaced-retrieval persona needs the same curve. Everything around it is persona-owned: which `persona_state` keys hold the inputs, what grows or shrinks the half-life, and what an item with no usable state ranks as (INV-6). Generalise the calculation, not the data. |

### Consolidation & offline pipeline

| Term | Definition |
|---|---|
| **Live/offline boundary** | THE architectural invariant (INV-1): live conversation may read the DB but writes only session logs; all DB writes, LLM extraction, embedding generation for storage, and upserts happen offline. |
| **Offline pipeline** | The post-disconnect sequence: replay → consolidation → enrichment → memory-brief regeneration. Triggered by the *idle timer*; doubles as crash recovery. |
| **Idle timer** | Countdown started at disconnect (`User.idle_consolidation_minutes`, default 5); a reconnect cancels it. |
| **Consolidation** | `ConsolidateMemory`: per unconsolidated conversation — extraction floor check, worthiness check, LLM extraction, upsert of extracted items, persona assessment, mark consolidated. One DB transaction per conversation. |
| **Extraction floor** | A cheap, deterministic pre-check (minimum user turns and user words, counting only the user's own turns) that skips worthiness evaluation and extraction entirely below it — pure cost control (FR-307). |
| **Worthiness** | The `WorthinessEvaluator`'s judgment of whether a conversation, past the extraction floor, is substantial enough to yield **episodes**. Its criteria explicitly exclude discussion about the assistant's own operation and require genuine time/place grounding — gates episodes only, not concepts. |
| **Concept origin** | `Concept.origin`: "authored" (bundle install, persona enrichment) vs "organic" (live-conversation extraction) — immutable once set, like `language`. Drives whether `MemoryUpserter.upsert_concept` protects a match as curated content or treats it as ordinary organic enrichment (FR-310). Procedures have no such field — they're always authored (FR-307). |
| **Authored-content protection** | A live-extraction concept candidate landing close enough (`authored_protection_threshold`) to an existing *authored* concept is a touch on it, never a rewrite, regardless of persona (FR-310). |
| **Concept engagement gate** | A brand-new *organic* concept (no match, authored or organic) needs the user to have literally named it (whole-word, case-insensitive) in at least one of their own turns before it's inserted — an assistant-only mention is not enough (FR-310). It decides **provenance** only, which is binary; significance is the separate *engagement level* ladder above. |
| **Extraction** | The `ConsolidationExtractor` LLM pass turning a conversation's raw turns into candidate episodes/concepts. Never requests procedures (FR-307). |
| **Upsert** | The shared merge-or-insert pipeline (`MemoryUpserter`): embed → similarity search → two-tier threshold → merge (with LLM *synthesis*) or insert. Used identically by consolidation, enrichment, and bundle install. |
| **Two-tier threshold** | similarity ≥ 0.93 → auto-merge; 0.75–0.93 → LLM *disambiguation* (same item or distinct?); < 0.75 → insert. Values are calibration placeholders, configured in `[memory]`. |
| **Synthesis** | The LLM rewrite that absorbs new evidence into an existing item's description (and steps) on merge — replaces, never appends. |
| **Consolidation gates** | Two independent gates, deliberately not the same question (FR-407). *Episodes*: extracted only when the conversation's persona declares `extracts_episodes` (see **Rehearsal persona**) — nothing else, assessment strategy included, affects it. *Curated content*: for personas with a registered assessment strategy, no procedure authoring and no description/steps/embedding rewrites on match — engagement/category only. Concept *insertion* is gated by neither (see Concept origin/Concept engagement gate above). |
| **Rehearsal persona** | A persona whose sessions are practice rather than life — drills, role-play, exercises — declared by `AssistantPersona.extracts_episodes = false` (FR-407). What the user says there is performance, not autobiography, so no *Episode* is extracted from it. Declared per persona, never inferred: a persona may want *assessment* **and** episodes. |
| **Enrichment** | `EnrichMemory` dispatching `PersonaEnrichmentPort.propose_items`: strategy-proposed new drafts (always `UNSEEN`) fed through the same upsert pipeline. Runs after consolidation. |
| **Assessment** | `PersonaAssessmentPort.assess_items`: offline, post-upsert; converts conversational evidence into `persona_state` updates, persisted byte-for-byte. |

### Persona extension & bundles

| Term | Definition |
|---|---|
| **Strategy ports** | The three optional persona hooks: selection (live), enrichment (offline), assessment (offline). GA registers none. Bound via the *strategy registry* in the composition root. |
| **Persona bundle** | A curated, versioned package: a directory of `bundle.toml` (manifest) + optional `lessons/*.toml`. It must carry a persona definition, content, or both — **persona-only** (a persona that consumes the memory substrate rather than authoring curriculum) and **content-only** (a cognate accelerator) are both valid; only the empty bundle is rejected. Reinstalling refreshes the authored half of the persona definition and preserves the voice-editable half (FR-601/TR-904). **The file format is the port** between Mamemai and external authoring tools (`BUNDLE_FORMAT_VERSION = 1`). |
| **Lesson** | Ordering-only grouping inside a bundle: lesson files in filename-sort order, items in file order. Leaves no persisted structure. |
| **Curriculum order** | The contract that insertion order = teaching order (INV-11): ascending SERIAL id within a type, `created_at` across types. |
| **Bundle install** | `InstallPersonaBundle` (via `mamemai-bundle install <path>`): creates the persona if needed (requires completed onboarding), upserts every item as `UNSEEN` (INV-12), appends a provenance record. Idempotent by merge. |
| **Sibling exclusion** | During one install run, freshly inserted items are excluded from later items' match candidates — a bundle's own deliberately-distinct items must never merge into each other. |
| **`bundle_installs`** | Append-only provenance log (one row per install run); read by nothing; deliberately not a reinstall guard. |

### Language-tutor vocabulary (first strategy persona)

| Term | Definition |
|---|---|
| **Two-teacher cast** | One LLM call role-playing a fixed native-language teacher (`default` anchor) and a target-language teacher; voice selection is automatic per segment from its detected language (see the *Voice cast* entry above), not an LLM-emitted tag. |
| **HVPT** (high-variability phonetic training) | The research basis for rotating the target-teacher voice across sessions via a *rotation pool* while the anchor stays fixed. |
| **SRS state** | The tutor's `persona_state` fields (`state.py`): `last_practiced_at` (day granularity), `half_life_days`, `retrievals` (successful only), `errors`, `avg_response_latency_s`, `user_initiated`, `sessions_practiced`. |
| **Episodic anchoring** | Pairing a due item with an **existing** GA-side Episode via similarity search (threshold-gated) to exploit the self-reference effect. Never seeds new episodes. |
| **Elicitation hint** | The fallback when no episode matches: an invitation (capped per batch) for the user to tell a short personal story — production practice only; nothing said is captured. |
| **Interleaving** | Round-robin ordering of the batch across `category` values — the anti-blocking rule. |
| **Interest cluster** | Several user-initiated concepts sharing a theme (greedy cosine clustering) — the enrichment trigger for proposing the surrounding vocabulary. |
| **Ephemeral generation** | Tutor principle: nothing that merely surfaces in a lesson is tracked; new tutor content comes only from bundles and enrichment proposals. |
