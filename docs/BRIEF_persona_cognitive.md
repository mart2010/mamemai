# Design Note — The Cognitive Persona

**Status: proposed, not scheduled.** Nothing here is settled. This is the design record
of persona #2 — the deliberately dissimilar second persona that
[BRIEF_persona_contract.md](BRIEF_persona_contract.md) names as the prerequisite for
collapsing the four extension ports.

Origin: 2026-08-01 design session. The goal is the user's own: *limit the cognitive
decline that comes with age* — or equivalently, sustain and sharpen memory, reasoning
and deduction. Short, frequent sessions, the same little-and-often rationale that works
for language. Voice-only, exploiting the LLM's capacity to invent and improvise.

Two things are true at once and this note keeps them apart: it is a persona the author
will actually use (so it must be honest and worth the time), and it is the second data
point that makes the contract designable (so its architectural demands are recorded as
carefully as its behaviour).

---

## Part 1 — What the evidence actually supports

The user asked that this be grounded in research rather than intuition. The honest
picture changes the design, so it comes first.

### What does not hold

The promise the commercial brain-training market is built on — train 15 minutes a day
and your general cognition, attention and everyday memory improve — is **not supported**.
Owen et al. (*Nature*, 2010; ~11,000 participants) found gains on trained tasks and no
transfer to untrained cognition. Simons et al. (*Psychological Science in the Public
Interest*, 2016) reached the same conclusion reviewing the field, as did the 2014
consensus statement organised by the Stanford Center on Longevity and the Max Planck
Institute. Jaeggi et al.'s 2008 dual-n-back claim of fluid-intelligence transfer did not
replicate robustly. In 2016 the FTC fined Lumosity $2M for advertising exactly the claims
this persona must not make.

**This persona therefore does not promise to prevent or slow cognitive decline.** Any
wording that implies it is a defect, not marketing.

### What does hold

- **Domain-specific gains are real and durable.** The ACTIVE trial (~2,800 older adults)
  showed improvements in the trained domain persisting up to 10 years. The later claim
  linking speed-of-processing training to reduced dementia risk is **contested** and is
  not relied on here.
- **Retrieval practice (the testing effect)** — retrieving beats restudying, robustly
  (Roediger & Karpicke). **Spacing** and the **generation effect** are similarly solid.
  Critically, the benefit comes from the *act of retrieval*, not from being scored.
- **Cognitive reserve** (Stern) is the best-supported frame: sustained engagement in
  effortful, novel, varied cognitive activity is associated with resistance to decline.
  Observational and confounded (people already declining engage less), but consistent.
- **The larger levers are elsewhere** — aerobic exercise, sleep, vascular health, hearing
  correction, social engagement (Lancet Commission on dementia prevention). FINGER
  (Ngandu et al., *Lancet*, 2015) is positive but multidomain; cognitive training is one
  of four ingredients.

### The inversion this licenses

Generic brain training bets everything on **far transfer** and loses. The gain is locked
inside tasks that are worthless outside themselves — you become excellent at a grid of
coloured tiles.

mamemai can bet on **near transfer** and win, because near transfer is the part that
reliably works and mamemai is the only one holding the user's actual content. Retrieval
practice over your own professional notes, your own procedures, your own episodes
produces a gain that is valuable *in itself*. It needs no contested transfer hypothesis
to be worth doing.

**The defensible promise:** *you reliably get better at what you practise, and this is
demanding, varied, novel, interactive daily cognitive activity — the profile associated
with cognitive reserve.* That is honest, and it is a better product than the one the
evidence would not support.

### The paradigms map onto validated instruments

The session's opening intuition — the LLM invents a story, the user retells it in their
own words, then answers specific questions — is the **Logical Memory** subtest of the
Wechsler Memory Scale, reconstructed from first principles. Immediate recall largely
measures attention; **delayed** recall (~20–30 min) measures consolidation, which is why
the delay is not a protocol detail but the point.

Verbal fluency ("name as many animals as you can in 60 seconds"), digit span backward and
serial sevens are likewise standard verbal instruments. Voice-only is not a constraint
for this persona — it is the native channel of the entire verbal battery. What it cannot
do is the visuospatial and reaction-time half, and it should not pretend otherwise.

---

## Part 2 — The persona

### Exercise families follow mamemai's memory types

The most useful structural finding of the session: mamemai's three long-term memory types
already partition along the same lines cognitive psychology partitions memory. The
exercise taxonomy is therefore **derived, not invented**.

