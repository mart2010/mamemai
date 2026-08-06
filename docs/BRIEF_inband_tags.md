# Design Note — Retiring the In-Band Response Tags

**Status: proposed, not scheduled.** Decidable independently of
[BRIEF_persona_contract.md](BRIEF_persona_contract.md), and best decided *before* it —
see Sequencing.

Origin: 2026-07-31 architecture review. Split out of the persona-contract note because
it is a narrower question with an earlier answer, and folding the two together would
re-import the single-example bias that note warns against.

## The channel, and what is wrong with it

Two control signals are carried **in-band**: emitted by the LLM inside the same token
stream that is simultaneously being split into sentences, synthesised, and spoken.

| Tag | Purpose | Spec | Consumed |
|---|---|---|---|
| `[FOCUS: wish]` | the user's session wish steers selection | FR-502, TR-304 | live, `session.py` step 8 |
| `[TOPIC_CONTINUATION]` / `[TOPIC_BREAK]` | topic-continuity classification | FR-112, TR-304 | **offline**, `replay.py:_group_into_conversations` |

A third bracket family — cast-role markers like `[SPEAKER:default]`,
`[TARGETTEACHER]`, `[GUIDE]` — is emitted by the tutor but consumed by **nothing**
(see §Cast-role brackets).

Using the spoken stream as a control channel costs four things. Each is observed, not
hypothetical.

### 1. Latency, charged to every persona

`_PREFIX_SCAN_WINDOW_CHARS = 200` (`session.py:173`). A reply with no tag at all must
buffer up to 200 characters — or reach stream end — before the first sentence can be
synthesised. GA emits neither tag and pays this on every turn. The constant's own
docstring is explicit that this is "a latency/reliability tradeoff, not a free win",
and the value is an untuned placeholder.

### 2. Leakage into audio

The stripper is pattern-based and only knows the tags it was told about. Anything else
in brackets is spoken literally. Two recorded instances:

- **Localized FOCUS marker** (PLAN.md, flagged 2026-07-13): the tutor spontaneously
  emitted `[Focalizza: …]`. `_extract_tag` matches the literal `[FOCUS:` only, so it
  fell through and was spoken as bracket text.
- **Cast-role markers** (2026-07-18 live testing, deferred): synthesised literally,
  sounding like "bracket speaker colon target teacher bracket".

The first is the structurally interesting one. A multilingual product asks a model to
emit fixed English control tokens while speaking Italian; models translate their own
control tokens. That is not a tuning failure — it is the channel being wrong for the
product.

### 3. Reliability ceiling

PLAN.md (Phase 12, 2026-07-13): bracket-tag emission on `aya-expanse` "has a real
reliability ceiling"; a front-loaded few-shot got `[PERSONA:]`/`[FOCUS:]` to 8/8, i.e.
correctness required prompt scaffolding tuned per model. Ollama tool/function-calling is
already noted there as an unexplored alternative signalling channel.

### 4. It fights the language rule

Prompt scaffolding that names tags and other personas is what caused the GA language
drift retired under FR-202/FR-203. Every in-band tag re-adds a little of that pressure:
the model is asked to interleave English control tokens with non-English speech.

## Two precedents that already won

This is not a new bet. Mamemai has retired in-band signalling twice, and both times the
replacement was strictly better:

- **FR-207 — persona switching.** `[PERSONA:]` (FR-202/FR-203) replaced by a
  deterministic keyword rule (`services/directives.matches_trigger`) evaluated
  *before* the LLM is called. It removed a per-turn embedding call, removed the
  system-prompt scaffolding that caused language drift, and made switching
  non-negotiable rather than a model judgment.
- **TR-305 — cast voice selection.** Moved from LLM-emitted speaker tags to
  per-segment language detection. "No LLM tag cooperation needed."

Both replaced *model cooperation* with *deterministic local computation*, consistent
with the standing preference for deterministic rules over embedding/LLM inference on
closed-vocabulary decisions.

## Proposal, tag by tag

### `[FOCUS:]` — move detection to the user turn

The core observation: **the signal originates in the user's own utterance, and the
interpretation is already an out-of-band call.**

The user says "let's just review old vocabulary today". Mamemai currently asks the LLM to
echo that wish back inside its spoken reply as `[FOCUS: …]`, parses it out of the
audio-bound stream, and hands the extracted text to
`FocusInterpreter.interpret(focus, categories)` — an LLM call that already runs
separately inside the strategy (`OllamaFocusInterpreter`, wired at `server.py:218`).

So the in-band tag performs only *detection* and *extraction*, of something already
present verbatim in the user turn, before handing off to an interpreter that would have
run anyway. The round trip through the spoken stream buys nothing.

