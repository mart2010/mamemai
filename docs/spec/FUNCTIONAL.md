# Functional Specification

*Last verified against code: 2026-07-16*

Externally observable behaviour, by capability. Terms per [GLOSSARY.md](GLOSSARY.md);
ID and wording conventions per [SPEC.md](SPEC.md). Technical contracts (protocol,
formats, algorithms) live in [TECHNICAL.md](TECHNICAL.md).

## FR-0xx — Onboarding & first launch

- **FR-001** On first server start, the single `User` row must be created automatically
  (no manual SQL); `primary_language` starts null.
- **FR-002** On client connect, when `User.primary_language` is null the server must
  send `select_language` listing the installed language codes (FR-705); the client must
  render a terminal selection prompt and reply `language_selected` with the chosen code.
- **FR-003** Completing language selection must persist the primary language and set the
  GeneralAssistant's `response_language` and default voice to the language's default
  Kokoro voice (fallback `af_heart` for unknown codes).
- **FR-004** Until language selection completes, incoming audio must be ignored; the
  client must mute the mic while the selection prompt is open.
- **FR-005** On the user's very first turn, the assistant must deliver a spoken
  introduction (in its own words) covering: placeholder name, fully-local/no-cloud,
  cross-conversation memory, personas, voice-only configuration, and that the
  introduction can be repeated on request.
- **FR-006** Onboarding is a once-only flow: subsequent sessions with a set primary
  language must skip language selection, and must then include memory brief and session
  tail (both skipped during the onboarding session).

## FR-1xx — Live conversation loop

- **FR-101** The user speaks; capture is hands-free: VAD detects speech, streams it, and
  end-of-utterance is inferred from trailing silence (~1.5 s by default) — no push-to-talk.
  Both ends of that inference are the speaker's, not the software's, and are configurable
  per install (TR-201): how much sustained speech starts an utterance, and how long a
  pause may run before it counts as finished. Defaults that suit a brisk talker cut a slow
  one off mid-sentence, and a threshold low enough to catch every hesitant start lets the
  room start conversations by itself.
- **FR-102** Each utterance must be transcribed with automatic language detection (no
  forced language); the detected language is recorded on the user turn.
- **FR-103** An utterance whose transcript is empty/whitespace must produce no response
  (the server still signals `speaking_end`). Text the STT engine hallucinated out of
  silence or room noise must not reach the turn either — it is not something the user
  said, and a reply to it is worse than no reply: it also lands in the session log and
  so feeds consolidation. The engine-side filters that make this hold are TR-956.
- **FR-104** The reply must be synthesised and sent **incrementally** — sentence by
  sentence as the LLM streams — not after the full response completes.
- **FR-105** The assistant must respond in the active persona's `response_language`
  (instructed via system prompt) and speak at the persona's `speaking_rate` — this
  applies uniformly, GeneralAssistant included (its `response_language` is set to
  `User.primary_language` at onboarding, FR-003, and only changes on explicit request,
  INV-14). Cast personas (non-default `voices` keys) receive **no** generic
  response-language instruction at all: a two-teacher cast deliberately speaks two
  languages per reply, and language use is owned by the persona's own system prompt.
- **FR-106** Spoken text must be cleaned for TTS: markdown emphasis/headers/rules and
  emoji stripped; digit sequences spelled out as words for languages where reliable
  (en, fr, es, it, pt), left to the TTS engine otherwise. A written number is spelled as
  the **one** number it is: thousands separators and decimal separators are read in that
  language's own convention (TR-308), never split into separate numbers. Marker-shaped
  bracket spans
  (a single unspaced token, optionally with a `:` payload — `[GUIDE]`,
  `[SPEAKER:default]`, `[Focalizza: …]`) are stripped too, whether or not any code
  consumes them: models invent and translate control tokens despite being told not to,
  and an unrecognised one is otherwise spoken literally. Ordinary bracketed content
  (`[1, 2, 3]`, `[see below]`) is not marker-shaped and must survive.
- **FR-107** No barge-in (INV-4): the client must keep the mic muted from the first
  audio chunk until the server signals `speaking_end`.
- **FR-108** A failure while processing one turn (e.g. TTS error) must not end the
  session or the server: the error is logged, the turn is dropped, `speaking_end` is
  still sent, and the session continues.
- **FR-109** Session-start context: the system prompt must include the memory brief
  (when one exists) and, when the previous session ended within 24 h, the last 10 turns
  of that session as a session tail.
- **FR-110** Long sessions must not overflow the context: every 50 turns, the oldest
  half of the recent-turn window is folded into a rolling summary that replaces those
  turns in the context.