| mamemai type | Memory system | Exercise family | Evidence |
|---|---|---|---|
| Episode | Episodic (Tulving) | Free recall, cued recall, temporal order, source memory | Reminiscence — modest but real |
| Concept | Semantic | Retrieval practice, elaborative interrogation ("why?"), category fluency within a domain | Strongest of the three (testing effect) |
| Procedure | Procedural / sequential | "Walk me through the steps", spot the missing or wrong step, reorder, "what if step 3 fails?" | Generation effect, worked examples |

This makes the cognitive persona a **consumer** of the memory substrate in a way neither
GA nor the tutor is. The tutor authors its own content; this one reads the whole store.

### Cold start: a ladder, not a wall

Not every family depends on the substrate, so an empty store degrades the persona rather
than disabling it.

| Family | Substrate needed | Source |
|---|---|---|
| Invented story, immediate + delayed recall | none | generated per session |
| Word lists, span, mental arithmetic, verbal fluency | none | generated |
| Logic and reasoning | none | generated — with a caveat below |
| Concepts / procedures, spaced retrieval | yes | substrate |
| Episodes, autobiographical recall | yes | substrate |

The generated families are the **floor**; the substrate families are the
**differentiator**. Writing this down as a design principle matters mainly to stop the
curated half being over-built.

Generation also solves the practice-effect problem that fixed-item batteries have: you
cannot memorise a test that did not exist yesterday. That is a genuine advantage over
published instruments, not a cosmetic one.

### The persona bootstraps its own supply — half of it

When a session runs on a real topic (the user's profession, hobbies), the user produces
genuine content. **Concepts already flow**: `services/memory.py:129-137` extracts them
for every persona including strategy-owning ones, deliberately and with a comment saying
so.

**Episodes did not.** `memory.py:121`'s `extract_episodes = strategy is None` cut them off
for any persona with an assessment strategy. So the autobiographical-recall family — the
most motivating one, and the one closest to the memory that matters day to day — could
never be filled by the persona's own sessions.

That changed the severity of the proxy. In the contract note it was "the leak most likely
to produce a wrong result"; it was in fact **a block on this persona's bootstrap path**,
which moved it ahead of everything else in sequencing. **Retired 2026-08-02** — this
persona declares `extracts_episodes = true` and both halves of its supply now flow.

### Session shape

- **Short and frequent**, little-and-often, same rationale as language practice.
- **Monotype by default** — today is stories, tomorrow is concepts — with the user able
  to override by voice ("concepts today instead"). Closed vocabulary, three or four
  types, so a deterministic rule, not embedding similarity (the FR-207/TR-315 precedent).
- **The session opens on the switch turn.** Sessions always start on GA (TR-301), so this
  persona is always entered by a switch directive. The utterance "switch to the cognitive
  coach" *is* a user turn, and the session opening is simply its reply. **No "speak
  first" hook is needed** — this was proposed during the session and correctly rejected.
  What is needed instead is durable state, below.
- **Delayed recall is an obligation that outlives the session.** "Ask about the lighthouse
  story at next activation." Not a spontaneous interjection — a debt carried in persona
  state and discharged when the persona is next activated.

### Scoring: measure, do not display, persist the minimum

Scoring is deliberately **not** the point of this persona, and three things that are
easily conflated are separated:

- **Measure** — the persona must know whether the story was too easy, or it cannot adapt
  difficulty. Internal to the session.
- **Display** — no. No spoken numeric verdict.
- **Persist** — only what cross-session adaptation needs, in the persona's own opaque
  state (INV-6 already covers exactly this).

Measuring without displaying is coherent precisely because the testing effect comes from
the act of retrieval, not from the verdict.

The reason this matters beyond taste: a persona in the cognition/health space invites the
user to read scores as diagnosis. Day-to-day variance (sleep, mood, time of day) dwarfs
any real slope. "My score dropped three times — am I declining?" is a question this
persona must never manufacture.

### The divergence rule: report, never adjudicate

The persona is **read-only** on the substrate. But when the user's answer differs
materially from what the store holds, it must say so, so the user can go and correct it
(typically with GA).

When user says X and the substrate says Y there are three causes, and **the persona
cannot distinguish them**:

1. The extraction was wrong → the store needs fixing.
2. The user's memory drifted → this is the very signal the persona exists to notice.
3. The fact changed in the world → the store is stale, not wrong.

Present every divergence as "your memory is wrong" and it becomes the pseudo-diagnosis
problem again. Present every divergence as "my note is wrong" and the user's drifted
memory silently overwrites the store — corrupting the thing being measured.

**Therefore: surface both versions, adjudicate neither.** The decision goes to the user,
via GA. This rule is written down because it is exactly the kind of rule that erodes
without anyone noticing.

An unrequested benefit falls out. Extraction is LLM-based and lossy, and **nothing in
mamemai verifies it against the user today**. This persona, by construction, asks the user
to restate things the store holds. It is an audit that does not announce itself — which
turns "the substrate may be wrong" from a risk into a feature.

### Where curation is genuinely required

LLMs produce **plausible but wrong** logic and reasoning problems: ambiguous, or with a
"correct" answer that is not. That is the one family where the persona tells the user
they are wrong and the user has no recourse — structurally the same corrosive shape as
the divergence problem.

So curated content earns its place where **verifiable ground truth** is required, and
essentially only there: a small library of reasoning items with validated answers.

Everything else is better generated. This leads to the distinction that decides what a
bundle should carry:

- **Exercise protocol** — "generate a story with 8–12 scorable elements; ask for free
  recall; then four targeted questions; re-ask at next activation." The *how*. Finite (a
  dozen at most), authored once, **never exhausted**, and works against an empty
  substrate because content is generated to fill it.
