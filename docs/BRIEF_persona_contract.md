# Design Note — The Persona Contract

**Status: proposed, not scheduled.** No phase number assigned deliberately — the
sequencing section argues this work should *follow* a second non-tutor persona, not
precede it. Nothing here is settled; this is a design record of a problem, a proposed
shape, and the case against it.

Origin: 2026-07-31 architecture review, prompted by the concern that language-tutor
logic is bleeding into the GeneralAssistant's concern, and that this will worsen as
more specialised personas arrive with less in common.

## The concern, restated precisely

The review's opening framing was that a third-party persona author must "shape his
persona to be compatible with GA's domain and memory". The code does not quite say
that — `infrastructure/language_tutor/` has **zero** dependency on GA or on generic
service code, the four extension ports (`services/ports.py:165-257`) are genuinely
persona-agnostic, and `persona_state`/`settings` opacity (INV-6) holds. The ~15
occurrences of "tutor" in `services/` and `domain/` are comments and examples only.

The real defect is narrower and worse: **generic code accumulates the union of every
persona's needs, because the extension seam is pull-shaped.**

## Evidence

### 1. Four registries answering one question

`server.py:213-295` defines `SELECTION_STRATEGY_FACTORIES`,
`RECALL_GATE_FACTORIES`, `ASSESSMENT_STRATEGY_FACTORIES`, and
`ENRICHMENT_STRATEGY_FACTORIES` — all keyed by the same `AssistantPersona.strategy`
string, all resolved by the same `_build_strategies` (`server.py:257-279`), wrapped in
four functions (`server.py:282-295`) that differ only in which dict they pass.

Four lookups asking one question: *what is this persona?* The axis of variation is
**persona**; the decomposition is sliced along **capability**.

### 2. A vestigial port

`RECALL_GATE_FACTORIES` is empty (`server.py:232`). `LanguageTutorRecallGate` was
removed 2026-07-30 when the tutor stopped needing it; the port survived with no
implementor. That is the signature of *need-driven hole-punching* — each port exists
because at one moment the tutor needed to reach into generic code at that exact spot —
rather than a decomposition of what a persona is.

### 3. The ports are pull interfaces with fixed signatures

`select_items(persona_id, focus, limit)`, `assess_items(persona_id, conversation,
touched_items)`, `should_embed(text)`, `should_search(max_similarity)`. Generic code
decides *when* to call and *exactly what to hand over*.

A persona needing something absent from a signature — session tail, user's primary
language, time since last session, the current turn's detected language — cannot
obtain it. The only remedies are widening a signature or punching a fifth hole. **Every
new persona capability becomes an edit to `session.py`.** This is the mechanism behind
the original concern, and it scales badly in exactly the way predicted.

### 4. Tutor-shaped policy currently living in generic code

Line references refreshed 2026-08-01.