- **FR-111** Every user and assistant turn must be appended to the session log at the
  moment it happens (the only live write, INV-1); assistant turns record the active
  persona and any conversation-boundary marker.
- **FR-112** `[RETIRED 2026-08-02 — LLM-declared topic continuity removed with the
  in-band tag channel (TR-304).` The markers had a parser but no producer: no prompt ever
  asked for them, so they only fired by accident, and in practice a session already
  became one conversation unless a persona switch split it. A persona switch still forces
  a break deterministically (FR-207/TR-315), which remains the only mid-session split.
  **Known gap, deliberately accepted**: two unrelated topics in one uninterrupted stretch
  with one persona now consolidate as a single conversation — one worthiness verdict, one
  summary, extraction over mixed content. Replacing this with offline segmentation at
  consolidation time is deferred until there are real multi-topic sessions to calibrate
  against; see docs/BRIEF_inband_tags.md.`]
- **FR-113** `[RETIRED 2026-07-18 — GA response-language mirroring removed;
  GA.response_language is now a plain fixed setting like every other persona's (FR-105),
  with no detection-driven override or not-installed reminder]`
- **FR-114** A user turn is rendered into the LLM context prefixed with its detected
  language as a `[lang:code]` tag **only for a persona that declares
  `sees_turn_language`** — for such a persona the tag distinguishes an on-target
  utterance from an aside in the user's own language, or (a third language) a likely
  pronunciation stumble. Every other persona sees plain text.
  Narrowed from "all personas" on 2026-08-02 after a live session: STT misdetected an
  English sentence as French, and the model read the resulting `[lang:fr]` as an
  instruction — switching language mid-answer and overriding its own standing
  response-language rule (FR-105). A persona that cannot use the signal must not be
  shown it; the fix is not to emit it and ask the model to ignore it (the FR-207/TR-304
  lesson). Tags are context-rendering
  only: stored turns and session logs stay clean (the log's `language` field carries the
  code, TR-402), and a `[lang:]` tag mimicked by the model in its response is stripped
  before TTS, never spoken.

## FR-2xx — Personas

- **FR-201** The GeneralAssistant must always exist (seeded, fixed id), is the persona
  every session starts on, and must be protected: it cannot be removed or deactivated.
- **FR-202** `[RETIRED 2026-07-18 — replaced by directive-based switching, FR-207. The
  `[PERSONA:name]` LLM-emitted tag scheme required listing every other persona's name
  in the system prompt to get the model to notice it reliably, which turned out to
  also be exactly the kind of salience that caused language drift toward that other
  persona even when nobody asked to switch — see the language-drift discussion FR-207
  replaces this with.]`
- **FR-203** `[RETIRED 2026-07-18 — persona discovery moved to onboarding/FAQ; GA's
  system prompt no longer names another persona under any circumstance, the actual
  fix for the drift FR-202 caused.]`
- **FR-204** Persona management use cases exist for create, edit, list, remove,
  deactivate, reactivate — with the rules: creation only while GA is active; system
  personas cannot be removed/deactivated; removal cascades to the persona's concepts,
  procedures, **and** its own Directive rows (INV-9 — `target_persona_id ON DELETE
  CASCADE`, no explicit cleanup code needed); deactivation preserves memory for later
  reactivation. Create additionally syncs a Directive for the new persona via
  `PersonaDirectiveSync` (FR-207). **⚠ Gap:** only *edit* (onboarding voice/language)
  and *switch* (FR-207) are wired to live triggers; create/remove/deactivate/reactivate
  have no voice or CLI entry point yet.
- **FR-205** A persona's voice identity is its `voices` map (mandatory `default`
  anchor, INV-7; other keys are IETF language codes). Each synthesized segment's own
  detected dominant language must switch the synthesis voice to that language's
  registered voice, at whole-segment granularity — never mid-sentence, so a segment
  quoting a foreign word in an otherwise native-language sentence stays in the
  native voice; an undetected or unregistered language falls back to the default
  anchor.
- **FR-206** A non-default `voices` map key defined as a `|`-separated pool must
  resolve to one voice per session — stable within the session, rotating across
  sessions (HVPT) — with no persisted state.
- **FR-207** A **Directive** is a user utterance that changes mamemai's own operating
  state rather than being answered in conversation. Persona switching is the first
  (and, currently, only) directive type; a Directive is a first-class domain object
  (`domain.model.Directive`, its own `directives` table — not a `Concept`):
  `target_persona_id`, `action` (e.g. `"switch_persona"`), `language`, `trigger` (e.g.
  `{"keywords": ["switch", "tutor"], "max_words": 6}`), `description`. Matched by a
  deterministic keyword/word-count rule against the turn's own utterance
  (`services/directives.matches_trigger`) — no embedding call, not by anything the LLM
  decides or emits. A trigger fires when every one of its keywords appears
  (case-insensitive substring match) in an utterance no longer than `max_words` words.
  A clearing match executes deterministically **before** that turn's system prompt is
  composed: `active_persona` changes first, so the reply is generated by the new
  persona from the first token, and the system prompt never has to name the other
  persona to make the switch happen (the actual fix for the language drift the retired
  `[PERSONA:]` tag scheme, FR-202, used to cause). An already-active target is a
  silent no-op (carried over from FR-202). Trigger keywords are built from a fixed,
  documented per-language action-verb table (the switch verb) plus the persona's own
  name, split into words — one Directive row per language the persona is used in
  (`persona.languages`, TR-904's session language pair, which already folds in the
  user's primary language; falls back to the persona's own `response_language` if that
  list is empty). `PersonaDirectiveSync` creates a new persona's Directive (FR-204) and
  bootstraps the "return to the GeneralAssistant" directive (one row per installed
  language, FR-705) idempotently on every server startup; removal needs no explicit
  sync call — `target_persona_id ON DELETE CASCADE` (INV-9) handles it. A switch
  always marks a conversation boundary (TR-315): consolidation attributes a
  whole unbroken conversation to a single persona (FR-303), so without a forced
  boundary a mid-session switch would silently let the new persona's turns be extracted
  and scored under the old persona's identity. A target that has been **deactivated**
  (`AssistantPersona.is_active`) is unreachable: deactivation doesn't clean up the
  persona's Directive row (only removal cascades, INV-9), so the trigger still
  matches, but the switch itself is blocked, deterministically, same as the match
  decision — never a silent reactivation via conversation. The blocked attempt still
  gets a reply: the target's name is injected as a turn-scoped context message (same
  mechanism as recalled memories/the selected item, TR-315) so the active persona can
  explain the persona is unavailable in its own words — reactive only (the user
  already named it themselves), not the standing persona-listing block FR-202/FR-203
  retired.

## FR-3xx — Memory & recall

- **FR-301** After conversations are consolidated (offline), the assistant must know
  their content in later sessions through three memory types: episodes (events),
  concepts (knowledge), procedures (know-how).
- **FR-302** Every substantive user turn must be checked against long-term memory (not
  gated on an explicit phrase like "remember when…" — FR-309 governs which turns count
  as substantive); the matching memories (top 5 by similarity; concepts/procedures
  scoped to the active persona, episodes global) must be injected into that turn's
  context.
- **FR-303** Concept/procedure knowledge must be persona-scoped end-to-end **for
  writing**: the same name under different personas is different knowledge, with
  independent engagement, and every write path (extraction, upsert, enrichment, bundle
  install) stays inside the owning persona's scope. INV-9's cascade is unchanged.
  **Reading is separable, and declared.** A persona may set
  `AssistantPersona.reads_across_personas` (default false — every persona before
  2026-08-02) to read concepts and procedures owned by *any* persona. This exists because
  a persona that works *from* the user's existing memory rather than authoring its own
  cannot otherwise reach it: scoped to itself it would only ever see what it was told
  during its own sessions, which is circular. Read-only by construction — the flag widens
  a listing scope and nothing else, so such a persona can never rewrite or delete what it
  sees. Episodes need no such flag: they carry no persona scope at all (INV-10), which is
  what already let the language tutor anchor lessons to the user's life.
- **FR-304** Repeated encounters must enrich, not duplicate: near-identical extracted
  items merge into the existing item (two-tier threshold), synthesising the description
  rather than appending; clearly-new items insert.
- **FR-305** Engagement must only ratchet upward on merge (max of existing and new).
- **FR-306** Episode summaries must always be written in the user's primary language,
  whatever language the conversation happened in (INV-10).
- **FR-307** Trivial exchanges must not become episodes or concepts: a cheap
  deterministic floor (minimum user turns and minimum user words, counting only the
  user's own turns — assistant chatter must never inflate it) gates whether worthiness
  evaluation and extraction are even attempted at all; below it, both are skipped
  outright, purely for cost control. Above the floor, episode extraction is separately
  gated by a per-conversation worthiness judgment (LLM) whose criteria explicitly
  exclude discussion about the assistant's own operation (bugs, testing, capability
  questions) and require a genuine, identifiable time or place — a substantial
  conversation is not automatically an episode-worthy one. Procedures are never
  extracted from conversation, for any persona: how-to knowledge belongs to authoring
  expertise (bundles) or persona enrichment, never live discussion.
  **The worthiness prompt states its YES criteria as their own enumerated list, and
  restricts NO to "nothing but" the excluded categories** (rebalanced 2026-08-02). This is
  a measured property, not a style preference. The previous wording gave YES one clause and
  NO five vivid ones; against a labelled probe set llama3.1:8b scored 23/45, getting every
  *negative* case right (20/20) and almost every positive one wrong — including a plain
  English "I went to Norway last weekend and finally saw the northern lights, I work as an
  engineer", which is both a personal fact and a dated event. Live, every French
  conversation in the database had been judged unworthy (0/4, against 9/18 for English).
  The rebalance alone scores 40/45; a language-neutrality clause and few-shot examples were
  each measured on top of it and changed nothing, so neither is in the prompt.
  The reply is asked for **in English** and parsed by first word across the supported
  languages' yes/no vocabulary: a French transcript drew a bare "OUI", and the previous
  substring parse (`"yes" in reply`) read that as NO — a gate misreading its own answer is
  indistinguishable from a real verdict. An unreadable answer is never a YES.
  **Extraction is windowed too, and for the same measured reason** — but over *all* windows,
  not just the ends, since extraction must cover every turn rather than sample the
  conversation. The same 72-turn conversation yielded 4 concepts from its first 12 turns, 4
  from its first 24, and **zero** from its first 48 or its full 72, with valid JSON
  throughout, so a 37-minute session extracted nothing at all.
  **That cliff was context truncation, not model capacity** (established 2026-08-03 —
  see TR-951/TR-952): every measurement above was taken at Ollama's default 4096-token
  window, which silently drops whatever doesn't fit, and the same transcripts at
  `num_ctx=16384` extract normally.
  **Windowing survives that correction on its own merits**, re-measured the same day at an
  explicit `num_ctx`: 4 real conversations (32–72 turns), window sizes 8/16/24/whole, 3
  repeats, scored on concepts the *user themselves named* (the FR-310 provenance rule —
  an ungrounded concept is discarded downstream anyway) rather than on volume. Every
  transcript fit the context with room to spare, and a single whole-conversation call
  still returned a third of the concepts, half the episodes and 2.5× fewer distinct
  subjects than the smallest window: a real length effect on extraction, distinct from the
  truncation that was masking it. Precision is **flat across sizes** (~25% grounded
  everywhere), so window size is a recall/volume knob and buys no accuracy. The window is
  16 turns — ~30% more grounded concepts than the 24 it replaced, at half the
  near-duplicate fragmentation of an 8-turn window, deliberately short of the
  recall-maximising value to keep the false-positive posture the 2026-07-18 incident
  demanded.
  Per-window results are merged, and **concept de-duplication by
  name in that merge is required for correctness**: the same concept proposed by two windows
  would be upserted twice, and the second upsert would match the row the first just wrote
  and promote its engagement level — manufacturing precisely the "repetition inside one
  sitting" FR-310 rules out as evidence. Episodes are de-duplicated only on an exact
  summary/time match; differently-worded duplicates are left to `upsert_episode`'s
  embedding merge, which exists for that. A window whose answer will not parse is skipped
  rather than failing the conversation — before windowing there was a single answer, so an
  unparseable one meant an empty result by necessity.
  **An unusable *item* costs only itself**, one level finer than that: well-formed JSON is
  not well-formed content, and an extracted item whose required text is missing, null,
  blank or not a string (a concept with no name or no description, an episode with no
  summary) is dropped where the answer becomes domain objects, leaving the good items
  beside it in the same window intact. Measured at ~2% of extraction runs, and it was
  fatal — a single null `name` raised out of the merge and discarded every window's
  results for the conversation, defeating the per-window skip above via a field the JSON
  decoder was perfectly happy with.
  **A window whose prompt exceeds the context budget is skipped too, and says so**
  (TR-951): a truncated extraction call returns valid JSON with empty arrays every time,
  which is indistinguishable downstream from "nothing here", so asking it buys a lie at
  full latency. Worthiness and the tutor's practice judge make the opposite choice —
  they warn and ask anyway, because a verdict over the surviving part of a transcript is
  degraded rather than fabricated, and skipping there would discard a long conversation
  outright, which is the failure this exists to stop.
  **A long conversation is judged in windows, and is worthy if any window is.** This model's
  judgment degrades with transcript *length* rather than content: one real 72-turn
  conversation scored 0/5 whole, 5/5 on its own first 24 turns, 4/5 on its last 24, and 5/5
  even on the 18 turns of pure STT-repair dialogue that were the least substantive material
  in it. Judged whole, the rule was exactly backwards — the longer and richer a session, the
  more certain it was to be discarded. Windows are taken at both ends; the middle is not
  sampled, which is a known gap accepted against the cost of a third call to a local model
  that serialises. Same correction as extraction's above: those numbers were measured at
  the default 4096-token context, so what they show is the transcript overflowing the
  window, not the judgment weakening with length — the sampling stays pending
  re-measurement at a correct `num_ctx`, and may prove unnecessary.
- **FR-310** Concept creation from live conversation is gated independently of episode
  worthiness, by origin and engagement, not by a whole-conversation verdict. Every
  Concept carries an immutable `origin` ("authored" — bundle install, persona
  enrichment — vs "organic" — live extraction). A live-extraction candidate close
  enough to an existing *authored* concept is recognised as a touch on it (engagement
  bump only, content immutable) regardless of which persona the conversation belongs
  to; a candidate distinct from all authored content is free to merge into or insert as
  a new *organic* concept using the existing merge/disambiguate thresholds (FR-304) —
  but a genuinely new organic insert additionally requires **provenance**: the user must
  have literally named it in at least one of their own turns, not merely been present
  for an assistant mention — topical proximity to what the assistant introduced is not
  engagement. Provenance is a binary question, so the bar is one mention (lowered from
  two on 2026-08-02: the higher bar was doubling as a significance proxy, which
  double-counted filters FR-307 already applies and silently lost concepts whose
  mentions differed only by inflection).
  **Significance is a separate axis on a different timescale**: an *organic* concept the
  user names again in a later conversation is promoted one engagement level, saturating
  at `INTEGRATED`. Recurrence across conversations is evidence; repetition within one is
  not. *Authored* concepts are excluded from that ladder — they still take the single
  `UNSEEN → MENTIONED` bump when the user first genuinely meets one, but never climb
  further, because a persona whose activity elicits its own material (it prompts for an
  item, the user repeats it back) would otherwise manufacture the very signal FR-506
  ranks on. Mastery of authored content belongs to the owning persona's `persona_state`,
  written by its own assessment strategy. Neither rule branches on persona — only on
  `origin`, a first-class generic field (INV-6).
- **FR-308** A fresh memory brief must be generated after each offline run that
  consolidated at least one conversation, and be in place for the next session start.
  It is **summarised from stored long-term memory** — recent episodes (INV-10: unscoped,
  the user's own life) and *organic* concepts, across every persona. Authored concepts are
  excluded: a bundle's curriculum is the owning persona's material, not a fact about the
  user, and the brief reaches every session's system prompt including personas that never
  declared `reads_across_personas` (FR-303). Organic concepts are things the user
  themselves raised (FR-310's provenance rule), so they belong in a profile in a way
  installed lesson content does not — the rule branches on `origin`, never on persona
  (INV-6). Volume is bounded so the prompt stays bounded as memory grows.
  **With no memory to summarise, no brief is written at all.** Stated as a requirement
  because the failure is silent and destructive: asked with an empty context, the model
  reports that it knows nothing about the user, and saving that replaces a good brief with
  a worse one. Until 2026-08-02 `GenerateMemoryBrief` had no memory repository whatsoever
  and always generated from nothing — live from the first commit, and invisible to its only
  test, which asserted on a fake LLM's canned answer instead of on the prompt it received.
  Found by inspecting a real brief that read "Based on our conversation (which is
  non-existent), I don't have any information about the user yet" while 63 concepts and 9
  episodes sat unread in the database.
- **FR-309** Whether a turn triggers a recall search at all is a persona-scoped policy
  (a `RecallGate`), not a fixed rule — though as of 2026-07-30 every persona, the
  language tutor included, shares the same one (`DefaultRecallGate`; the tutor's own
  override was removed, see below): recall answers **questions**, not statements — a
  turn must read as an actual question (a literal "?" in the STT transcript) and clear
  a word-count floor (raised 2026-07-30 specifically to also exclude short reflexive
  questions like "How are you?"/"What's up?", not just very short ones) to be worth
  checking memory for at all; a plain statement, however long or substantive, never
  triggers a search — this must be documented for the user (README/FAQ): recalling
  something requires asking about it explicitly, not just mentioning it. The language
  tutor no longer overrides this for its own single-word vocabulary answers (e.g. a
  bare "agua" replying to "which word would you like to practice?"): the item
  currently being tested is already injected via the tutor's own `PersonaSelectionPort`
  (a separate mechanism, spaced-repetition scheduling off `persona_state`, FR-501/506),
  independent of recall — a one-word answer doesn't need its own additional generic
  memory search on top of that. `RecallGate` remains a persona-scoped extension point
  (a future persona-type may still need a distinct policy), just with no active
  overrides today. Independently, a turn whose embedding is nearly identical to *any*
  prior search this session (for the active persona) — not only the most recent one —
  skips a fresh search and reuses that search's results: correct, not just an
  optimisation, because nothing new can enter long-term memory mid-session (INV-1), so
  a repeat of any earlier query would deterministically return the same thing again.
  Whether the gate actually decided a turn was worth checking memory for is itself
  carried into that turn's context composition (TR-303) — so the reply can be
  transparent about a genuine recall miss (say so) versus a turn recall never ran on at
  all (say nothing), rather than reading the same either way.
  **A returned memory must also be close enough to count** (`RecallGate.is_relevant`,
  third decision of the gate, added 2026-08-03). `MemoryRepository.search` is nearest-N
  with no distance floor of its own (TR-509), so against a non-empty database it always
  returns rows however unrelated they are — which made "recall found nothing" not a
  representable state and the honesty branch above unreachable: every recall-eligible
  turn was told it had found relevant memories. Observed live: asked whether zodiacal
  light had been discussed before — absent from the database, from the session, and from
  the session tail — the reply opened "Oui, nous en avons déjà parlé". Rows below the
  floor are dropped before composition, so honesty stops depending on the model
  overriding a contrary instruction: the same deterministic-filter-over-plea posture as
  FR-114 and `matches_trigger`. The floor is persona-scoped like the rest of the gate,
  and calibrated rather than guessed — see TR-314 for the measurement and its limits.

## FR-4xx — Offline processing & durability

- **FR-401** All heavy processing (DB writes, extraction, embedding for storage,
  upserts, brief generation) must happen offline, after disconnect (INV-1).
- **FR-402** The offline pipeline must start after the user has been disconnected for
  `User.idle_consolidation_minutes` (default 5); a reconnect within the window must
  cancel it (it will run after the next disconnect).
- **FR-403** The pipeline order is: replay session logs → consolidate conversations →
  enrichment proposals → regenerate memory brief.
- **FR-404** Crash safety: unprocessed session logs must be replayed into the DB on
  every client connect, so a crashed or killed server loses no conversation. Replay
  must be idempotent (already-persisted sessions skipped).
  **Consolidation must also catch up on connect** (2026-08-02) when the quiet gap since
  the previous session already exceeds `idle_consolidation_minutes` — the same window the
  idle timer waits for. Without it consolidation starves: the timer starts on disconnect
  and any new connection cancels it, so sessions a few minutes apart cancel every timer
  before it fires while replay keeps filling `conversations`. Observed in real use — 8
  sessions in 50 minutes left 7 conversations unconsolidated and the memory brief 13 days
  stale, so nothing said that afternoon was recallable. Catch-up runs in the background so
  the session starts immediately; the first turns may be slower while it finishes, which
  is the accepted cost of not silently losing long-term memory.
- **FR-405** A consolidation failure must lose nothing: each conversation is processed
  in its own transaction; a failed conversation remains unconsolidated and is fully
  reprocessed next run.
- **FR-406** Session logs are permanent (INV-5): no rotation, no cleanup.
- **FR-407** Whether a persona's conversation is a record of real events is that
  persona's own declaration — `AssistantPersona.extracts_episodes`, defaulting to true,
  set at creation (a bundle's `[persona] extracts_episodes`) and never inferred by
  generic code from any other property. A persona whose sessions are rehearsal (drills,
  role-play, exercises) declares it false, and consolidation must then never extract
  episodes from its conversations: what the user says there is performance, not
  autobiography. Having a registered assessment strategy does **not** imply the
  declaration either way — a persona may want assessment *and* episodes. Independently
  of the declaration, consolidation must never author new procedures from any
  conversation (FR-307) and, for personas with an assessment strategy, must never
  rewrite curated (authored) wording: engagement bumps and category gap-fills against
  existing content only. Concept creation is not persona-gated at all: it follows the
  same origin/engagement rules for every persona (FR-310), so a user going genuinely
  off-curriculum mid-lesson can still produce a new organic concept — curated content
  stays immutable either way, via FR-310's authored-origin protection, not via a
  blanket ban on strategy personas ever authoring anything new.

## FR-5xx — Language tutor (first strategy persona)

- **FR-501** On the tutor's first active turn of a session, a selection batch must be
  fetched (default mix: ~50% review items ranked least-known/stalest first, rest new
  items in curriculum order, interleaved by category); exactly one item is injected per
  turn until the batch is exhausted.
- **FR-502** `[SUSPENDED 2026-08-02 — the requirement stands, its mechanism does not.`
  A user session wish should steer selection, and the strategy side that interprets one
  (mode review/new/mixed, a category, or a free topic ranked by similarity) is intact and
  still reachable through `select_items(focus=…)`. What was removed is the only thing that
  ever supplied it: the in-band `[FOCUS: …]` marker (TR-304). **No session wish reaches
  selection today** — every persona gets its default batch. The intended replacement is
  detecting the wish on the *user's* turn, deliberately deferred so it can be designed
  against a second persona and tested live rather than inferred from the tutor alone;
  see docs/BRIEF_inband_tags.md.`]
