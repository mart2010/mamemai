# Eval — Design

*Created 2026-10-11. Vocabulary: [GLOSSARY.md](GLOSSARY.md). Task tracking:
[PLAN.md](PLAN.md). Main-product spec: [../../docs/spec/](../../docs/spec/) (authoritative
for everything about Mamemai itself).*

## Purpose

Qualify and benchmark the complete Mamemai app, on demand and automatically: compare LLMs
(and versions of one LLM), embedding models, prompts and calibration thresholds; measure
conversation quality, knowledge/hallucination, semantic search, consolidation, and
end-to-end long-term memory — in **English and French**.

## Invariants (EV-INV)

| ID | Rule |
|---|---|
| **EV-INV-1** | **One-way dependency.** `eval/` may import `mamemai_server`; `server/`, `client/`, `setup/` never import anything from `eval/`. Enforced by a test in `eval/tests/` that scans those packages' sources. |
| **EV-INV-2** | **All evaluation lives in `eval/`.** Existing LLM quality gates under `server/tests/` migrate here (see PLAN, slice 2.5) and are then deleted from `server/tests/`. Correctness tests of server code stay in `server/tests/`. |
| **EV-INV-3** | **Ground truth before dialog.** A Ledger is written first; Dialogs are generated from it; facts are never extracted from Dialogs to build ground truth. |
| **EV-INV-4** | **The SUT is never its own Judge, simulator, or generator.** Those are the Reference LLM. |
| **EV-INV-5** | **Frozen means immutable.** A frozen Corpus version is never edited; a change is a new version. Runs cite the version. |
| **EV-INV-6** | **The SUT never writes to the durable `eval` schema or to real user schemas.** It writes only to a Scratch Schema. |
| **EV-INV-7** | **No bare pass/fail.** Every result is a Score with n, a Run Fingerprint, and (for comparisons) the Noise Floor. |
| **EV-INV-8** | **Every Reference LLM call is cached** by content hash and logged with model id. |
| **EV-INV-9** | **No real session content in `eval/`.** Real session logs are neither a ground-truth source nor a style source, and nothing from them is read, stored, or sent to the Reference LLM (decided 2026-10-11: they were recorded in unrepresentative sessions). Speech style comes from the two hand-specified Style Targets. |
| **EV-INV-10** | **No silent substitution of the Reference LLM.** The model id is pinned per call and recorded in the Run Fingerprint and cache key. Quota exhaustion stops the job at a checkpoint (resume from cache); it never switches model. `compare` refuses Runs with differing Reference LLM id (for judge-based Scores, also Rubric hash) and Runs tagged `non-comparable`. |

## Layout

```
eval/                      # 4th uv package: mamemai-eval
  pyproject.toml           # depends on mamemai-server (path dependency)
  docs/                    # GLOSSARY, DESIGN, PLAN (this folder)
  corpora/                 # exported frozen Corpora (jsonl), reviewed + versioned
  results/                 # exported Run summaries (jsonl)  [DB is the store; this is the diffable export]
  src/mamemai_eval/
    domain/                # Entities / VOs: SyntheticUserProfile, Ledger, LedgerFact, Scenario, Dialog,
                           #   Probe, Run, Score, RunFingerprint …  Pure, no I/O.
    usecases/              # Generate*, Freeze*, Execute*Suite, Compare, Sweep … + ports (abstract)
    suites/                # retrieval, consolidation, (later) conversation, hallucination, recall_gate, e2e_memory
    adapters/              # reference_llm (claude_cli / anthropic_api / ollama), eval_repository (postgres),
                           #   server_bridge (builds SUT use cases from mamemai_server), corpus_files
    cli.py                 # mamemai-eval run|generate|freeze|compare|sweep|export
  tests/                   # T0: unit tests with Fakes; test_no_reverse_dependency.py
```

Clean Architecture holds inside `eval/` exactly as for the product: domain → usecases (ports)
→ adapters → CLI. `mamemai_server` is reached **only** through `adapters/server_bridge`, which runs the SUT as a
subprocess under `server/.venv`'s interpreter (JSON over stdio; decided 2026-10-11), so the eval
domain never imports it and the server's heavy ROCm environment stays untouched.

## Persistence

