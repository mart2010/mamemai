# Eval — Plan

*Started 2026-10-11. Read first in any session touching `eval/`, with
[DESIGN.md](DESIGN.md) and [GLOSSARY.md](GLOSSARY.md). Markers: `[ ]` not started,
`[~]` in progress, `[x]` done — update in place. Dated findings go at the bottom.*

## Decisions taken (2026-10-11)

- Everything evaluation-related lives in `mamemai/eval/`; the product never depends on it (EV-INV-1).
- Persisted ground truth (profiles, ledgers, scenarios, dialogs, runs) lives in a dedicated `eval`
  schema; the SUT writes only to throwaway scratch schemas.
- Languages: English and French (results never averaged across languages).
- Reference LLM (generator/judge): Claude via headless `claude -p` within the subscription (D1). Pinned model id; **no silent fallback** to another model (EV-INV-10); on quota exhaustion a job stops at a checkpoint and resumes later from the cache.
- Database: separate `mamemai_eval` DB (D2). Corpus of record: DB + JSONL export in git (D4). First corpus size: 2 Profiles × 60 facts × 6 Scenarios per language (D5).
- Real session logs are **not used** for anything in `eval/` (EV-INV-9, 2026-10-11). Speech style = two Speech Archetypes (hesitant, fluent) with hand-specified Style Targets; knowledge/life subjects per DESIGN §Subjects.
- Scope now: slices 1–2. Existing server quality gates are migrated here and removed from
  `server/tests/` (slice 2.5).

## Open decisions (need the user before the task they block)

| ID | Decision | Recommendation | Blocks |
|---|---|---|---|
| D1 | ~~Reference LLM transport~~ **DECIDED 2026-10-11:** `claude -p`, pinned `--model`, cached, fail-and-resume, never substitute another model mid-comparison. Ollama adapter exists for harness smoke runs only; its output is tagged `non-comparable`. Revisit API adapter before slice 3 (judge-heavy volume). | — | — |
| D2 | ~~Database~~ **DECIDED:** separate `mamemai_eval` DB — created 2026-10-11, owned by the OS user's own database role (see findings). | — | — |
| D3 | ~~Real logs as style anchors~~ **SUPERSEDED 2026-10-11:** the user withdrew it — the logs were recorded in unrepresentative sessions. Replaced by two Speech Archetypes with hand-specified Style Targets (EV-INV-9). | — | — |
| D4 | ~~Corpus storage~~ **DECIDED:** DB working store + `corpora/*.jsonl` exports in git. | — | — |
| D5 | ~~Corpus size~~ **DECIDED:** per language, two Profiles (one `hesitant`, one `fluent`) × 60 facts × 6 Scenarios. Total 4 Profiles (fr-hesitant, fr-fluent, en-hesitant, en-fluent), 240 facts, 24 Scenarios, ≈ 300 probes. The en and fr Profiles are different fictional people, not translations. | — | — |

## Slice 0 — Scaffold

- [x] S0.1 `eval/` uv package (`mamemai-eval`, path dep on `server`), ruff config coverage, `mamemai-eval` CLI stub
- [x] S0.2 `eval` schema DDL + migration step; create `mamemai_eval` DB *(D2)*; scratch-schema helper over the server's `connect(dsn, schema)`
- [x] S0.3 `ReferenceLLM` port + adapters (`claude_cli` first; `anthropic_api` later; `ollama` smoke-only, `non-comparable`) + `FakeReferenceLLM` + content-hash cache + checkpoint/resume on quota exhaustion *(D1)*
- [x] S0.4 `test_no_reverse_dependency.py` (EV-INV-1)
- [x] S0.5 Domain entities/VOs: Profile, Ledger, LedgerFact, FactRevision, Scenario, Dialog, Probe, Run, Score, RunFingerprint; `FakeEvalRepository`
- [x] S0.6 Add eval terms cross-link from `docs/spec/GLOSSARY.md` header (one line, no redefinition)

## Slice 1 — Retrieval suite (no Simulated User, no SUT LLM)