- **FR-503** Where a due item relates to a stored episode (similarity above the anchor
  threshold), the injection must carry that personal anchor; otherwise at most 2 items
  per batch may carry an elicitation hint inviting a short personal story.
- **FR-504** Nothing said during a lesson may enter long-term memory as new curated
  content: no new episodes or procedures from tutor conversations (FR-407); elicited
  stories live only in that turn's context. A concept distinct from curriculum content
  and clearing the engagement gate (FR-310) is the one exception — genuine
  off-curriculum discussion, not lesson drills, which never carry that kind of
  real-world content to begin with.
- **FR-505** After each lesson is consolidated, the tutor must update per-item SRS
  state from evidence: successful retrievals (not exposures) grow the half-life, errors
  shrink it, practice is day-granular, user-initiated items are flagged sticky and get
  a longer initial half-life, scaled by the configured pair difficulty.
- **FR-506** Selection must rank reviews by engagement level until the persona's
  settings switch it to retention ranking (derived decay estimate) — the
  instrument-now-calibrate-later posture.
- **FR-507** When several user-initiated concepts cluster around a theme, offline
  enrichment must propose the surrounding vocabulary cluster (one cluster per run,
  proposals installed as `UNSEEN` through the normal upsert dedup).
- **FR-508** Tutor lessons must be staged as a two-teacher cast (fixed native anchor +
  rotating target-language teacher) using FR-205/FR-206 mechanics.