- **Exercise item** — a specific problem with its answer. Finite, **exhaustible**, and
  the practice effect returns once the user has been through it.

The protocol is the right unit for a bundle. Items are worth shipping only where
generation cannot be trusted.

---

## Part 3 — What this persona demands that mamemai does not have

All six candidates raised during the design session were checked against the code. Two
resolved; four are real. Each is a specific, cited gap — the point of building persona #2
before designing the contract.

### Resolved on inspection

- **Per-item opaque state already exists.** `Concept.persona_state` /
  `Procedure.persona_state` (`domain/model.py:303,319`) with a single-writer contract —
  `MemoryRepository.update_persona_state` (`services/ports.py:124-126`), "upserts never
  touch the column". Nothing needed.
- **A "speak first" hook is not needed** — see Session shape above.

### Gap 1 — a persona cannot bring content on the turn that activates it

```python
# services/session.py:688
selected_item = await self._next_selected_item(wm) if not is_directive_turn else None
```

`is_directive_turn` is true on a successful switch, and the batch is *fetched* lazily
inside the same function, so on the activation turn nothing is fetched either. Real
sequence: "switch to the cognitive coach" → it greets, empty-handed → the user says
something → *now* the exercise arrives. One wasted turn out of a ten-minute session.

This is the cleanest leak found all session. Skipping selection on the switch turn is a
reasonable **UX choice for the tutor** (do not dump a drill on the turn where the user
just asked to switch; let it greet). It is frozen in the generic pipeline and it blocks
another persona's core requirement.

### Gap 2 — no per-persona runtime state for the delayed-recall debt

Assessment is correctly offline (`services/memory.py:144-149`, inside the unit of work),
so INV-1 holds and the debt can be created at consolidation time. But `ItemAssessment`
targets a *memory item*: `update_persona_state(memory_type, item_id, persona_state)`. A
debt about a story the persona invented corresponds to no row.

What remains is `AssistantPersona.settings` — but that is **configuration** (bundle-
supplied, voice-editable under FR-701), not an execution scratchpad. Using it as a debt
queue would merge the two permanently.

### Gap 3 — selection policy is generic, and this persona needs three

`_next_selected_item` (`session.py:545-567`) hard-codes lazy fetch, one item per turn,
and "an exhausted batch is not re-queried; only a `[FOCUS: ...]` marker replaces it".
That is tutor pacing in generic code — already noted in the contract brief's Evidence §4.

Persona #2 makes it unarguable, because it needs **three different policies at once**:

1. **Maximal novelty** for generated exercises — never repeat.
2. **Forgetting curve** for concepts and procedures — target what is about to be lost,
   which is the opposite of novelty.
3. **Salience / recency** for episodes.

One persona, three policies, none matching the tutor's. Policy belongs to the persona;
ordering and budget belong to generic code.

### Gap 4 — no durable, generic emission addressed to another persona

The divergence flag is neither a spoken reply nor persona-private state. It is a durable,
generic object destined to be consumed **by a different persona** (GA) in a later session.