- [x] S1.1 Speech Archetypes (`hesitant`, `fluent`) with Style Targets, `compute_style` measurement and `violations` check (`domain/style.py`); `archetype` on the Profile entity and `eval.profile`
- [x] S1.2 (**done: four ledgers, frozen in Corpus v1**) `GenerateProfileAndLedger`: per language one `hesitant` + one `fluent` Profile (60 facts each), schema validation + consistency checks *(D5)*
- [x] S1.3 `GenerateProbes` from Ledger: paraphrase, cross-lingual, hard-negative, abstention (+ update, aggregation) — 291 probes
- [x] S1.4 Review pass → `FreezeCorpus` v1 (en, fr) + JSONL export *(D4)* — frozen 2026-10-11, digest `3b0fbff76f68`
- [x] S1.5 `retrieval` Suite: build memory items from Ledger in a Scratch Schema via the real embedding + repository; metrics recall@k / MRR / nDCG / abstention
- [x] S1.6 Baseline Run: current embedding model (e5), en and fr; measure Noise Floor (N repeats; mostly deterministic, confirm)
- [x] S1.7 Threshold Sweep for merge/disambiguate/recall cut-offs → feeds BACKLOG "Calibration" items (Verdicts recorded there)
  - **Sensitivity check (agreed 2026-10-16):** repeat the sweep under a second concept-naming rule (e.g. sentence only, no name) and report whether the Verdicts move; if they do, escalate to Reference-LLM-written names. Optional extra rendering variant: SPO triple text (see findings 2026-10-16, storage-form question)
- [x] S1.8 ANN-vs-exact recall check (HNSW vs exact scan)
- [ ] S1.9 `compare` and report (table + deltas), `export` of Run summaries

## Slice 2 — Consolidation suite