## FR-6xx — Persona bundles (power-user extension)

- **FR-601** `mamemai-bundle install <path>` must install a bundle directory: creating
  the persona from its `[persona]` definition when the `persona_key` is new, or, when the
  persona already exists, adding any content **and refreshing the authored half of its
  definition** — prompt, settings, episode declaration, target languages — while leaving
  everything the user can change by voice (name, voices, speaking rate, response
  language) exactly as they have it, with a notice saying so (TR-904). A bundle carrying
  only a `[persona]` table and no content is a valid bundle; before 2026-08-02 the format
  required at least one lesson file, which made a persona that authors no curriculum
  impossible to ship, and the ignore-on-exists rule would additionally have made such a
  persona impossible to ever update.
- **FR-602** Every bundle item must install at engagement `unseen` (INV-12); merging
  into an already-engaged item keeps the earned level (FR-305).
- **FR-603** Reinstalling the same bundle must be idempotent: unchanged items merge
  into themselves (near-free via the exact-duplicate short-circuit), nothing
  duplicates, user progress is preserved.
- **FR-604** Within one install run, a bundle's own items must never merge into each
  other (sibling exclusion) — the author meant them as distinct; matching against
  pre-existing content (earlier installs, live extraction) is intended behaviour.
- **FR-605** Installing a persona-creating bundle must fail with a clear error until
  onboarding is complete (the native-language anchor voice and input languages derive
  from `User.primary_language`).