Proposed: detect a session-wish utterance on the **user turn**, before the LLM call,
and pass the utterance to the existing `FocusInterpreter`. Detection can reuse the FR-207
machinery (`matches_trigger`-style keywords + word cap) — a wish is a short imperative,
the same shape a directive trigger already handles. `_PREFIX_SCAN_WINDOW_CHARS`
disappears entirely, and with it the latency charged to GA.

Open question, honestly: a focus wish is *open* vocabulary where a persona name is
closed, so a keyword rule will have lower recall than the tag does when the tag works.
Mitigations worth weighing — a deliberately loose trigger (the interpreter is the real
filter, and a spurious focus merely re-fetches a batch), or accepting that the wish must
be phrased as a recognisable request. Needs a decision before implementation.

### `[TOPIC_BREAK]` / `[TOPIC_CONTINUATION]` — decide offline, not live

The decisive fact: **nothing live consumes these.** They are written into the session log
(FR-111) and read only by `replay.py:_group_into_conversations`, during the offline
pipeline. They are computed on the live path, at live-path latency cost, purely to be
stored and used later.

Proposed: drop them from the response entirely and classify continuity **at consolidation
time**, over the logged turns. The offline pipeline already makes LLM calls with no
latency budget (extraction, worthiness, synthesis, MemoryBrief), and it sees the whole
conversation rather than having to judge continuity one turn at a time from the front.
That is strictly more information for the same decision.

Two constraints to preserve:

- `[TOPIC_CONTINUATION]` relates a session to the *previous* one and is valid only on the
  first turn. Offline, this is the same question asked with more context — the previous
  session is right there in the log.
- **A persona switch must still force a break deterministically** (TR-315, `session.py`
  step 7). That is already not a tag and must stay live and deterministic: consolidation
  attributes an unbroken conversation to one persona (FR-303), so the boundary has to be
  recorded when the switch happens. Unchanged by this proposal.

### Cast-role brackets — **fixed 2026-07-31**

Not a control channel at all: TR-305 made them vestigial, and nothing reads them.

This note originally asserted the root fix was the tutor's system prompt "which should
stop asking for them". **That was wrong** — checking the bundle showed the prompt has
told the model the opposite since TR-305 landed (2026-07-13): *"there is no tag or
marker to place, just write naturally in the right language for who's speaking."* The
markers observed on 2026-07-18 were therefore emitted **unprompted**, which inverts the
fix: the backstop is the load-bearing part, not the defence in depth.

Shipped: a marker-shaped strip in `_strip_markdown` (`_SPOKEN_MARKER`), spec'd under
FR-106/TR-308. Deliberately narrower than the `[A-Z_:]+` sketch above — it matches one
unspaced token with an optional `:` payload, so it also catches the localized
`[Focalizza: …]` case (mixed case, spaced payload) while leaving ordinary bracketed
speech (`[1, 2, 3]`, `[see below]`) alone. Stripping *any* bracketed span would silently
drop words the user is meant to hear.

The bundle prompt was still strengthened, from "there is no marker to place" to a
positive prohibition — mentioning markers at all arguably primes for them. Note this
reaches only **new** installs: `InstallPersonaBundle` ignores the `[persona]` definition
when the persona already exists (upgrade semantics deferred, Phase 11), so an installed
tutor keeps its original prompt. Another reason the stripper had to carry the fix.

### `[lang:code]` — stays

The inverse direction: rendered *into* the LLM context on user turns (FR-114/TR-303), not
emitted by the model. Not part of this thread. The `_LANG_TAG` stripper stays, because it
exists precisely to catch the model *mimicking* the convention back — a small, permanent
piece of evidence that models copy whatever tag vocabulary they are shown.

## What is not proposed

- **Not tool/function-calling.** PLAN.md floats it as an alternative channel. It is still
  model cooperation, with a narrower model compatibility surface, and both precedents
  above won by removing cooperation rather than re-plumbing it. If a signal genuinely
  needs open-vocabulary judgment, a dedicated out-of-band call is simpler and portable.
- **Not removing the boundary concept.** Only moving *where* it is decided.
- **Not touching FR-207.** It is the model for this work, not a target of it.

## Sequencing

**Do this before the persona contract.** The dependency runs one way: with tags gone,
`TurnContribution` never needs a "declared tags" concept and the scan-window buffer stops
being something a persona must opt out of — the contract's surface shrinks. In the other
order, that concept gets designed and then deleted.

It is also decidable now. The contract waits on a second, deliberately dissimilar persona
to reveal the real seam; this thread waits on nothing.

Suggested order within the thread, cheapest and most certain first:

1. **Cast-role brackets** — a known, recorded bug with a root-cause fix in one persona's
   prompt. No spec change beyond a note. Fixes audio quality immediately.
2. **Boundary markers offline** — the strongest argument (nothing live consumes them) and
   contained within the offline pipeline plus a `TR-304` amendment.
