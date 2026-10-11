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
- [ ] S1.5 `retrieval` Suite: build memory items from Ledger in a Scratch Schema via the real embedding + repository; metrics recall@k / MRR / nDCG / abstention
- [ ] S1.6 Baseline Run: current embedding model (e5), en and fr; measure Noise Floor (N repeats; mostly deterministic, confirm)
- [ ] S1.7 Threshold Sweep for merge/disambiguate/recall cut-offs → feeds BACKLOG "Calibration" items (Verdicts recorded there)
- [ ] S1.8 ANN-vs-exact recall check (HNSW vs exact scan)
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