- **FR-606** Curriculum order must survive install: lessons in filename order, items in
  file order, persisted as insertion order (INV-11) and honoured by tutor selection of
  new items.
- **FR-607** Each install run must append a provenance record (bundle identity,
  counts, manifest verbatim) that survives persona deletion.
- **FR-608** A malformed bundle must be rejected as a whole with a format error naming
  the offence (missing manifest keys, unsupported `format_version`, unknown `[persona]`
  keys, malformed items); nothing partial is installed from a bundle that fails parsing.
- **FR-609** Installing a persona-creating bundle must fail with a clear error — naming
  the missing language and pointing at `mamemai-setup` — when any of the bundle's target
  languages is not an installed language (FR-705): the session language pair is only
  speakable when the target's TTS voices actually exist on the machine, and that must
  surface at install time, not as a missing-voice failure mid-lesson.

## FR-7xx — Configuration & deployment

- **FR-701** Voice is the only configuration surface for the GeneralAssistant's own
  settings; every voice-configurable setting is a DB-backed attribute of `User` or
  `AssistantPersona` — `mamemai.toml` holds only bootstrap-before-DB settings. Anything
  requiring install/download/restart is wizard territory (`mamemai-setup`), not voice.
- **FR-708** The user must be able to change the language the assistant answers in, by
  voice, and have it persist. A `set_response_language` *Directive* is generated for every
  *installed language* (FR-705), in exactly one phrasing: **the target language's own**
  ("speak english", "parle français", "日本語で話して"). A match changes the
  **active persona's** `response_language` and its default voice, written to the persona
  row so it holds across sessions until another directive changes it.
  The one-phrasing rule is load-bearing, not a simplification (2026-08-02). The directive
  utterance is also an ordinary user turn and lands at the bottom of the LLM's context, so
  a request phrased in the language being left behind pits a fresh example of that language
  against the switch instruction in the system prompt — and llama3.1:8b was observed
  answering the old language for a turn regardless. Asked in the target language, evidence
  and instruction point the same way. A second phrasing in the user's current language was
  generated until 2026-08-02 so that asking for a language you cannot yet produce would not
  require producing it; that cost is now accepted — an unpronounceable language is reached
  from the wizard, not by voice. Rows written by the old rule are **pruned** on startup, not
  merely left ungenerated: a directive row outlives the configuration that produced it.
  Scope is deliberate: `User.primary_language` is **not** touched. INV-10 pins episode
  summaries to the primary language precisely so months of conversing in another language
  never rewrite the user's life story into it, and INV-14 makes a primary-language change
  its own explicit act. "Answer me in French" is about what you hear, not a relabelling of
  your past — and it leaves a persona whose language is intrinsic to its purpose (a tutor
  answering in its target language) unaffected unless asked directly.
  Availability is re-checked at execution even though these directives exist only for
  installed languages: a directive row outlives the config that produced it, and switching
  to a language with no voice on this machine would produce silence rather than speech. An
  unavailable request is refused in the current language, pointing at the setup wizard.