3. **`[FOCUS:]` detection on the user turn** — last, because it has the one genuinely open
   question (open- vs closed-vocabulary detection recall).

## Implemented 2026-08-02 — the channel is gone, both replacements deferred

Done in one step rather than the three staged above, because investigating the second
item collapsed it.

**The finding that changed the plan: `[TOPIC_BREAK]`/`[TOPIC_CONTINUATION]` had a parser
but no producer.** Grepping every system prompt, bundle, config file and wizard template
turned up nothing that ever asked a model to emit them. This note's own §1 table lists
them as consumed offline, which is true, but the signal reaching that consumer was
essentially always absent — the only mid-session `BREAK` that has ever fired in practice
is the deterministic persona-switch override (TR-315, 2026-07-20). That is exactly what
the 2026-07-20 live test observed when a whole GA→tutor→GA session collapsed into one
`Conversation` row. So "move boundary classification offline" was not a relocation of
working machinery; it was **new work to build a classifier that never existed**, and
retiring the parser costs nothing that was actually running.

`[FOCUS:]` was the opposite: a real, working feature with exactly one producer, the
tutor bundle prompt (`bundles/italian-a0-starter/bundle.toml`).

**What was removed** (`services/session.py`): `_try_resolve_prefixes`, `_extract_tag`,
`_tag_might_still_open`, `_resolve_boundary_marker`, `_BOUNDARY_MARKERS`, and
`_PREFIX_SCAN_WINDOW_CHARS` — the 200-char lead-in buffer every persona paid on every
turn. The streaming loop now synthesises a sentence the moment it completes, with nothing
in front of it. The tutor prompt's `[FOCUS:]` instruction became a plain
follow-their-lead sentence.

**What was deliberately kept:**

- The **persona-switch break** (step 7). Deterministic, load-bearing for persona-scoped
  consolidation, never an LLM signal — it was only ever *co-located* with the tag channel.
- `ConversationBoundaryType`, the log's marker field, and `replay.py` untouched. Old
  session logs are kept forever (INV-5) and any marker one already carries stays readable.
- `select_items(focus=…)`, `TutorFocus`, and `FocusInterpreter`. This cuts against the
  project's no-orphans rule and the call was made explicitly: the replacement is user-turn
  detection, and rebuilding the interpreter to feed it would be pure churn. `focus` is
  now always `None`.

**Two gaps are now open by decision, not oversight**, and both are recorded in the spec
rather than left implicit — FR-112 as a known accepted gap, FR-502 as `[SUSPENDED]`:

1. No session wish reaches selection. Every persona gets its default batch.
2. Two unrelated topics in one uninterrupted stretch with one persona consolidate as a
   single conversation — one worthiness verdict, one summary, extraction over mixed
   content.

Both wait on the same thing: a second persona to design against and real sessions to test
with. Which is the same reason the contract itself is unscheduled.

**Sequencing note, now settled.** This thread was argued to come *before* the persona
contract, and it did. `TurnContribution` never needs a declared-tags concept, and the
scan-window buffer is not something a persona must opt out of, because neither exists.

344 unit tests green; 6 tests rewritten from "the tag works" to "the tag is inert",
following the `test_persona_tag_in_llm_response_is_now_inert` precedent from FR-207.

## Spec impact — as landed

- **TR-304** — `[RETIRED 2026-08-02]`, ID kept per SPEC.md rule 2.
  `_PREFIX_SCAN_WINDOW_CHARS` and its rationale went with it.
- **FR-112** — `[RETIRED 2026-08-02]` rather than reworded to a consolidation-time
  classification: there is no classifier to describe yet, and specifying one nobody wrote
  would be exactly the spec-drift SPEC.md exists to prevent. The accepted gap is stated in
  the retirement note.
- **FR-502** — `[SUSPENDED 2026-08-02]`. Not retired: the requirement stands and the
  strategy side is intact; only its mechanism is gone. The note says plainly that no wish
  reaches selection today.
- **TR-306** — amended: `focus` is always `None`, and why the parameter survives.
- **TR-308** — amended: the marker strip is now the *only* thing between a bracket token
  and the speaker, load-bearing rather than a backstop, which is also the argument for
  keeping it marker-shaped rather than stripping every bracket.
- **TR-315** — amended: the persona switch is now the sole source of a mid-session break.
- **FR-111** — **untouched, and that is the point.** The log format still carries a
  per-assistant-turn marker and `replay.py` still reads it; only the set of things that
  can write one shrank to the persona switch. Nothing about the persisted format changed,
  so INV-5's kept-forever logs stay readable — the concern this section raised turned out
  not to bite, because classification did not move offline, it went away.
- **GLOSSARY** — *Conversation boundary* and *Focus* rewritten; both now state what is
  absent, not just what remains.
- **TR-305** — unchanged; cross-referenced as the precedent.