- Database: a dedicated database **`mamemai_eval`** (peer auth, same mapping as `mamemai`/
  `mamemai_test`; decided 2026-10-11, D2; currently owned by the OS user's own database role, see PLAN findings) — separate from `mamemai_test`, which the server's tests rebuild.
- Integration tests use a separate database, **`mamemai_eval_test`** (EV-INV-6 in spirit: tests never touch durable ground
  truth; the fixtures refuse to truncate any database not named `*_test`).
- Durable schema **`eval`**: slice 0: `profile`, `ledger_fact`, `run`, `score`, `reference_llm_cache`; later slices add
  `scenario`, `dialog`, `dialog_turn`, `corpus`, `corpus_member`, `probe`, `calibration_item`.
  Own DDL + own migration step (not the server's).
- **Scratch Schema** `eval_run_<run_id>` per Run, built with the server's existing
  `connect(dsn, schema)` mechanism and schema bootstrap, dropped at Run end (kept on failure
  with `--keep-scratch`).
- Small Corpora and all Run summaries are also exported to `corpora/` and `results/` as JSONL so
  they can be diffed, reviewed in git, and rebuilt on another machine.

## Reference LLM

Port `ReferenceLLM.complete(system, messages, *, model, temperature, seed?) -> str`, adapters:

1. `claude_cli` — headless `claude -p --model <pinned>` (draws on the Claude Code subscription's usage limits). **Default.**
2. `anthropic_api` — Anthropic API (separately billed; needs a key). Not built until needed (judge-heavy slices).
3. `ollama` — harness smoke runs only; results tagged `non-comparable` (EV-INV-10).

Decided 2026-10-11 (D1). Because every call is cached (EV-INV-8) and quota exhaustion fails-and-resumes
(EV-INV-10), a Corpus is generated once and reused, and an interrupted job costs nothing for work already
done. Slices 1–2 need only generation, so call volume is small; budget becomes a real constraint at slice 3.

## Suites in scope for slices 1–2

### `retrieval` (slice 1) — semantic search

Corpus: a frozen Ledger rendered as memory items (event → episode, knowledge → concept, attribute → concept
until the product decides otherwise; with the spec's embedding text rules) plus Probes of kind single-fact (paraphrase), cross-lingual
(FR fact ↔ EN query and vice-versa), hard-negative, and abstention (no matching fact).
SUT: real `EmbeddingService` + real Postgres/pgvector `MemoryRepository` search in a Scratch
Schema, so index type and distance operator are part of what is measured.
Metrics: recall@1/3/5/10, MRR, nDCG@10, abstention precision at the production threshold(s).
Variants: **Threshold Sweep** over merge (0.93) and disambiguate (0.75) thresholds and the
recall similarity cut-off; HNSW vs exact scan (recall of the ANN index against exact).
Needs **no** Simulated User and no SUT LLM.

### `consolidation` (slice 2) — extraction and memory upkeep

Corpus: frozen multi-Scenario Dialogs per Profile, converted to the server's session-log
format and replayed through `TurnLogReplayer` → `ConsolidateMemory` in a Scratch Schema.
Metrics, each scored against the Ledger: extraction precision/recall by Fact Kind (events vs. episodes, knowledge vs. concepts; attributes: where they land, if anywhere);
hallucinated-item rate (stored items matching no Ledger Fact — the FR-307 post-check);
provenance correctness (FR-310: user-said vs assistant-said); merge/dedup accuracy across
Scenarios; Fact Revision handling (new value stored, old superseded, no duplicate);
worthiness-gate accuracy on Scenarios labelled worthy / not worthy.
SUT: the extraction/worthiness LLM + embedding model; the dialogs are frozen so the SUT LLM is
the only varying part.

## Corpus freezing

`corpus freeze` validates (every probe kind present, probe fact ids exist, cross-lingual probes in the other language, update
probes target revisions, no duplicate probe texts), then stores the Corpus and exports it to `corpora/<version>/` as four sorted
JSONL files plus `MANIFEST.json` (digest, counts, generating model, notes). The database refuses any later change to a member
Profile (trigger in `005_corpus.sql`). Fixes ship as a new version with new people (`--seed`), never as edits. `corpus verify`
and `corpus import` prove a database or another machine holds exactly the frozen content.

## Making the Reference LLM's dialogs human-like (frozen Corpora)

Two-stage generation, each stage cached and reviewable: (1) Profile + Ledger (structured JSON,
validated by schema and a consistency check — unique fact ids, no fact revealed in the wrong Scenario,
every Revision supersedes an earlier fact, no forks); (2) per Scenario, a Dialog written with the profile's
**Style Target** in the prompt (EV-INV-9) and **Style Conformance** checked afterwards: a Dialog whose measured
user side falls outside the target is regenerated, not frozen. STT-style noise is injected
deterministically afterwards (homophone/drop substitutions) so the noise level is a parameter.
Assistant turns in a frozen Dialog are Reference-LLM-written GA-style replies; consolidation
only reads the user side plus the assistant context, matching how the live path logs turns.
A manual review pass on a sample, plus the Calibration Set, gates freezing.

## Speech Archetypes

Each Eval Language gets two Synthetic User Profiles (D5), one per **Speech Archetype** — four in all (fr-hesitant, fr-fluent, en-hesitant, en-fluent), each a different fictional person. The same numeric ranges apply to both languages for now:

| Metric (over the user's turns) | hesitant | fluent |
|---|---|---|
| words per turn, median / p90 | 6–12 / 14–32 | 12–22 / 30–70 |
| fragments (≤ 3 words) | 20–40 % | 0–8 % |
| questions | 15–35 % | 15–35 % |
| turns with no final punctuation | 30–60 % | 0–10 % |
| fillers per 100 words | 4–9 | 0–1 |
| turns containing an ellipsis | 25–50 % | 0–4 % |
| turns with an immediate repeated word | 10–30 % | 0–2 % |
| turns starting in lowercase | 15–40 % | 0–5 % |

The numbers are **design targets, not measurements** (`domain/style.py` is the source of truth; this table
mirrors it). They describe the text the assistant receives *after* STT, which tends to clean up some
disfluencies, so they will be revisited against the Calibration Set and, later, a TTS→STT round trip.
Why two: the same facts must survive both a user who gives them in one clean sentence and one who
circles, restarts and trails off — extraction and recall are expected to behave differently on each.

## Subjects

*Knowledge Domains* (hallucination, later slices): astronomy, weather & climate, geography,
AI/ML, history of science, biology/nature, space exploration. Proposed additions: cooking &
food science, music, basic medicine/health literacy (answers must be safe), personal finance
basics, European history, French cultural/administrative topics (for the French runs).
*Life Domains* (personal Ledger Facts): family & friends, work & career, travel, health &
fitness, cooking, home & DIY, hobbies (astronomy observing, hiking, photography), finances,
upcoming events.

## Spec relationship

Eval is development tooling, not product behaviour: its requirements live here (`EV-INV-*`,
and `EV-nnn` per Suite as they are written), not in `docs/spec/`. A Suite that *measures* a
spec requirement cites its ID in its docstring (same convention as tests). When a Verdict changes
a product constant, the change goes through the normal spec loop in `docs/spec/`.