- **FR-702** The system must assume exactly one user: no authentication, no concurrent
  sessions, no row-level security (INV-3).
- **FR-703** The client must support both deployments from one config file: `ssh_host`
  present → auto-establish (and auto-restart) an SSH tunnel before connecting;
  absent → connect to the local server directly.
- **FR-704** Persistent language settings — `User.primary_language` and any persona's
  `response_language` — must only ever change on explicit user request (INV-14), never
  inferred from detected speech language.
- **FR-705** The install wizard must record the selected languages in `mamemai.toml`
  (`[languages].installed`) — the **installed languages**: the wizard-selected subset
  of supported languages whose TTS voices actually exist on the machine. Onboarding
  language selection (FR-002) is bounded by this set; adding a language means
  re-running `mamemai-setup`. A config without the key (written before it existed) treats
  every supported language as installed.
- **FR-706** Re-running the install wizard must start from the recorded installation
  state, not from nothing: the existing `mamemai.toml` is parsed and its current settings
  shown up front; already-installed languages come pre-checked in the language
  selection (so adding one never silently drops the rest of `[languages].installed`);
  the current LLM and Whisper choices are the highlighted defaults (current LLM marked
  in its label); the recorded database connection is offered as a keep-current default
  (still verified). Topology is locked when inferable (`ssh_host` ⇒ split-host client),
  asked again otherwise; a malformed config degrades to a fresh run.
- **FR-707** The install wizard must let the user choose how live conversation is
  powered: local via Ollama (default), or a remote OpenAI-compatible HTTP endpoint
  (any provider — OpenRouter, OpenAI, a self-hosted server, ...), for installs without a
  local GPU capable of fast live inference. Minimal remote configuration: a base URL and
  a model name are required; an API key is optional (some self-hosted endpoints don't
  require one). This choice affects only the live conversational path (the main reply
  and per-turn recall-intent detection, TR-955) — the offline memory pipeline
  (consolidation, memory brief generation, and Ollama-backed persona-strategy helpers
  such as the language tutor's focus interpreter) always runs on a local Ollama model
  regardless, so a GPU-less install accepts it running slower, not remotely.