- [ ] S2.1 `GenerateScenarios` from Ledger (reveal / revise / omit / worthy-or-not agenda)
- [ ] S2.2 `WriteFrozenDialog` (Reference LLM, prompted with the profile's Style Target; regenerate until Style Conformance holds) + deterministic STT-noise injector
- [ ] S2.3 Dialog → server session-log format adapter; Calibration Set: a small human-labelled realism check of synthetic dialogs, and review of the Style Targets themselves
- [ ] S2.4 Freeze Corpus v2 (multi-Scenario dialogs, en + fr)
- [ ] S2.5 `consolidation` Suite via `TurnLogReplayer` in a Scratch Schema; metrics per DESIGN
- [ ] S2.6 Baseline Run for the current default model(s) (`llama3.1:8b`, plus `mistral-nemo` if installed); Noise Floor (LLM is non-deterministic: N ≥ 3)
- [ ] S2.7 Findings → docs/changes entries for any product defect found (e.g. FR-307 naming dedup, FR-310)

## Slice 2.5 — Migrate existing quality gates (EV-INV-2)

- [ ] S2.5.1 Port `test_worthiness_llm_quality_gate.py` (45 cases) into a `worthiness` Suite; reproduce 40/45 as a parity check
- [ ] S2.5.2 Port `test_tutor_llm_quality_gate.py` into a `tutor` Suite; parity check
- [ ] S2.5.3 Delete both from `server/tests/integration/`; fix the stale `server/tests/e2e/` mention in `mamemai/CLAUDE.md` and any spec citations
- [ ] S2.5.4 Review other `server/tests/integration/` files; only quality/benchmark-style tests move, correctness tests stay

- [ ] S2.8 `brief` Suite: after the consolidation replay, run `GenerateMemoryBrief`; coverage / staleness / invention scores

## Later slices (not scheduled)

- Slice 3 — `conversation` + `hallucination` Suites; live Simulated User; Judge + Rubrics + Calibration Set; T2.
- Slice 4 — `e2e_memory` (LongMemEval-style: plant over N Scenarios, consolidate, probe later; categories single-fact / aggregation / update / abstention); `recall-gate` Suite; T3 soak.
- Slice 5 — nightly runner, Baseline pinning, regression gate on model upgrades; STT/TTS/VAD/latency suites (absorbs `TR-957-vad-benchmark-remeasure` if applicable).

## Findings log

**2026-10-11 — slice 0 done.** 40 tests green (`cd eval && uv run pytest`): domain invariants, Reference
LLM cache + quota stop/resume, `claude -p` adapter (fake runner), Fake/Pg repository contract, scratch-schema
lifecycle, EV-INV-1 guard (verified to fail when `server/` imports `mamemai_eval`). One live smoke call through
`ClaudeCliReferenceLLM` → Pg cache worked (haiku, ~1 s, second call served from cache).

- **DB owner is the OS user's own role, not `mamemai`.** `pg_hba.conf` grants role `mamemai` access per database
  (`mamemai`, `mamemai_test` only), and editing `/etc/postgresql/18/main/pg_hba.conf` is a system change, so
  `mamemai_eval` is owned by the OS role (superuser, covered by the generic `local all all peer` rule). pgvector
  installed `WITH SCHEMA public`. If a `mamemai_eval`/`mamemai` hba line + `mamemai_map` entry is added later,
  `ALTER DATABASE mamemai_eval OWNER TO mamemai` and set `MAMEMAI_EVAL_DATABASE_URL`.
- **`claude -p` invocation** that works under subscription auth: `--model <pinned> --output-format json
  --system-prompt … --tools "" --no-session-persistence --setting-sources "" --disable-slash-commands`, prompt on
  stdin, throwaway cwd. `--bare` is NOT used (skips keychain → subscription OAuth). JSON carries `result`,
  `is_error`, `api_error_status`, `modelUsage` (answering model — checked against the pin). Temperature cannot be
  set via the CLI, so `ClaudeCliReferenceLLM` rejects it. Quota-exhaustion detection is by `api_error_status == 429`
  or message regex; **the real quota-exhausted output has not been observed yet** — verify when it first happens.
- **SUT venv — DECIDED 2026-10-11: bridge runs as a subprocess under `server/.venv`'s interpreter** (option b).
  `eval/` keeps its light core and no `sut` extra install; `adapters/server_bridge` launches
  `server/.venv/bin/python -m <bridge entrypoint>` and talks JSON over stdio. The server environment stays
  untouched (no torch drift, no second 6.8 GB install). The bridge entrypoint lives in `eval/` and imports
  `mamemai_server` only inside that subprocess; EV-INV-1 still holds (server never imports eval).
- **Reference model — DECIDED 2026-10-11:** everyday conversation and common knowledge don't need a top-tier
  model. Default pin `claude-sonnet-5-5` (generation, later judging); `claude-haiku-5-5` for smoke runs.
  Configured via `MAMEMAI_EVAL_REFERENCE_MODEL` / `MAMEMAI_EVAL_SMOKE_MODEL`. Changing the pin starts a new
  comparability line (EV-INV-10) — judge-based Scores must be re-baselined.

**2026-10-11 — S1.1 revised (supersedes the first S1.1 attempt).** The first attempt built tooling around the
user's real session logs (statistics + a scrubbed Style Anchor Pack). The user then withdrew the logs as a basis —
they were recorded in unusual sessions, not typical future use — and asked instead for **two profiles with
distinct speech styles: one hesitant (fillers, pauses), one steady and regular**. All real-log code (log reader,
scrubber, anchor files, `style` CLI) and the generated candidate file were deleted; nothing from the logs was
ever sent to the Reference LLM, and no raw turn was displayed in the session. Replaced by `SpeechArchetype` +
`StyleTarget` (design targets, see DESIGN §Speech Archetypes) + Style Conformance. This also removes the English
style-source problem (the logs held only 5 en turns). 45 tests green. Caveat recorded: targets describe
*transcribed* text and STT tends to clean fillers; revisit with a TTS→STT round trip.

**2026-10-11 — filler lexicon narrowed.** Counting ambiguous discourse words (`alors`, `bon`, `du coup`, `so`,
`well`, `like`) as fillers would have made fluent speakers look hesitant, since they open sentences with them.
Only unambiguous fillers are counted now (fr: euh, heu, ben, bah, hum, hmm; en: um, uh, er, erm, hmm, you know,
I mean). Ranges are shared across languages for now; en and fr samples both conform. If Style Conformance data
from S2 shows systematic language differences, `STYLE_TARGETS` becomes keyed by (archetype, language).

**2026-10-11 — S1.2 French ledgers drafted.** `mamemai-eval generate ledger --language fr --archetype hesitant|fluent`
(Sonnet 5.5, `claude -p`, ≈ 95 s each, first attempt valid for both, so 2 Reference LLM calls in the cache).
Review files (gitignored): `eval/drafts/ledger_fr_hesitant.md` (**Odile**, 66, retired teacher, Quimper: 58 facts =
23 event / 23 attribute / 12 knowledge, 4 revisions) and `eval/drafts/ledger_fr_fluent.md` (**Julien**, 34, developer,
Toulouse: 62 facts = 25 / 24 / 13, 6 revisions incl. one chain f28→f34→f59). Spot-checked: dates precede their scenario,
knowledge facts correct (Curie's two Nobels, Moon recession 3.8 cm/yr, Garonne source, Perséides/Swift-Tuttle, …).
Weak spots to judge in review: many facts share the template "<Prénom> a …" (realistic for the product's own memory
text, but makes retrieval harder than varied text — a feature for S1.5, not a bug); domain spread is uneven
(hesitant: 14 `loisirs`, 1 `travail`; fluent the reverse), acceptable for now; no ledger fact is a *procedure* by design.
- **Fact Kind replaces the product memory type in the Ledger** (design correction made before any data was kept):
  the live extractor yields only episodes (dated personal events) and concepts (knowledge learned/discussed) —
  never procedures (FR-307) — so ledger facts are `event | attribute | knowledge`, mapped event→Episode,
  knowledge→Concept; migration `002_fact_kind.sql`.
- **Where do stable personal facts (`attribute`) live? — corrected 2026-10-11 after the user pointed at the memory brief.**
  The memory brief (FR-109/FR-308, `GenerateMemoryBrief`) IS the product's user summary: injected into every session's
  system prompt, regenerated offline after each consolidation run. But it is not maintained by the extractor: it is
  rebuilt from scratch each time, only from the 20 most recent episodes + the last 40 *organic* concepts
  (`DEFAULT_BRIEF_EPISODES/CONCEPTS`), summarised by the LLM "based only on the material", and not written at all when
  there is no material. There is **no separate store for stable facts**: an attribute reaches the brief only if the
  extractor saved it as a concept/episode (the prompt asks for "facts or knowledge the user learned or discussed" —
  self-descriptions are not clearly covered) and only while it stays within the recency window. Consequence for eval:
  attributes are scored mainly through the **brief** (new `brief` Suite: coverage of current facts, stale-value
  rate for superseded ones, invention rate, effect of the recency cap), and secondarily by where the extractor puts
  them. Whether the product should extract self-descriptions explicitly, or keep a persistent user profile instead of
  a pure projection, is a product decision for a `docs/changes/` proposal once data exists — not worked around here.

**2026-10-11 — measure-first agreed; key_terms added; test-DB incident.**
- User agreed to **measure first** on the memory brief: eval data is the test bench; baseline today's `GenerateMemoryBrief`,
  then compare redesigns A/B/C on identical Ledgers. Change folder `docs/changes/memory-brief-rebuild/` opened (proposal
  + tasks; PLAN row). The `brief` Suite (S2.8) therefore moves up: it follows S2.5 directly, ahead of the other S2.x.
- `LedgerFact.key_terms` (1–3 strings that must literally occur in the fact text; validated by the domain; required by
  generation) — migration `003_key_terms.sql`. The fr ledgers were regenerated with it (new prompt → new Reference LLM calls):
  **Mireille** (fr/hesitant, 60 facts: 23 event / 24 attribute / 13 knowledge, 6 revisions) and **Mathieu** (fr/fluent,
  60 facts: 23 / 24 / 13, 5 revisions). They replace the earlier Odile / Julien drafts (never reviewed). Review files:
  `eval/drafts/ledger_fr_*.md` — **still awaiting the user's review.** Key terms are mostly distinctive (names, places,
  amounts); a handful are weak ("cheville", "prise de sang") — acceptable, the Judge covers ambiguous cases.
- **INCIDENT (fixed): the integration tests were truncating the durable `mamemai_eval` database.** Each `pytest` run
  emptied the freshly generated profiles, ledgers and Reference LLM cache (noticed because the cache held 2 rows instead of 4).
  Exactly what D2's separate DB was meant to prevent, defeated by pointing the tests at it. Fix: integration tests use
  `mamemai_eval_test` (created on demand, `MAMEMAI_EVAL_TEST_DATABASE_URL`), and the fixtures refuse to truncate any
  database whose name does not end in `_test`. Verified: durable data intact across a full test run.

**2026-10-11 — English ledgers drafted.** `generate ledger --language en --archetype hesitant|fluent` (Sonnet 5.5). Added two
en briefs (distinct from the fr pair in age, gender, job, interests) and made the knowledge-fact phrasing language-aware
(`learned that …` / `a appris que …`; it was French-only). Results: **Hannah** (en/hesitant, 41, part-time bookkeeper,
Manchester: 60 facts = 26 event / 21 attribute / 13 knowledge, 4 revisions) and **Graham** (en/fluent, 52, civil engineer,
Bristol: 60 = 27 / 20 / 13, 4 revisions). 7 Reference LLM calls in the cache in total = 4 first answers + **3 repair rounds**
(all on the English side; the validate-and-repair loop worked on real output). Review files: `eval/drafts/ledger_en_*.md`.
Facts spot-checked correct. Known weaknesses, for the review:
- **Domain slugs are French identifiers in the English ledgers** (`astronomie`, `meteo_climat`, `sante_sport`). They are
  language-neutral labels, not text, and renaming means regenerating all four; deferred unless the user objects.
- **Knowledge facts repeat across profiles** (Moon drifting 3.8 cm/yr, Perseids/Swift-Tuttle, Jupiter's Great Red Spot, Mount
  Everest, equinox, tides appear in 2–3 of the 4). Fine for retrieval (one Scratch Schema per profile) but thin diversity for a
  future knowledge/hallucination Suite; fix then by passing an "already used topics" list into the prompt.

**2026-10-11 — S1.3 probes generated.** `mamemai-eval generate probes [--language L --archetype A]`; **291 probes**: fr/hesitant 74,
fr/fluent 73, en/hesitant 72, en/fluent 72 — per profile 28 single_fact, 14 hard_negative, 4–6 update (one per current revision),
4 aggregation (2–4 facts each), 8 abstention, 14 cross_lingual (translations of single_fact queries into the other language).
Review files (gitignored): `eval/drafts/probes_<lang>_<archetype>.md`. 29 Reference LLM calls in the cache in total.
- **Design:** the plan (which facts, which distractor) is deterministic and seeded; the Reference LLM only words the queries, in the
  user's first-person spoken voice. Hard negatives = a same-domain, same-kind current fact; update probes list the older facts of the
  revision chain as negatives. **No query may contain the key terms of its expected fact** (checked; violations are sent back for
  repair) — otherwise retrieval would be a lexical lookup, not a semantic test. **Abstention questions are verified by a separate
  Reference LLM call** (against all facts, including outdated ones); answerable candidates are dropped and more are requested.
- **Hesitant profiles' probes carry the profile's own fillers in about half the queries** (fr `euh`/`ben`, en `um`/`uh`); first
  run had English fillers in French queries, fixed in the prompt. Fluent profiles' probes: none.
- Persisted in `eval.probe` (migration `004_probe.sql`); `GenerateProbes` has 6 unit tests with a scripted Fake.
- **Known limit:** the key-term leak check is lexical; a paraphrased giveaway ("the city on the Odet") would pass. The S1.5 baseline
  will show it as suspiciously perfect recall on some probes, which is the signal to tighten.

**2026-10-11 — Corpus v1 frozen (S1.4).** `mamemai-eval corpus freeze --version v1` → 4 profiles, 240 facts, 291 probes
(112 single_fact, 56 hard_negative, 56 cross_lingual, 32 abstention, 19 update, 16 aggregation); digest
`3b0fbff76f681f6adeccef46f39b4a72fe396c69650ffa877d47f49ccc79bdfa`; generated with `claude-sonnet-5-5`. Exported to
`eval/corpora/v1/` (MANIFEST.json + profiles / ledger_facts / scenario_dates / probes `.jsonl`) — **to be committed by the user**.
- **Review status recorded in the manifest notes:** user read the fr ledgers (fine), skimmed the en ledgers (ok); probes were
  *not* individually reviewed (only the generation checks). If the baseline shows bad probes, fix = a new version (v2), not an edit.
- **Immutability is enforced by the database** (migration `005_corpus.sql`): a trigger refuses INSERT/UPDATE/DELETE on a Profile, its
  facts, scenario dates and probes once the Profile belongs to a corpus (EV-INV-5). Verified on the real DB (raw `UPDATE` refused).
  The repository turns it into `FrozenCorpusError`; `generate … --force` on a frozen profile now says so. A new Corpus version needs
  new people: `generate ledger/probes … --seed N`.
- `corpus verify --version v1` recomputes the digest from the database; `corpus import <dir>` rebuilds a corpus from the exported
  files on another machine / wiped DB after checking the digest (both tested; integration tests show export → empty DB → import
  gives an identical, verified corpus). `corpus list` lists versions.
- Canonical form: four JSONL files, rows and keys sorted, UTF-8; digest = sha256 over names + bytes. 94 tests green.

**2026-10-16 — S1.5 + S1.6: `retrieval` Suite built, baseline measured.** `mamemai-eval run retrieval --corpus v1
[--language L] [--repeat N] [--keep-scratch] [--device D]`; 102 tests green (7 unit with `FakeRetrievalSut`, 1 integration
against the real model). Design as agreed: `domain/retrieval.py` (metrics + `score_retrieval`, pure), `usecases/retrieval.py`
(`RetrievalSut` port, `ExecuteRetrievalSuite`), `adapters/server_bridge.py` (client) + `adapters/sut_worker.py` (runs under
`server/.venv`, the only file importing `mamemai_server`; JSON lines on stdio, fd 1 re-pointed at stderr so library output
cannot corrupt the protocol). One Scratch Schema per Profile; one Run per language (both archetypes pooled). Per-Probe
`Hit`s are kept in `RetrievalOutcome` so the S1.7 sweep can re-score without re-embedding. Runs are saved to `eval.run`.
- **Baseline (Run per language, corpus v1, e5-large on CPU, HNSW cosine, cut-off 0.84, git `0aa7cd5`+dirty):**

  | | en (n=128) | fr (n=131) |
  |---|---|---|
  | recall@1 / @5 / @10 | 0.900 / 0.984 / 1.000 | 0.919 / 0.993 / 0.995 |
  | MRR / nDCG@10 | 0.962 / 0.968 | 0.972 / 0.974 |
  | **gated_recall@5** (survives the 0.84 floor) | **0.438** | **0.626** |
  | abstention_correct (n=16 each) / mean top-1 sim | 1.000 / 0.762 | 1.000 / 0.790 |
  | update_current_first (n=8 / 11) | 0.500 | 0.636 |
  | aggregation recall@5 (n=8 each) | 0.750 | 0.885 |
  | single_fact, cross_lingual, hard_negative recall@1 | ≈ 1.0 (ceiling) | ≈ 1.0 (ceiling) |

- **Noise Floor = 0.** Three repeats per language gave bit-identical Scores: retrieval is deterministic (embedding on CPU +
  HNSW with a fixed insert order). Any difference between two Runs of this Suite is therefore real, not noise — until the
  device changes (CPU vs GPU embeddings differ in the last digits; `embedding_device` is in the fingerprint).
- **Finding 1 — the 0.84 floor discards most correct answers on this corpus** (gated recall 44 % en / 63 % fr) while
  abstention is already perfect (every unanswerable probe tops out below 0.84, mean 0.76–0.79). So on this data the floor looks
  stricter than needed for honesty, and stricter for English than French, the "per-language band" limit TR-314 already
  records. Caveat before acting: stores here hold 60 items, the live store holds thousands, and the Ledger texts are
  template-like. **S1.7's sweep is where this becomes a Verdict**; nothing is to be changed in the product before it.
- **Finding 2 — the corpus is too easy to discriminate on single_fact / cross_lingual / hard_negative** (≈ 1.0, with 60-item
  stores). They remain valid regression guards, but model comparisons will be decided by gated recall, aggregation, update and
  the similarity distributions. A larger or noisier store (distractor items from the other three Profiles, or a v2 corpus) is the
  lever if an embedding comparison needs more resolution — an S1.7 decision, not made here.
- **Finding 3 — update Probes retrieve the stale fact first about half the time** (en 0.50, fr 0.64): the old and new value
  embed almost identically. Retrieval alone cannot fix this; it is the case consolidation's Fact Revision handling (S2.5) must
  cover, and one the memory-brief rebuild already cares about.
- **Measurement choices to keep in mind:** concept names are `key_terms[0]` (the Ledger has no names; recorded in the
  fingerprint as `rendering=concept-name=first-key-term`) — a different naming rule is a new comparability line;
  `attribute` facts are stored as concepts, as the PLAN already says, without a product decision behind it; the worker
  searches episodes + concepts only (procedures are never extracted live, FR-307).
- **Environment:** the server venv's torch is a CUDA build (`2.12.0+cu130`, `cuda.is_available() == False`) on this AMD
  machine, so embeddings ran on CPU (~16 s model load + index for 120 items, ~30 s per language-Run; GPU not needed for this
  Suite). I did not touch the server venv (a ROCm torch reinstall is the memory's known `uv sync` trap).
- Bug caught on first live run, now covered by the integration test: `PSMemoryRepository.search` already returns
  similarity (`1 − distance`); the worker had converted a second time and every ranking came back reversed.

**2026-10-16 — S1.7 scope extended; storage-form question raised.** The user kept `concept-name=first-key-term` for the baseline
and asked for a naming-sensitivity check inside S1.7 (added above). The user also asked whether personal facts should be stored as
subject–predicate–object triples instead of reusing the Concept model, which was designed for larger knowledge items. Not decided;
the Suite's rendering axis (`RENDERING` in the fingerprint) is where a triple variant would be measured first, on identical Ledgers.

**2026-10-16 — S1.7 done: Threshold Sweep + naming sensitivity.** `mamemai-eval sweep retrieval --corpus v1 [--language] [--renderings
first-key-term,bare]` (≈ 4.5 min on CPU for 2 languages × 2 renderings); full tables in `eval/results/sweep_retrieval_v1.md`
(tracked). It re-scores stored similarities: recall cut-off over the Probe outcomes, and merge/disambiguate over labelled
passage↔passage pairs (SAME = fact vs. its 2 paraphrases, 480/language; REVISION = old↔new, 8 en / 11 fr; DISTINCT = every other
pair of the same memory type, ≈ 1800/language). 108 tests green. **Nothing in the product was changed**; these are Verdicts for the
spec loop, with the caveats below.
- **Verdict 1 — recall floor (0.84, FR-309/TR-314).** The lowest cut-off that still refuses every unanswerable probe is
  **0.787 en / 0.811 fr** (naming rule A) and 0.781 / 0.807 (rule B). At 0.84 gated recall is 44 % / 63 %; at the safe point it is
  96 % / 83 %. So on this corpus 0.84 is ≈ 0.05 stricter than abstention safety requires, more so for English — the
  per-language-band limit TR-314 already documents, now with numbers. A language-aware floor of about 0.80 (en) / 0.82 (fr), i.e. safe
  cut-off + margin, would recover most answers. Evidence weak in two ways: only 16 abstention probes per language (the "safe" point
  is the max of 16 samples), and 60-item stores (a live store has thousands of neighbours, which pushes unanswerable similarities up).
  → input to `docs/changes/FR-309-language-recall-floor`; re-measure on a larger store before changing the constant.
- **Verdict 2 — merge threshold 0.93 (TR-602): paraphrases are fine, but it is slightly too low.** Paraphrases score 0.93–1.00
  (p10 0.97), so 100 % merge at 0.93. But ≈ 0.2 % of distinct-fact pairs also reach 0.93 and are silently fused (about 4 pairs en
  of 1757); the **lowest merge threshold fusing no distinct pair is 0.946 en / 0.949 fr**. At 0.95 only 0.8 % of paraphrases
  fall back to the LLM path. Cheap to raise (≈ 0.95) with a small cost; the fused-facts error is the dangerous one.
- **Verdict 3 — disambiguate threshold 0.75 (TR-602) is effectively "always ask".** Distinct pairs have p10 0.76 en / 0.83 fr and
  median 0.81 / 0.86, so **95 % (en) – 100 % (fr) of distinct pairs land in the LLM-disambiguation band**; same-domain distinct pairs
  go up to 0.946. A value that separates would sit around 0.90, but that also swallows revisions (0.88–0.93). Caveat: all-pairs
  overstates live cost (upsert only compares the best of a top-5), and the Ledger texts share the template "<Prénom> a …" which lifts
  every similarity. The BACKLOG's "hypernym vs same-name-different-domain at 0.86–0.87" concern is consistent with this.
- **Revisions sit in the middle** (0.85–0.96): below 0.93 they all go to the LLM path today (good: the old value must be replaced
  deliberately), but raising merge to ≈ 0.95 keeps them safely out of auto-merge, whereas lowering it below 0.92 would auto-merge them.
- **Sensitivity to the concept-naming rule (agreed check): the Verdicts do not move.** Safe cut-off 0.787 → 0.781 (en), 0.811 → 0.807
  (fr); safe merge 0.946 → 0.946 / 0.949 → 0.949; paraphrase and distinct distributions shift by < 0.02. What does change is the
  *height* of the gated-recall curve at a fixed floor (en at 0.84: 44 % → 37 %), i.e. including the name raises similarities by ≈ 0.01–0.02
  — so naming affects the absolute numbers, not the calibration conclusions. Reference-LLM-written names are therefore **not** needed
  for S1; revisit only if a Verdict is about to change a constant. The SPO-triple variant stays optional and unbuilt.
- Method note: both sweeps embed with `embed_passage` on both sides, as the upsert path does (TR-601); the recall sweep uses
  `embed_query` against passages, as live recall does.

**2026-10-16 — S1.8 done: HNSW vs exact scan.** `mamemai-eval run ann --corpus v1` (suite `ann_vs_exact`, saved to `eval.run`); per Eval
Language one store of 360 items (both Profiles, each fact + its 2 paraphrases = near-duplicates, the hard case for an ANN index),
queried with all Probes (144 en / 147 fr) at depth 10 through the index and by brute force. 108+ tests green (unit tests with the Fake;
guards below).
- **Result: with pgvector's default `hnsw.ef_search` (40) the index returns exactly the exact top-10 for every query** — overlap@10 =
  overlap@5 = top-1 agreement = 1.000, zero queries with any miss, in both languages. At this scale the HNSW index costs no recall.
- **Positive control:** `ef_search = 1` drops overlap@10 to 0.124 (en) / 0.134 (fr), so the check can see misses; a perfect score is not
  an artifact. (Top-1 still agrees 85 %: the graph entry point is near.)
- **Limit of the evidence:** 360 items per store, one persona. HNSW degradation appears with size, not here. Two production-relevant risks
  are therefore **unmeasured**: (a) recall at thousands of items; (b) **filtered search** — the product's concept search filters on
  `persona_id`, which pgvector applies *after* the index returns `ef_search` candidates, so a persona with few concepts inside a store
  full of another persona's (e.g. a tutor bundle with hundreds of vocabulary items) can get fewer than `top_n` rows, or none, even though
  matches exist. The ef_search = 1 control shows the mechanism (overlap collapses once candidates are too few after filtering). A
  multi-persona store check is the natural S1.8b; recorded in BACKLOG, not built.
- **Method bug found and fixed (affects how to read S1.5):** on freshly loaded tables Postgres has no statistics until autovacuum runs, so
  whether a search used a sequential scan or the HNSW index was arbitrary and could change mid-run. The worker now `ANALYZE`s after
  indexing and the SUT takes an explicit access path (`planner` = what production would do; `index` = force HNSW; `exact` = force the scan).
  `plan` evidence is recorded in the fingerprint and the check refuses to run if the "index" path is not an index scan or the "exact" path is
  not a sequential scan (vacuous comparison). With 60-item stores the planner picks a sequential scan, so the S1.5 baseline is in effect
  an exact search; **re-ran it after the fix: every Score identical**.
