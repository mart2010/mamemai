# Eval Glossary — the Ubiquitous Language of `eval/`

*Created 2026-10-11. Subordinate to [../../docs/spec/GLOSSARY.md](../../docs/spec/GLOSSARY.md):
terms defined there (Persona, Session, Conversation, Episode, Concept, Procedure,
Directive, MemoryBrief, …) keep their meaning and are **never redefined here**. This file
only adds the vocabulary of the evaluation harness.*

If a term feels imprecise during design work, fix it here first, then everywhere else.

## Name-collision rules (decided 2026-10-11)

| Do not say | Because | Say instead |
|---|---|---|
| "persona" for the fictional user | *Persona* means the **assistant** persona (GA, tutor, coach) | **Synthetic User Profile** |
| "episode" for a scripted dialog | *Episode* is a long-term **memory type** | **Scenario** (the plan) / **Dialog** (the realised text) |
| "test" for an evaluation | `tests/` are pass/fail regression tests; evaluations yield graded **Scores** | **Suite** / **Run** |
| "session" for a scenario | *Session* is a live websocket connection (spec) | **Scenario** |
| **Scenario Kind** | What a **Scenario** is for: `disclosure` (the user tells the assistant about their life — carries **Ledger Facts**), or one of two fact-free negative cases for the worthiness gate: `small_talk` (phatic chat) and `assistant_operation` (talk about the assistant itself, which the spec's Worthiness criteria exclude). |
| **Withheld fact** (a Scenario's `omits`) | A fact from a *later* Scenario, in a domain this one touches, that is already true at the Scenario's date but which the user does not tell yet — a trap for an extractor or assistant that fills in what was never said. Future events and revisions cannot be withheld. |
| **Worthiness label** | Whether a Scenario should yield episodes: true iff the user tells at least one dated event (`Scenario.expects_episodes`). Concepts are not gated by worthiness, so an attribute-only Dialog may still yield concepts. |

## Terms

### The thing being measured

| Term | Definition |
|---|---|
| **System Under Test (SUT)** | The Mamemai component(s) a **Suite** exercises: an LLM model, an embedding model, a prompt, a threshold, a use case, or the whole server. A **Run** always names its SUT configuration exactly. |
| **Eval Language** | `en` or `fr`. Every **Synthetic User Profile**, **Corpus** and **Suite** result is tagged with one; results are reported per language and never averaged across them. A **Cross-lingual Probe** deliberately mixes the two. |

### Ground truth (what is *known to be true*)

| Term | Definition |
|---|---|
| **Synthetic User Profile** | A seeded, fictional person: biography, relationships, preferences, habits, interests, a **Speech Archetype** (hesitant or fluent) plus free-text speech colour, and a timeline of life events across **Scenarios**. The source of all ground truth. Never a real person; nothing in it comes from real session logs (EV-INV-9). |
| **Ground Truth Ledger** (*Ledger*) | The authoritative, structured list of **Ledger Facts** about one **Synthetic User Profile**, each with the **Scenario** in which it is first revealed. Written *before* any **Dialog** and never inferred from one. Persisted in the `eval` schema; immutable once its **Corpus** is frozen. |
| **Ledger Fact** | One atomic, checkable statement in the **Ledger**: stable `fact_id`, `kind` (a **Fact Kind**), canonical text in the profile's language, accepted paraphrases, `revealed_in` scenario, `domain`, `happened_on` (events only), and optional `supersedes` (the earlier attribute this one revises). The unit every memory metric is scored against. |
| **Fact Kind** | What a **Ledger Fact** *is* in the user's life, independent of how the product stores it: **event** (something that happened at an identifiable time/place; has `happened_on`; expected to become an *Episode*), **attribute** (a stable fact about the user — relationships, home, job, tastes, routines; the product has no dedicated store for it — it can only reach the *memory brief* by being extracted as a concept or episode — so it is scored mainly through the brief and where the extractor puts it is *measured and reported*, not asserted), **knowledge** (general world knowledge the user learned or discussed; expected to become a *Concept*). Procedures are not a Fact Kind: the live extractor never yields them (FR-307). |
| **Fact Revision** | A **Ledger Fact** that replaces an earlier one (user moved city, changed job, changed their mind). The newer fact points at the older via `supersedes`; the Ledger rejects forks and out-of-order revisions; the unit for scoring knowledge-update handling. A *contradiction* the user never resolves is a distinct, deliberately planted case. |
| **Life Domain** | Where personal **Ledger Facts** (events, attributes) live (family, work, travel, health & fitness, cooking, home, hobbies, finances, …). Drives *what the user talks about*. |
| **Knowledge Domain** | A subject with externally checkable world facts (astronomy, weather & climate, geography, AI, history of science, …). Drives *what the assistant is asked about*, to measure knowledge and hallucination. Distinct from **Life Domain**: a Knowledge Domain answer is true or false of the world; a Life Domain fact is true only of the **Synthetic User Profile**. |

### Producing dialogs

| Term | Definition |
|---|---|
| **Scenario** | The plan for one **Dialog**: which **Ledger Facts** the user reveals, which **Fact Revisions** occur, which facts are deliberately *not* mentioned, which **Probes** are asked, the user's mood and goal, a target turn count, and the **Eval Language**. Authored/generated *from* the **Ledger**. |
| **Simulated User** | An LLM role-playing the **Synthetic User Profile** under a **Scenario**, replying to the real assistant turn by turn (live) or writing the whole exchange (frozen). Constrained to the **Scenario**'s agenda and held to its profile's **Style Target**. |
| **Dialog** | The realised text of one **Scenario**: a sequence of user and assistant **Turns** (spec term). *Frozen* (generated once by the **Reference LLM**, both sides) or *live* (user side simulated, assistant side being the **SUT**). |
| **Corpus** | An immutable, versioned, language-tagged set of **Profiles + Ledgers + Scenarios + Dialogs**, stored in the `eval` schema and exported to files. A **Run** cites the Corpus version it consumed. Frozen Corpora make component suites deterministic and cheap. |
| **Reference LLM** | The external stronger model (e.g. Claude) used *only inside `eval/`* to generate **Ledgers**, **Scenarios**, **Dialogs**, **Probes**, and to act as **Judge**. Never the **SUT**, never a dependency of the product. Model id pinned per call, every call cached by content hash, never silently substituted (EV-INV-10). |
| **Reference LLM Cache** | Content-addressed store of Reference LLM requests → responses, so re-running a generation or judgement is free and reproducible. |

### Measuring

| Term | Definition |
|---|---|
| **Probe** | A question or stimulus with a known expected outcome derived from the **Ledger** or a Knowledge Domain answer key: a recall question, a paraphrased retrieval query, an unanswerable or false-premise question, a knowledge-update question. Kinds: *single-fact*, *aggregation*, *update*, *abstention*, *cross-lingual*, *hard-negative*. |
| **Hard Negative** | A distractor deliberately close to a target (hypernym, same name in another domain, a near-duplicate with a different value). Exists to stress thresholds and separate "found it" from "found something similar". |
| **Suite** | A named, executable evaluation of one aspect (e.g. `retrieval`, `consolidation`, `conversation`, `hallucination`, `recall-gate`). Declares its **SUT** parameters, its **Corpus** requirement, its **Tier**, and its **Metrics**. |
| **Tier** | Cost class of a Suite. **T0** harness self-tests with Fakes (CI). **T1** component suites on a frozen Corpus (minutes). **T2** live **Simulated User** conversations (tens of minutes). **T3** multi-Scenario soak (hours, overnight). |
| **Run** | One execution of one **Suite** against one **SUT** configuration and one **Corpus** version, producing **Scores** plus a **Run Fingerprint**. Persisted; never overwritten. |
| **Run Fingerprint** | Everything needed to reproduce and compare a Run: git SHA, SUT model digests, prompt hashes, embedding model, thresholds, Corpus version, Eval Language, seeds, Reference LLM model id. |
| **Scratch Schema** | A throwaway Postgres schema (named `eval_run_<id>`), created via the server's existing per-user-schema mechanism, in which the **SUT** consolidates and stores memory during one **Run**. Dropped afterwards. Keeps the SUT's writes away from the durable `eval` schema and from real user data. |
| **Score** | A numeric result with its unit and sample size (e.g. `recall@5 = 0.82, n = 140`). Never a bare pass/fail. |
| **Metric** | A named definition of how Scores are computed (recall@k, MRR, nDCG, extraction precision/recall, hallucinated-item rate, abstention correctness, win rate, TTFT …). |
| **Judge** | A grader for outputs no deterministic rule can score. Always the **Reference LLM** (never the SUT), applying a **Rubric**, voted over several samples, and admissible only once its agreement with the **Calibration Set** meets the bar. Deterministic checks always run first. |
| **Rubric** | The written, versioned criteria a **Judge** applies. Its hash is part of the **Run Fingerprint**. |
| **Calibration Set** | A small hand-labelled set (target ≈ 50 items per Judge task) used to measure **Judge**-vs-human agreement and **Simulated User** realism. The only place human labels enter. |
| **Speech Archetype** | How a **Synthetic User Profile** talks, as one of two named types: **hesitant** (short broken turns, filled pauses "euh"/"um", trailing "…", false starts, repeated words, turns trailing off unpunctuated) and **fluent** (steady complete sentences, almost no fillers, proper punctuation, occasional long explanatory turn). Each Eval Language has one profile of each. |
| **Style Target** | The measurable definition of a **Speech Archetype**: an inclusive range per metric (words per turn p50/p90, fragment, question, no-final-punctuation, filler per 100 words, ellipsis, repeated-word and lowercase-start rates) in `domain/style.py`. A **design target** chosen from general knowledge of spontaneous speech — not measured on any real person — describing the *transcribed* text the assistant receives. Revisited against the **Calibration Set**. |
| **Style Conformance** | Whether a generated **Dialog**'s user side, measured by `compute_style`, falls inside its profile's **Style Target** on every metric. A gate for freezing a **Corpus**; reported per Dialog. |
| **Non-comparable** | Tag on a **Run** whose Reference LLM or SUT setup is a smoke/fallback configuration; `compare` refuses it. |
| **Noise Floor** | Run-to-run variance of an *unchanged* SUT, measured by N repeated Runs. A difference between two Runs is reported as a change only if it exceeds the Noise Floor (bootstrap confidence interval). |
| **Baseline** | A pinned, named Run (per Suite, Language, SUT) that later Runs are compared against by the `compare` command. |
| **Threshold Sweep** | A Suite variant that varies a Mamemai calibration constant (e.g. upsert merge 0.93 / disambiguate 0.75) across a range and reports precision/recall at each value, to calibrate it from evidence rather than guess. |
| **Verdict** | The human decision taken *from* Scores (adopt the model, keep the threshold). Not stored as a Score; recorded in the relevant `docs/changes/` or eval PLAN entry. |