Nothing in mamemai does this. Directives route the *user* between personas; here *content*
travels. And it is precisely what four pull-shaped ports cannot express: they all answer
"what should generic code do for this persona this turn?", and none lets a persona
**emit** something durable and generic.

INV-1 resolves it elegantly, which is a good sign for the boundary: the persona notices
live and says so aloud; the durable flag is written **offline** from the session log. The
same persona logic runs twice — live to speak, offline to persist. That is exactly why
the proposed contract carries both `observe_reply` and `consolidate` on one object. The
requirement validates the shape without being forced to.

---

## Part 4 — Directives, bundles, and the half-life decision

### Directive routing: two opposite generalisations of one field

`Directive.action` is not routed; it is tested once:

```python
# services/session.py:621
if matched_directive is not None and matched_directive.action == "switch_persona":
```

An unknown action falls through silently — no error, no log. Meanwhile two different
generalisations of this field are wanted, with **opposite interpretation rules**:

- **Generic actions** — `switch_persona`, and planned voice-configurable settings such as
  changing the default language (useful for bilingual users) and other FR-701 parameters.
  Generic code understands and executes these.
- **Persona-scoped actions** — `set_exercise_type`. Generic code must **not** understand
  them; it delivers them to the active persona. INV-6 is exactly this boundary.

Both land in the same table via the same deterministic matcher and look alike. Naming the
distinction now is cheap; recovering it later is not.

**Proposed mechanic:** known closed set → generic code executes; unknown action →
delivered to the active persona. Generic code never branches on persona content, it
routes — and a new persona adds commands without a line of generic code changing, which
is the property being sought.

One convenient accident: `is_directive_turn` is derived from the *switch outcome*
(`session.py:638`), not from "a directive matched". A non-switch directive therefore does
not suppress selection — which is the correct behaviour for `set_exercise_type`.

Open question this raises: **who creates those rows?** Today `services/directives.py:105`
generates them in code, one per language, for persona switching only. Persona-scoped
commands must be declared by the persona — a fourth candidate element for the contract,
alongside `prepare_turn`, `consolidate`, and durable emission.

### Bundles: a persona-only bundle is structurally impossible today

`infrastructure/bundle_toml.py:64-65` rejects a bundle with no lesson files. A lesson is
ordering only — a container of `concept | procedure` items, nothing more
(`services/ports.py:291-296`).

The format anticipated the exact inverse of this persona's need:
`BundlePersonaDefinition` notes that "content-only bundles … omit `[persona]`". Content
without persona: supported. Persona without content: impossible. Shipping the cognitive
persona as a bundle today would require inventing a dummy lesson file.

`system_prompt` itself is already carried (`_PERSONA_KEYS`, `bundle_toml.py:26`), so that
part of the requirement is met.

Three consequences:

1. The bundle format is **content-shaped**; this persona needs it to be
   **capability-shaped** — declare a strategy, settings, and its own commands. Extending
   `_PERSONA_KEYS` with a `[[directives]]` table is mechanical.
2. The item vocabulary is `concept | procedure`, and an exercise protocol or a validated
   reasoning item is neither. A third item kind, or a different file class, is needed.
3. **Update semantics move up in priority.** `InstallPersonaBundle` ignores `[persona]`
   when the persona already exists (deferred since Phase 11). For a content bundle that
   is inconvenient; for a persona that is *nothing but* `[persona]`, it means the persona
   can never be updated at all.

Aside: `lesson` was deliberately excluded from the leak test's word list on the argument
that it is generic bundle-format vocabulary rather than tutor vocabulary. A second
persona needing lessons confirms the call was right.

### Half-life: generalise the calculation, not the data

The delegated decision. Today the machinery is entirely tutor-owned —
`_retention()` at `infrastructure/language_tutor/selection.py:69`, `STATE_HALF_LIFE_DAYS`
in `language_tutor/state.py:10`, growth/shrink in `language_tutor/assessment.py:159-197`.

This is the **reverse** of the leak this project has been hunting: not persona logic
escaping into generic code, but a **generic capability trapped inside a persona**. Any
spaced-retrieval persona needs it. The contract must handle both directions.

Split it three ways, because the three parts do not belong in the same place:

- **The formula** — `2 ** (-days_since / half_life_days)`, pure arithmetic with no
  persona vocabulary → move to `domain/`. Reusable by any spaced-retrieval persona. This
  is the part to free. (`state.py:6`: retention is "always DERIVED at selection time …
  never stored" — so this is genuinely a function, not data.)
- **The storage** — already correct. `persona_state` per item is per-persona in effect
  (INV-9: an item belongs to one persona), so no collision is possible. Nothing to do.
- **The key names and the policy** — `"half_life_days"`, and the growth-on-success /
  shrink-on-error rule → stay with the persona. Standardising key names would invite
  generic code to read inside `persona_state`, violating INV-6. **This is the line not to
  cross.**

Generalise the calculation, not the data.

Related: the cognitive persona's forgetting-curve selection should rank on `_retention()`,
**not** on `EngagementLevel`. The tutor anticipated both — `SETTING_RANKING =
"engagement" | "retention"` (`selection.py:33`), defaulting to engagement "until
retention calibration data exists".

### The engagement feedback loop — real, and monotonic

Concepts are extracted for strategy personas too (`memory.py:129-137`, deliberate). So a
cognitive session that quizzes the user on concept X → the user utters X → extraction
re-proposes X → `_has_engagement` (`upsert.py:262-264`) counts the turn → `_max_engagement`
(`upsert.py:17`) keeps the higher value. **Engagement only ever rises**, so the persona
inflates its own selection signal permanently.

The obvious fix — "don't update engagement outside GA" — produces the right behaviour via
the wrong criterion: it encodes "GA is special" into generic code. Not an INV-6 violation
by the letter, the same disease.

**The same rule stated on the nature of the evidence:** engagement counts when the user
mentions an item *spontaneously*, not when they retrieve it *because they were asked*.
Identical behaviour today, still correct for persona #3, and it is the **persona** that
declares which of the two it just produced — another property carried by the contract
rather than by an exception in generic code.

---

## Sequencing impact

This note does not change the contract note's conclusion — build persona #2, then refactor
at the points of disagreement — but it reorders what comes first.

1. ~~**Retire `extract_episodes = strategy is None`**~~ — **done 2026-08-02**. Replaced by
   `AssistantPersona.extracts_episodes`, a first-class declaration (details in
   [BRIEF_persona_contract.md](BRIEF_persona_contract.md)). This persona declares it
   `true` *and* owns an assessment strategy — the combination the proxy could not express
   — so its episodic bootstrap path is open: a session on a real topic now feeds both the
   concept store and the episode store.
2. ~~**Free the retention formula into `domain/`.**~~ — **done 2026-08-02**.
   `domain.model.retention(days_since, half_life_days)` is available to this persona's
   forgetting-curve selection policy on day one; the tutor's key names and growth/shrink
   policy stayed with the tutor, exactly as the delegated decision below called it.
3. ~~**Allow a persona-only bundle** and decide `[persona]` update semantics.~~ —
   **done 2026-08-02.** A bundle may now carry a persona, content, or both, and a
   reinstall refreshes the authored half of the definition while preserving everything
   voice-editable. This persona is installable as a bundle without inventing a dummy
   lesson, and remains updatable afterwards. Still open from Part 4: the **third item
   kind** (exercise protocols, validated reasoning items) — not a blocker, since a
   persona-only bundle carries no items, but required before this persona ships curated
   content.
4. ~~**Move pacing policy out of `_next_selected_item`**~~ — **done 2026-08-02.**
   `SelectionPacing` covers the two rules that actually blocked this persona: it can now
   declare `include_activation_turn=True` (Gap 1 — no wasted opening turn) and
   `refetch_when_exhausted=True` (short frequent sessions). Note the three-policies
   argument in Gap 3 turned out to be about *selection*, not pacing — composing one batch
   from novelty, forgetting-curve and salience rankings was always inside `select_items`
   and never blocked. The real blockers were the two hard-coded pipeline rules.
5. Directive routing, the divergence flag's storage, and per-persona runtime state are
   contract-shaped and should wait for it.

The in-band tag thread ([BRIEF_inband_tags.md](BRIEF_inband_tags.md)) still comes before
the contract, unchanged.

## Open questions

- How many exercise protocols is enough to start? A dozen is the guess; nothing supports
  it yet.
- Difficulty adaptation needs measurement across sessions, which needs the Gap 2 state.
  What is the minimum that must persist?
- The text-only capture channel (note-taking at work) is a client/protocol concern, not a
  persona one — but this persona's value is gated on the store having content, so it is a
  real dependency rather than an aside. Parked deliberately.
- Should the divergence flag be visible to the user directly, or only surface when GA next
  touches the item? The second is quieter and probably better; undecided.