| Location | What it is |
|---|---|
| ~~`_next_selected_item`~~ | ~~Lazy fetch, one item per turn, exhausted batch not re-queried — pure tutor **pacing policy** in generic code~~ — **partly resolved 2026-08-02**: refetch and activation-turn are now persona-declared (`SelectionPacing`). Lazy fetch, one-item-per-turn and `WorkingMemory.selection_batches` remain generic |
| ~~`session.py:688`~~ | ~~a persona cannot bring content on the turn that activates it~~ — **resolved 2026-08-02** via `SelectionPacing.include_activation_turn`; a blocked switch still never counts as an activation |
| `session.py:91` `recall_history` | Per-persona search cache in the generic working-memory struct |
| `session.py:333` `_render_turn_content` | `[lang:code]` prefix on every user turn, for every persona; docstring justifies it purely in tutor terms. GA pays prompt pollution for it |
| `session.py:178` `_PREFIX_SCAN_WINDOW_CHARS = 200` | Every persona, GA included, buffers up to 200 chars before first audio, scanning for a `[FOCUS:]` tag only the tutor emits — a latency tax on a latency-critical path |
| `session.py:383-385` | Response-language instruction suppressed when `voices` has non-default keys — generic code inferring "is a two-teacher cast" from a data shape. Not an INV-6 violation by the letter (it isn't `settings`), same leak through a side channel |
| `session.py:621` | `matched_directive.action == "switch_persona"` — `Directive.action` is tested once, not routed; an unknown action falls through silently. Blocks persona-scoped commands |
| `domain/model.py:81-89` | `DEFAULT_VOICE_ROLE` docstring encodes HVPT rotation-pool semantics into the domain layer |
| ~~`memory.py:121`~~ | ~~`extract_episodes = strategy is None`~~ — **retired 2026-08-02**, see below |

### 6. The leak also runs the other way

Added 2026-08-01, from the persona #2 design session. The hunt so far has been for persona
logic escaping into generic code. The reverse exists too: a **generic capability trapped
inside a persona**.

Spaced-retrieval decay — `_retention()` (`infrastructure/language_tutor/selection.py:69`),
`STATE_HALF_LIFE_DAYS` (`language_tutor/state.py:10`), growth/shrink
(`language_tutor/assessment.py:159-197`) — is owned entirely by the tutor. Any
spaced-retrieval persona needs it, and today would have to reach into the tutor's package
to get it. The contract has to address both directions, not just one.

### 5. GA plays three roles at once

It is the default persona; it is the runtime shell (`GENERAL_ASSISTANT_ID` hardcoded at
`domain/model.py:79`, sessions always start on it per FR-201, `ONBOARDING_SCRIPT` lives
in `session.py:97`); and it is the implicit "everything not otherwise specified"
fallback. Consequence: anything unspecified becomes GA's behaviour by default, and any
persona need that doesn't fit a port gets bolted onto the shared path — which *is* GA's
path. There is no way to leak *into* GA without a GA-shaped code path to leak into.

## Proposed shape

Invert the seam. Generic code hands over a context and receives a contribution.

```python
class Persona(Protocol):
    # live — INV-1 enforced by the type: read-only memory view, no repositories
    def prepare_turn(self, ctx: TurnContext) -> TurnContribution: ...
    def observe_reply(self, ctx: TurnContext, reply: str) -> None: ...
    # offline — repositories available
    def consolidate(self, ctx: ConsolidationContext) -> ConsolidationOutcome: ...
```

**The field lists below are illustrative, not settled** — deriving them from the tutor
alone is exactly the failure mode this note warns about (see Counter-case §3).

- `TurnContext` — utterance, detected language, recent turns, session tail, turn index,
  and a **read-only** memory view.
- `TurnContribution` — ordered prompt injections, system-prompt fragments, and a
  segment policy (how to split a reply, what to strip, how to select a voice).
- `ConsolidationContext` / `ConsolidationOutcome` — the conversation plus repositories;
  item-state updates and proposed drafts out.

**Two further elements, added 2026-08-01 from persona #2.** Both are requirements the
four pull-shaped ports structurally cannot express, and neither was visible from the
tutor alone:

- **Durable emission.** A persona must be able to produce something durable and generic
  addressed to *another persona* — persona #2's divergence flag, raised when the user's
  answer contradicts the store, for GA to help resolve later. Today nothing does this:
  directives route the *user* between personas, never content. It fits `consolidate`'s
  return, and the fact that the persona must both *speak* the observation live and
  *persist* it offline is direct evidence for keeping `observe_reply` and `consolidate`
  on one object.
- **Declared commands.** A persona must be able to declare its own voice commands
  (`set_exercise_type`). Generic code executes a known closed set (`switch_persona`, and
  the planned FR-701 voice-configurable settings) and **delivers anything else to the
  active persona without interpreting it** — INV-6 is exactly that boundary. Today
  `Directive.action` is tested once at `session.py:621` and rows are generated in code
  (`services/directives.py:105`), so a bundle cannot declare one.

**Evidence for the state slot, found 2026-08-02.** Moving pacing out of
`_next_selected_item` was supposed to also "shrink `WorkingMemory`" by letting the batch
live inside the persona. It couldn't: the batch is *session* state, and strategies are
constructed once at startup and shared across sessions, so a strategy holding its own
batch would leak it into the next session. `WorkingMemory.selection_batches` therefore
stayed exactly where it was, and only the *policy* moved out as a declaration.

That is a concrete instance of the "no home for cross-session persona state" row in the
mapping table below, arrived at from a different direction: it is not only that personas
*want* durable state, it is that generic code cannot hand them session-scoped state today
without inventing a place to put it. Any contract that lets a persona own its own pacing
has to solve this first.

Two properties matter more than the exact fields:

**Generic code owns ordering and budget; personas own content and policy.**
`_compose_working_context` (`session.py:331-421`) stops being where every persona's
needs accumulate — today six tiers, several tutor-shaped — and becomes a renderer of an
ordered list it does not interpret.

**The interface enforces INV-1 structurally.** Live hooks receive a read-only memory
view and return data; they *cannot* write. Offline hooks receive repositories. Today
INV-1 rests on convention and review.

### Mapping from today

| Today | Under the contract |
|---|---|
| `PersonaSelectionPort.select_items` + `_next_selected_item` batching | `prepare_turn` returns injections; pacing moves inside the persona; `selection_batches` leaves `WorkingMemory` |
| `RecallGate.should_embed` / `should_search` + `recall_history` | Persona queries the read-only memory view if and when it chooses; the cache becomes persona-owned session state |
| `[lang:code]` prefix | A render policy the tutor sets and GA does not — GA stops paying for it |
| `[FOCUS:]` scan window | Persona-declared tags. GA declares none and recovers first-audio latency |
| Cast-voice suppression via `voices` shape | Persona supplies its own segment/voice policy; generic code stops inferring persona type from data shape |
| ~~`extract_episodes = strategy is None`~~ | **Done 2026-08-02** — `AssistantPersona.extracts_episodes` |
| Selection skipped on the switch turn (`session.py:688`) | A persona decides whether it opens with content; generic code stops making that call for everyone |
| Engagement raised by any user mention (`upsert.py:17,262-264`) | The persona declares whether a turn was *spontaneous* or *elicited*; only the first counts. Replaces the tempting "GA is special" exception, which would be the same disease under a different name |
| No home for cross-session persona state | Runtime state distinct from `AssistantPersona.settings` — the latter is configuration (bundle-supplied, voice-editable, FR-701), not a scratchpad |
| `assess_items` then `propose_items` (separate dispatches, `memory.py:144-148` then `EnrichMemory` at `server.py:183-187`) | One `consolidate` call — same lifecycle moment already; can share computed state instead of both re-deriving it |

### GA becomes `DefaultPersona`

An implementation like any other — memory brief, default recall policy, default segment
policy, no offline hooks. Likely under 50 lines. This is the load-bearing part: **there
is no longer a GA code path for persona logic to leak into**, because GA stops being a
code path. Everything else in this note is consequence.

Note this does **not** retire FR-201 (GA must always exist, seeded, fixed id) — GA
remains the mandatory default *persona*; it stops being the default *code path*.

## What this deliberately does not change

- **Personas stay runtime-coupled.** Decoupled in code (independent modules, no
  compile-time dependency on GA or each other); still one shared session, shared memory
  substrate, still switchable mid-conversation via FR-207 directives.
- **Full decoupling — persona chosen at session start, no switching — is rejected.**
  Two reasons. (a) The memory substrate is the differentiator, not the voice pipeline;
  STT→LLM→TTS over a WebSocket is commoditised. Fully independent personas either
  reimplement consolidation/upsert/embedding/replay each, or consume mamemai as an
  optional library — recreating the coupling with weaker guarantees and no enforceable
  invariants. Specifically, the tutor reading GA's episodes (INV-10, "anchored to your
  life") is the highest-value cross-persona behaviour and the first casualty. (b)
  Voice-only makes session-start selection actively worse UX: a spoken menu before every
  session, and no "switch to my tutor" mid-flow.
- **No plugin loader, no third-party in-process Python.** For a local privacy-first
  product that is a trust boundary worth keeping closed. The tiered extension surface
  stands: bundles (data, anyone — already shipped), declarative `strategy` + `settings`,
  in-tree Python strategy behind a reviewed PR.
- **No generalisation of `MemoryType` or `EngagementLevel`.** One specialist is
  insufficient evidence; rule of three.

## The counter-case

Recorded deliberately — these are the strongest objections and none is fully answered.

1. **Wider blast radius.** `select_items(persona_id, focus, limit)` is trivially
   auditable. A `TurnContext` carrying conversation history and a memory view is far
   more surface to misuse, and materially more work to keep genuinely read-only.
2. **"Optional" stops being free.** Today a persona implements zero to four ports and
   *absence is the default behaviour* — genuinely elegant, and GA's registration of
   nothing is the proof. A single interface means either boilerplate in every persona
   or a base class to inherit, which invites fragile-base-class problems as personas
   diverge.
3. **Designed from one example.** This is the same rule-of-three objection this note
   raises against generalising `EngagementLevel` — and it applies here with full force.
   A `TurnContribution` derived only from the tutor's needs will have tutor-shaped
   fields, and they will be *harder* to dislodge than four visible ports, because they
   will look generic. **This is the reason the note is unscheduled.**
4. **Test cost.** Fakes for one wide port are more work than fakes for four narrow ones
   (fakes-over-mocks is the standing rule).

## Sequencing

The collapse is the right end state and the wrong next step.

**Prerequisite: build a deliberately dissimilar persona #2, then refactor at the points
of disagreement.** Every leak listed in Evidence §4 is only *provably* a leak once a
second persona disagrees with it. Maximise disagreement: no bundle, no spaced
repetition, no voice cast, no curriculum — and specifically one that wants **assessment
*and* episodes**, which breaks `memory.py:121`'s proxy immediately and forces the real
distinction into the open.

**Persona #2 chosen and designed 2026-08-01** — a cognitive-training persona, written up
in [BRIEF_persona_cognitive.md](BRIEF_persona_cognitive.md). It satisfies the criterion:
it wants assessment *and* episodes, it is a **consumer** of the memory substrate rather
than an author of its own content, it has no target language and no voice cast, and its
selection is anti-curriculum. Checking its needs against the code produced four verified
gaps and one reverse leak (Evidence §6) — and disproved two of the six candidates,
including a "speak first" hook that turned out to be unnecessary. Its Part 3 is the
concrete evidence this section was waiting for.

One honest limit: the tutor and the cognitive persona still share "structured activity
that brings its own content". A third persona that is purely reactive — a journalling
companion, say — has none of that. Testing the final contract on paper against that case
costs nothing and is worth doing before it is frozen.

Four items are worth doing *before* the contract, independently — none presupposes it,
none is wasted if its shape changes:

1. ~~**Retire the `extract_episodes = strategy is None` proxy**~~ — **done 2026-08-02**,
   see below.
2. ~~**Free the retention formula into `domain/`**~~ — **done 2026-08-02**, see below.
3. ~~**Move pacing policy out of `_next_selected_item`**~~ — **done 2026-08-02.**
   `SelectionPacing` is declared on `PersonaSelectionPort`; the tutor now states its two
   choices in its own module instead of having them assumed by the pipeline. Narrower
   than sketched here: `WorkingMemory.selection_batches` did **not** move, because the
   batch is *session* state and strategies are startup-constructed singletons with
   nowhere to put it. Giving personas a session-state slot is the contract's job, not a
   preparatory step's — see the note added under Proposed shape.
4. **Mechanical leak test in CI** — ~~proposed~~ **done 2026-07-31**, see below.

## Implemented 2026-08-02 — the retention formula is generic

Sequencing item 2, and the first repair of the *reverse* leak (Evidence §6): a generic
capability trapped inside a persona. `domain.model.retention(days_since,
half_life_days)` is now the curve; `language_tutor/selection.py`'s `_retention` keeps
everything that is genuinely the tutor's — which `persona_state` keys hold the inputs,
and the `-1.0` sentinel ranking an item with no usable SRS state as most due.

**The split held exactly where the note predicted.** What moved is the arithmetic and
nothing else: no key names, no growth/shrink policy, no sentinel. A domain function that
took `persona_state` and looked up `"half_life_days"` itself would have been the INV-6
violation this note warns about, dressed as a refactor.

**One boundary decision worth recording.** A non-positive `half_life_days` raises rather
than returning a sentinel. Deciding what an unusable SRS state *ranks as* is persona
policy, so the generic function refuses to answer instead of baking the tutor's `-1.0`
into `domain/`. Negative `days_since` (clock skew) is clamped to 0 — that is
degenerate-input arithmetic, not policy, and matches `cosine_similarity`'s existing
zero-vector posture in the same module.

Nothing about the tutor's behaviour changes; `_retention`'s observable results are
identical. 9 new unit tests (`tests/unit/domain/test_retention.py`), including a
documented float64 limit: past ~1074 half-lives the curve underflows to exactly 0.0 and
such items tie. Harmless for ranking precisely because the persona's sentinel is
negative — a never-practiced item still sorts ahead of a long-abandoned one. Spec:
TR-802 amended, GLOSSARY's **Retention** entry moved out of the tutor tier into *Recall &
selection* and rewritten to state which half is generic.

## Implemented 2026-08-02 — the episode proxy is retired

Sequencing item 1. `extract_episodes = strategy is None` is gone; `ConsolidateMemory`
now reads `AssistantPersona.extracts_episodes` (new field, new column, default `true`)
from the conversation's persona and passes it straight to the extractor.

**Where the declaration lives, and why not in `settings`.** INV-6 forbids generic code
reading inside `settings`/`persona_state`, and this value must be read by generic code —
so it is a **first-class attribute**, like `strategy` or `response_language`. The
alternative considered and rejected was a registry keyed on `strategy` in the composition
root: that is the same proxy wearing a different hat.

**Named for the fact, not the mechanism.** The question the field answers is *does this
persona's conversation record real events?* Rehearsal — drills, role-play, exercises — is
performance, not autobiography. That framing survives a persona that assesses *and*
remembers; "has an assessment strategy" never could.

**Migration preserves today's behaviour and stays idempotent.** The column is added
nullable, backfilled `(strategy IS NULL)` only where NULL, then constrained `NOT NULL
DEFAULT TRUE`. Re-applying `001_initial_schema.sql` (which the schema runner does every
start) leaves no row NULL, so a persona later declared `true` *with* a strategy — exactly
the case the proxy could not express — is never clobbered back to `false`.

Also landed: the bundle format carries `[persona] extracts_episodes` (bool, rejected if
not a bool — never coerced), the shipped Italian tutor bundle declares `false`
explicitly, and `save()`'s UPDATE branch carries the column so an ordinary rename or
deactivate cannot silently reset it. Spec: FR-407 rewritten, INV-10/TR-703/TR-706/TR-903
amended, GLOSSARY gains **Rehearsal persona** and a rewritten **Consolidation gates**
(the two gates are now stated as the separate questions they always were).

**Note for the contract.** This is the first case of what the proposed shape calls a
persona *declaration* — data the persona states about itself that generic code reads and
routes on without interpreting. It is worth watching whether the next two or three
declarations want to live as sibling columns or as one declared-capabilities structure;
one instance is not enough to tell, and the rule of three applies here as much as
anywhere else in this note.

**Not yet live-verified**: unit-tested only (336 green). Needs a real workstation run to
confirm the migration applies cleanly against the existing database and that the seeded
tutor persona ends up `false` via the backfill rather than the bundle path.

## Implemented 2026-07-31 — the leak test

Sequencing item 3, done ahead of the rest. `server/tests/unit/test_no_persona_leak.py`
parametrises over every `.py` under `domain/` and `services/` (14 files) and fails on
`tutor|CEFR|HVPT|learner`, case-insensitive. It cites INV-6, whose rule it enforces one
level earlier than a branch: generic code that has learned to talk about one persona's
domain is generic code about to start branching on it.

**Word list, tightened after checking what actually matches.** `lesson` is *excluded* —
`BundleLesson` / `bundle.lessons` (`services/ports.py:292-295`,
`services/bundle_install.py:120-122`) is bundle-*format* vocabulary and belongs in
`services/`; a lesson is an ordering unit. `vocabulary` is excluded too: its hits
(`domain/model.py:287,311`) are the ordinary English sense.

**Making it pass took 26 sites** — 25 comment/docstring rewordings plus one real
identifier, `_TUTOR_DEBUG` → `_PERSONA_DEBUG` (env var `MAMEMAI_TEST_TUTOR_DEBUG` →
`MAMEMAI_TEST_PERSONA_DEBUG`, trace prefix `[tutor-debug]` → `[persona-debug]`), which was
the last persona-specific identifier in the core. The rewordings state the generic rule
and, where an example genuinely helped, use a neutral one. Two carry a forward reference
to this note rather than being smoothed over — `memory.py`'s `extract_episodes` proxy and
`session.py`'s `_render_turn_content` — because in both cases the *behaviour* is the
leak and the comment was merely describing it accurately.

**Known out of scope.** Generic *infrastructure* still carries the vocabulary:
`infrastructure/config.py:35` and `infrastructure/llm/_common.py:50,55,63` (the shared
extraction-prompt builder encodes one persona's episode exemption — the same proxy as
`memory.py:114`, one layer down). Widening the scan to `infrastructure/` requires
excluding each persona's own strategy package and fixing those four sites first.

**Not yet wired to a CI runner** — the repo has no `.github/workflows`. The test runs
under the documented pre-PR gate (`cd server && uv run pytest`, CONTRIBUTING.md).

## Related open thread — in-band tags

Not part of this contract, but it surfaced in the same review and interacts with it.
`[FOCUS:]` and `[TOPIC_BREAK]`/`[TOPIC_CONTINUATION]` are signalling inside a stream
that is simultaneously being spoken. They cost latency (Evidence §4), they leak into
audio (the known "cast-role brackets spoken aloud" issue), and they require model
cooperation. FR-207 already ran this experiment: replacing `[PERSONA:]` with a
deterministic pre-LLM rule was strictly better on every axis. The same argument likely
applies to focus, and possibly to boundary detection. Written up separately in
[BRIEF_inband_tags.md](BRIEF_inband_tags.md).

**Ordering: resolve the tags first.** The two threads are independent decisions, but the
dependency runs one way. If tags move out-of-band before this contract is designed,
`TurnContribution` never needs a "declared tags" concept and the
`_PREFIX_SCAN_WINDOW_CHARS` buffer stops being something personas must opt out of — the
contract's surface shrinks. If the contract lands first, that concept gets designed and
then deleted. The tag question is also decidable *now*: unlike the contract, it does not
depend on knowing what persona #2 needs.

## Spec impact if adopted

Sketch only — none of this is written yet.

- **New TR** — the persona contract itself: hook set, ordering guarantees, and the
  read-only-live / read-write-offline split.
- **INV-1** — strengthened from a convention to a statement about the type of the live
  hooks' memory view.
- **INV-6** — extended beyond `persona_state`/`settings` to cover inference of persona
  type from *any* generic-visible data shape (closing the `voices` side channel at
  `session.py:354-361`).
- **FR-309** — recall gating stops being its own persona-scoped policy port and folds
  into `prepare_turn`; the FR keeps its ID with amended wording.
- **FR-201** — unchanged in substance; may need wording to separate "default persona"
  from "default code path".
- Retirements: `PersonaSelectionPort`, `PersonaEnrichmentPort`, `PersonaAssessmentPort`,
  `RecallGate` as separate ports, each keeping its ID per SPEC.md rule 2.
