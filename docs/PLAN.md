# PLAN.md — Mamemai Implementation Plan

## Status Legend
- `[ ]` not started
- `[~]` in progress
- `[x]` done

## Where the project stands (2026-07-14)

Phases 1–13 are implemented and verified: domain + service layers, all infrastructure
adapters (Postgres+pgvector, faster-whisper, Ollama/OpenRouter, Kokoro, e5 embeddings),
the full WebSocket server, offline consolidation pipeline, MemoryBrief generation and
injection, the install wizard (incl. model downloads and re-run pre-fill), the config
placement / persona-lifecycle refactor, persona extension ports, the bundle format +
`InstallPersonaBundle`, the Italian language-tutor persona (selection / assessment /
enrichment strategies, two-teacher cast via per-segment language detection), and the
installed-languages contract + GA response-language mirroring.

The full task-by-task record — findings, live-verification notes, benchmark numbers,
rejected alternatives — is archived in
[docs/archive/PLAN_phases_1-13.md](archive/PLAN_phases_1-13.md); references elsewhere
in the repo (code comments, briefs, tests) to "PLAN.md Phase N" resolve there. The spec
([docs/spec/](spec/)) is the present-tense description of behaviour; this file only
tracks work still to do.

---

## Next up

### Live session 2026-08-03 — 16 turns, French, astronomy

One monitored GA session on the workstation: **12-core / 24-thread x86 APU, ~46 GB
shared RAM, integrated AMD GPU** (unified memory — ~38 GB of it addressable by the GPU).
The LLM runs on that GPU via ROCm; **STT, TTS and the embedding model all run on CPU**
and contend for the same 24 threads. Only one of those three has to (see the GPU entry
below), so every number here is a contended-CPU number — and the contention is partly
self-inflicted, which is the point. 16 user turns, 435 user words, 32 turns total
(conversation 23).

Measured, all turns:

| | median | max | first → last turn |
| --- | --- | --- | --- |
| STT | 4.84s | 13.15s | — |
| LLM first token | — | 5.15s | 2.16s → 5.15s |
| total turn | 22.71s | 35.38s | — |

STT is **1.76× TR-957's benchmarked 2.75s**, consistently, not occasionally. The 13.15s
outlier is *not* explained by utterance length (a 309-char utterance took 5.18s, the
253-char outlier took 13.15s; per-character cost ranges 1.7–5.6 s/100ch with no trend) —
recorded as unexplained rather than guessed at, since the LLM stage spiked on the same
turn, which points at machine-level contention.

- [x] **The extraction cliff is a context-window overflow (2026-08-03, FR-307/TR-951/
      TR-952)** — measured after the entry below, and it supersedes it: there is no
      "length cliff" in the model's attention at all. `num_ctx` defaults to **4096** and
      Ollama silently truncates anything longer, so the model answers about a transcript
      it was never shown and returns empty arrays. Nothing errors and nothing warns.

      Decisive test, same transcript, only the context changed:

      | conv 22 slice | transcript | `num_ctx=4096` | `num_ctx=16384` |
      | --- | --- | --- | --- |
      | first 48 turns | 18 355 chars | **0 concepts** | **8 concepts, 2 episodes** |
      | full 72 turns | 28 005 chars | **0 concepts** | **6 concepts, 3 episodes** |

      The sweep that found it (264 measurements, 4 conversations, 3 repeats each) shows
      the boundary is transcript **size**, not turn count, and that it is razor sharp:

      | transcript chars | zero-rate | mean concepts |
      | --- | --- | --- |
      | 8 000–13 999 | 3–13% | ~5.1 |
      | **14 000+** | **100%** | **0.00** |

      ~14 000 chars of French is ~4 400 tokens — the 4096 window plus the system prompt.
      This is why the cliff appeared to sit at a different turn count for every
      conversation (conv 23 fell over at 24 turns, conv 22 at 40, conv 8 not at all
      through 48): turn count is a proxy for size and a bad one, since a verbose turn is
      worth several terse ones. Conv 23 packs 435 user words into 16 user turns; conv 8
      spreads 224 across 25.

      **It almost certainly explains the worthiness cliff too** (2026-08-02, below): same
      model, same default context, same shape — conv 22 scored 5/5 on its first 12 and 24
      turns and 0/5 on the full 72. Worth re-testing that with `num_ctx` raised before
      keeping `worthiness_transcripts`' both-ends sampling.

      **And it is not confined to the offline path.** The live conversation context passes
      4096 tokens around turn 18 (measured on conv 23: 2 526 tokens at turn 12, 4 141 at
      18, 6 969 at 32) and `recent_turns` is not compacted until `rolling_window_size` =
      50. So the back half of a normal session runs truncated. Whether that is what let
      the zodiacal false-recall through is plausible but **unproven** — llama.cpp's
      truncation usually preserves the system prompt and drops oldest turns — and it
      should be established rather than assumed.

      Fix is to set `num_ctx` explicitly on the offline calls and to size the live budget
      against it, not to tune a turn count. Cost measured: 29–33s for a single
      full-context call against 4.7–7.1s truncated, but it replaces the 3–6 windowed calls
      it was split into, so it is roughly cost-neutral while removing boundary
      fragmentation entirely. VRAM is not the constraint here (~38 GB GPU-addressable).
      **Do not pick a new window constant from the Part B table below until this is
      fixed** — every cell in it was measured under truncation, so it largely reports how
      well each window size avoids overflowing 4096, not how well it extracts.

      **Fixed the same day** — parts 1 and 3 of the design entry below, which were the
      sequenced-first pair. `[llm] num_ctx` (default **16384**) is read by `load_config`
      and sent as `options={"num_ctx": …}` on *every* local Ollama call — live reply,
      worthiness, disambiguation, synthesis, extraction, and the three tutor helpers — so
      no call inherits the 4096 default any more. The wizard writes the key and pre-fills
      a hand-tuned value from an existing config. Live-verified against the real model and
      the real database, conversation 22 (72 turns, 27 322 chars), nothing else changed:

      | | concepts | episodes | wall time |
      | --- | --- | --- | --- |
      | `num_ctx=4096` | 0 (all 3 windows refused) | 0 | 0.0s |
      | `num_ctx=16384` | 14 | 0 | 73.8s |

      Every skip printed its reason. 12 new tests; 565 server + 111 setup green; spec
      TR-951/TR-952/TR-311 and FR-307 amended (FR-307's "the model stops answering as the
      transcript grows" is now recorded as the truncation artefact it was).

      Two things that verification run makes visible, both feeding the deferred work
      below. The 14 concepts include `Lumières zodiacales`, `Lumière zodiacale`,
      `Zodiaque lumineuse` and `Cirrus zodiacal` — one subject, four records, split across
      window boundaries and past `merge_extractions`' exact-name dedupe. That is the
      boundary fragmentation raising `num_ctx` was supposed to let us delete, and it is now
      the strongest argument for dropping windowing rather than retuning it. And 0
      episodes on a conversation that produced 3 at 16384 in the earlier sweep — that run
      passed a `primary_language`, this one didn't, so it is a difference in the call, not
      a regression; worth pinning down before scoring extraction quality.

- [x] **`merge_extractions` dies on a null name and takes the conversation with it
      (2026-08-03, FR-307)** — 5 of 264 sweep runs (~2%) failed with
      `AttributeError: 'NoneType' object has no attribute 'strip'` from
      `concepts.setdefault(concept.name.strip().lower(), concept)`; `episode.summary.strip()`
      on the next line has the same exposure. The model occasionally emits a well-formed
      JSON object with a null `name`, and one such item discards every window's results
      for that conversation. This is the same failure shape the windowing fix already
      handled for unparseable JSON ("a window whose answer won't parse is skipped rather
      than failing the whole conversation") — a null field simply slips past that guard,
      since the JSON parses fine.

      **Fixed the same day**, at `_parse_extraction` rather than in `merge_extractions`:
      the merge is where it *crashed*, but the boundary where a model answer becomes
      domain objects is where an unusable item should die, and dying there costs the item
      instead of its whole window. `_text` treats null, a missing key, whitespace and a
      non-string as the same answer — no value — and drops the concept (no name or no
      description) or episode (no summary) that gave it; `_entries` does the same for the
      arrays themselves, since `"concepts": null` and a list of bare strings were the same
      class of conversation-losing exception. Reproduced the reported
      `AttributeError: 'NoneType' object has no attribute 'strip'` on the pre-fix code and
      confirmed both good concepts now survive alongside the bad one. 8 new tests; 573
      server + 111 setup green. FR-307 amended.

- [x] **Window sizing, deferred pending the context fix (2026-08-03, FR-307)** — the
      shipped `EXTRACTION_WINDOW_TURNS = 24` is the worst of the five sizes measured, on
      all four conversations (concepts per whole conversation, mean of 3):

      | conv (turns) | w=8 | w=12 | w=16 | w=20 | **w=24** |
      | --- | --- | --- | --- | --- | --- |
      | 22 (72) | 26.7 | 19.7 | 15.7 | 17.0 | **12.3** |
      | 8 (50) | 7.0 | 8.0 | 3.0 | 5.0 | **3.0** |
      | 9 (36) | 15.7 | 6.3 | 8.7 | 12.5 | **10.0** |
      | 23 (32) | 20.0 | 20.7 | 7.3 | 13.3 | **0.0** |

      But more concepts is not better memory — the 2026-07-18 incident was false positives
      — and these counts are confounded by the truncation bug above. 26.7 concepts from
      one astronomy conversation is more likely over-extraction than recall. Re-measure on
      a correct context, and score on whether the concepts are *right*, not how many.
      **Unblocked 2026-08-03**: `num_ctx` is explicit now, so a re-sweep measures window
      size rather than overflow avoidance — and the first question it should answer is
      whether any window at all beats one whole-conversation call.

      **Re-measured and settled the same day — `EXTRACTION_WINDOW_TURNS = 16`.** Same 4
      conversations, sizes 8/16/24/whole, 3 repeats, at `num_ctx=16384`. Scored on
      concepts the user themselves named (`upsert.py`'s FR-310 provenance rule), on
      distinct subjects after near-duplicate clustering, and on wall time — not on volume,
      which is what misled the table above.

      | size | concepts | grounded | subjects | frag | episodes | secs | grounded% |
      | --- | --- | --- | --- | --- | --- | --- | --- |
      | w=8 | 74.7 | 18.3 | 66.7 | 8.0 | 4.7 | 251 | 25% |
      | **w=16** | 40.0 | 11.3 | 36.3 | 3.7 | 3.7 | 163 | 28% |
      | w=24 (was) | 36.0 | 8.7 | 34.7 | 1.3 | 4.0 | 131 | 24% |
      | whole | 24.0 | 7.0 | 23.7 | 0.3 | 2.0 | 77 | 29% |

      **The whole-conversation call loses, which answers the question above: windowing is
      not redundant.** Every transcript fit 16384 with room to spare and no overflow line
      was printed, yet one call found a third of the concepts, half the episodes and 2.5×
      fewer distinct subjects than w=8. There is a real length effect on extraction,
      separate from the truncation that was masking it, and `extraction_transcripts` /
      `merge_extractions` stay.

      **Precision is flat (~25% grounded at every size)**, so size is a recall/volume knob
      and buys no accuracy. 16 was chosen on that trade-off rather than the
      recall-maximising 8: +30% grounded over the 24 it replaces, half the fragmentation
      of w=8, 1.25× offline cost, keeping the 2026-07-18 false-positive posture. The
      trailing-remnant fold stays — the concern that raised the question (a 24–47-turn
      conversation collapsing into one oversized window) was a truncation symptom, and at
      16 the fold only ever absorbs a remnant of ≤7 turns.

      Two tests were sized off the literal 24 and now derive from the constant: the
      contract is full coverage and the fold, not any particular number.

      **What the names show, and the sweep's own blind spot.** Reading the concepts rather
      than counting them (the entry below is the follow-up): the `frag` column badly
      under-reports, because it clusters on name similarity and so misses synonym
      clusters. The *whole-conversation* run — scored 0.3 fragmented — returned `Lumière
      zodiacale`, `Zodiaque lumineuse`, `Cirrus zodiacal`, `Zodiaque de Bénou` and
      `Zodikallicht`: five names for one phenomenon. Fragmentation is not caused by window
      boundaries and is not fixed by choosing a size.

- [ ] **One global recall floor cannot fit three languages (2026-08-04, FR-309/TR-314)**
      — promoted out of the prefix entry, which is where it was measured. Every
      per-language margin is positive; the global one is not, so the single
      `MIN_RECALL_SIMILARITY` works only because the bands happen to overlap:

      | | fr | en | it |
      | --- | --- | --- | --- |
      | margin under asym | +0.018 | +0.027 | +0.017 |

      The absent ceiling differs by language (fr 0.834, en 0.806, it 0.826), so one number
      is simultaneously too strict for one language and too loose for another — and it
      shifts as the mix of stored languages changes. `ProcessTurn` already has the turn's
      STT-detected language in hand at the recall step, so the plumbing exists. Open
      questions: per-language constants (needs a probe set per language, and re-derivation
      as the DB grows) versus normalising the score against that language's own
      background similarity, which would need no constants but is unmeasured.

- [ ] **One subject, five names — extraction's naming problem (2026-08-03, FR-307/FR-304/
      TR-609)** — the largest quality defect the window sweep measured, and it is
      size-independent, which is why it gets its own entry rather than another sweep.
      Reading the actual concept names from 48 runs across 4 conversations:

      - **Synonym clusters.** One phenomenon under five names in a *single* run
        (`Lumière zodiacale` / `Zodiaque lumineuse` / `Cirrus zodiacal` / `Zodiaque de
        Bénou` / `Zodikallicht`), and again live on 2026-08-03 during the `num_ctx`
        verification. `merge_extractions` de-duplicates on exact lowercased name, so none
        of these collide; downstream `upsert_concept` embeds `"{name}: {description}"` and
        merges only above 0.93, which differently-worded pairs will not reach. Five rows
        for one subject, each with its own engagement counter.
      - **Morphological pairs** the same exact-match dedupe misses: `Halos`/`halo`,
        `parhélie`/`parhélies`, `Noctiluques`/`Nuages noctilucs`.
      - **STT corruption promoted to concepts**, the hazard two entries below, now
        quantified: `Horreur Boréale`, `Horreur Polaire`, `Horaires boréaux` and
        `Parellis` (for *aurores boréales* and *parhélies*), `Troux noirs`, `Matère
        noire`. Some of these are **grounded** — the user repeated the mangled form — so
        FR-310's provenance gate lets them in while the correct spelling has no
        provenance.
      - **Bare-word concepts** with no content: `glace`, `latitude`, `atmosphère`,
        `phénomène météorologique`, and one apparent hallucination, `Norton`.

      Roughly half of what extraction returns is one of these, at every window size —
      that is the ~25% grounded rate seen from the other side. Candidate directions, none
      measured yet: normalise the merge key (casefold + strip accents/plurals) for the
      cheap morphological half; ask the extractor for a canonical name in the
      conversation's language and treat translations as aliases; or dedupe by embedding
      inside `merge_extractions` rather than by string, which is the only one of the three
      that touches the synonym clusters. Worth doing before any further tuning of
      thresholds downstream, since every one of them is fed by these names.

      **Investigated 2026-08-04. Both mechanical candidates are measured dead, and the
      premise may have expired.** Conv 22 and 23 are still in the database with their
      turns, so the defect was reproduced on demand rather than recalled — real extractor,
      real transcripts, nothing upserted (conv 22 must still not be consolidated).

      Conv 22 returns **23 concepts naming ~8 subjects**: five for zodiacal light
      (`Lumières zodiacales`/`lumière zodiacale`/`cirrus zodiacal`/`zodiaque de Bénou`/
      `Zodikallicht`), eight around aurora, two each for halo and noctilucent clouds.

      | approach | result |
      | --- | --- |
      | exact-lower key (ships) | 23 distinct — baseline |
      | + strip accents | 23 — **collapses nothing** |
      | + strip plurals | 22 — catches `Lumières zodiacales`/`lumière zodiacale` |
      | + strip stopwords | 21 — also catches `Halos`/`Phénomène des halos` |
      | embedding dedupe | 88.5% pair accuracy, **ordering inverted** |
      | LLM grouping (3 runs, temp 0) | ~90%, stable, but unusable as-is |

      **Embedding dedupe is dead, and for a reason worth keeping.** Labelling all 23 by
      the subject they actually name: same-subject pairs score 0.875–0.974, different-
      subject pairs 0.857–0.958 — a margin of **−0.083**, with the order actively wrong
      in places (`Lumières célestes`↔`Lumières zodiacales`, *different* subjects, 0.958;
      `zodiaque de Bénou`↔`Zodikallicht`, *same* subject, 0.886). Every one of these is a
      French description of an atmospheric optical phenomenon, most opening "Phénomène …
      qui se produit lorsque" — **the embedding reads register, not referent**, exactly
      as it does for `oggi`/`adesso`. This is the same finding that forced
      `merge_threshold` to 0.97, seen from the other side, and it means no scalar
      similarity bar can serve both jobs. Note the interaction: raising that bar to 0.97
      makes downstream merging of these clusters *less* likely, not more.

      **LLM grouping half-works.** `llama3.1:8b` given all 23 at once is stable at
      temperature 0 (runs 2 and 3 identical) and reliably finds the useful groups —
      `noctiluques`+`Nuages noctilucs`, `Halos`+`Phénomène des halos`,
      `Aurorales`+`Aurore boréale`+`Parellis` in 3/3 runs. It also, in 3/3 runs, drops
      5-6 of the 23 from its output entirely, merges `Light Diffusion` into the zodiacal
      cluster, splits the STT-corrupted aurora names into their own group (it reads
      "Horreur boréale" as *boreal horror*, which is fair), and picks bad canonical names
      (`Aurora Boreale` over `Aurore boréale`). Shippable only wrapped in deterministic
      guards — unplaced items default to singletons, members must match input names
      exactly, canonical name chosen by rule rather than by the model.

      **The reframe, and why this is now blocked on live data rather than design.** Most
      of conv 22's mess is not naming variation at all: `Horreur boréale` came back
      described as *"un type d'horreur liée à l'environnement polaire"* — a **fabricated
      concept**, the model inventing a definition to fit an STT error. The chain is STT
      mangles the term → the user repeats the mangled form → FR-310 sees user provenance
      and admits it → the model confabulates a description. Deduplicating those stores
      garbage once instead of five times.
      **Conv 22 predates the STT upgrade to `medium` (both dated 2026-08-02); conv 23
      does not.** Conv 23 returns **11 concepts naming 11 distinct subjects — zero synonym
      clusters**, one STT artifact (`Matéri e noire`, a word split in two). Confounded by
      length (72 turns/5 windows vs 32/2), so not proof, but it is the strongest available
      signal that the defect this entry describes was substantially an STT-quality problem
      that has since been fixed.
      **Deferred deliberately**: building the canonicalisation pass now would be real work
      aimed at a defect that may no longer occur. What decides it is one monitored session
      on current STT. If concepts come back like conv 23's, what remains is the
      bare-word/junk-concept filter, which is a different problem.

      **Small item, noted not done**: normalise `merge_extractions`' key with plural and
      stopword stripping. Deterministic, no LLM, matches the recorded preference for rules
      over embeddings for closed-vocabulary matching — worth 2 of 23 here, which is honest
      about its size. Held back with the rest until there is live evidence about what the
      defect actually looks like now.

- [~] **Design: an explicit `num_ctx`, and budgets counted in tokens (2026-08-03,
      TR-951/TR-952/TR-309/FR-707)** — the design response to the context-overflow
      finding above. Written before any code because it spans server config, the wizard,
      and both halves of the pipeline. Four parts, and the third matters more than the
      first. **Parts 1 and 3 are done** (2026-08-03, see the entry above); parts 2 and 4
      are open, and both are now unblocked — measurements taken from here on are taken on
      a correct context.

      **1. `[llm] num_ctx` in `mamemai.toml`.** [x] Ollama-scoped, wizard-written. It belongs in
      TR-951 rather than being voice-configurable: FR-701 doesn't apply, same
      technical-tuning-knob posture as `session_tail_turns` and `[memory]`'s thresholds.
      Note it is needed **whatever `provider` is set to** — FR-707 sends only live
      conversation to a remote endpoint, and the offline pipeline stays on local Ollama
      (TR-955), which is exactly where the cliff was found.

      **2. A context ceiling in the wizard's LLM catalogue.** [ ] `LLMCatalogueEntry` records
      `model_id`, `vram`, `languages`, `recommended`, `reasoning` — add the model's
      context length, which `ollama show` reports (`llama3.1:8b` → 131072). The model was
      never the constraint; Ollama's 4096 default was. But the ceiling is not the answer
      either: KV cache scales with `num_ctx`, so the value must be
      `min(model ceiling, what VRAM allows, what the workload needs)`. The wizard already
      computes `VRAMEstimate`/`FitLevel` per entry, so this is an extension of a fit
      calculation it performs, not a new concept. Re-running the wizard to change model
      must re-derive it (FR-706 already re-runs from recorded state).

      **3. Overflow must stop being silent — this is the actual defect.** [x] A `num_ctx`
      value alone protects nothing: whatever it is set to, a long enough conversation
      exceeds it, and today the failure is undetectable. Ollama truncates, the model
      answers about a transcript it never saw, extraction returns `[]`, and every stage
      downstream reads that as "nothing worth remembering". A whole 37-minute conversation
      was discarded twice this way with no error in any log. The fix is to measure the
      prompt against the budget before sending and say so — an `[llm]` stdout line in the
      TR-311 instrumentation family for the live path, and a skipped/flagged window rather
      than a silent empty result offline. Cheap, and it converts an invisible data-loss
      bug into a visible one. Open question: a real tokenizer or a chars/3 approximation.
      For a *warning* threshold the approximation is likely enough and avoids a dependency;
      for refusing work it is not.

      **4. Budgets in tokens, not turns.** [ ] TR-309 compacts at
      `total_turn_count % 50 == 0`, dropping the oldest 25 — a turn count, which is the
      same bad proxy that made `EXTRACTION_WINDOW_TURNS = 24` wrong. Measured on conv 23,
      the live context crosses 4096 tokens at **turn 18**; the mechanism meant to bound it
      does not fire until turn 50, i.e. 32 turns after the overflow starts. Both budgets
      should be expressed against the context, and both should then follow `num_ctx`
      automatically instead of being independently-tuned constants.

      **Remote providers need budgeting, not a knob.** `num_ctx` is an Ollama option; an
      OpenAI-compatible endpoint has a fixed context and generally returns an explicit
      error rather than truncating, so the remote path's requirement is to stay inside a
      declared budget and surface the error — a different mechanism reaching the same
      guarantee.

      **Open questions.** *Settled:* the value shipped is **16384**, and the estimator is
      **chars/3** — it over-counts for these languages, which is the safe direction for a
      warning, and the decision it feeds is only ever shout-or-don't, never refuse-work, so
      no tokenizer dependency was taken on. *Still open:* whether raising `num_ctx` makes
      `extraction_transcripts`/`merge_extractions` **unnecessary** rather than merely
      better-tuned — windowing exists only to keep each call under a limit nobody knew was
      there, and removing it would delete the concept-dedupe and boundary-fragmentation
      problems with it. The 2026-08-03 verification run is evidence for removal: one
      subject came back as four records (`Lumières zodiacales`, `Lumière zodiacale`,
      `Zodiaque lumineuse`, `Cirrus zodiacal`) split across window boundaries. And whether
      worthiness's both-ends sampling (`worthiness_transcripts`) is likewise redundant; its
      cliff has the same shape and almost certainly the same cause.

      Sequencing: settle `num_ctx` and the loud-overflow check first, since every other
      measurement here was taken under truncation and cannot be trusted until it is fixed.
      **Done** — the rest of this section is now measurable.

- [x] **The extraction window is itself inside the extraction cliff (2026-08-03,
      FR-307)** — superseded the same day by the context-window finding above; the
      measurements here are sound but the diagnosis was wrong. Kept because the reasoning
      is the same trap twice over. Conversation 23 was judged `worthiness=true` and extracted
      **0 concepts, 0 episodes**. Not the provenance gate — re-running
      `OllamaConsolidationExtractor.extract` on it directly returns empty, so nothing
      ever reaches FR-310.

      Slicing the same conversation locates the cliff, and it is *lower* than the
      "somewhere between 24 and 48" recorded above:

      | transcript | concepts |
      | --- | --- |
      | first 16 turns | **6** |
      | first 24 turns | **0** |
      | first 32 turns (= the whole conversation) | **0** |

      `EXTRACTION_WINDOW_TURNS = 24` therefore sits *at or past* the cliff it was chosen
      to avoid. It survived review because conv 22's first 24 turns happened to yield 4
      concepts — a single lucky sample. `_windows`' trailing-remnant fold compounds it:
      a remnant shorter than `size // 2` is folded back, so any conversation of 24–47
      turns collapses to one oversized window. Conv 23 (32 turns) is exactly that case.
      Fix needs measurement, not a guess at a smaller constant: find the window size that
      maximises concepts *per conversation* across several real transcripts, and decide
      whether the fold should be dropped in favour of an undersized trailing window (a
      short window that extracts something beats a merged one that extracts nothing).
      Note the 2026-08-02 entry already asked "whether a smaller window than 24 extracts
      *more* per window before choosing a size" — that question was deferred and this is
      the bill for it.

      **Closed 2026-08-03 without separate work**: everything still open here was answered
      by the re-measured sizing entry above — the window is 16, the fold stays (the
      24-47-turn collapse it was blamed for was a truncation symptom, and at 16 the fold
      absorbs at most 7 turns), and "maximises concepts per conversation" was replaced as
      the criterion by grounded concepts, since volume turned out to be flat on precision.

- [x] **The live pipeline can never say "I found nothing" (2026-08-03, FR-309/TR-303/
      TR-314)** — `PSMemoryRepository.search` is `ORDER BY distance LIMIT top_n` with no
      distance floor, for all three memory types. With a non-empty DB it always returns
      `top_n` rows however far away they are, so `recalled_memories` is never empty and
      the honesty branch in `ProcessTurn` — *"You checked your memory for this and found
      nothing … say so plainly rather than guessing or making something up"* — is
      **unreachable**. Every recall-eligible turn takes the other branch and is told to
      *"briefly acknowledge you're drawing on a past conversation"*.

      Observed live: asked whether zodiacal light had been discussed before, the reply
      opened "Oui, nous en avons déjà parlé" and closed "J'ai déjà discuté de cela
      précédemment" — for a subject absent from the DB, absent from this session, and
      absent from the (empty) session tail.
      **Not deterministic, and the first write-up of this overstated it.** Three
      recall-eligible turns produced one false claim and two clean ignores, including one
      where the model explicitly said it had no memory of the subject. The injected
      instruction is pressure, not a determinant. The defect is still worth fixing: a
      distance threshold makes "nothing close enough" *representable*, so the honest
      branch stops depending on the model choosing to override a contrary instruction —
      the same deterministic-filter-over-plea posture as FR-114 and
      `matches_trigger`.
      **Why 521 green tests missed it**: `test_recall_attempted_but_nothing_found_prompts_
      honesty` sets `memory_repo.search_results = []`, and the fake honours it. The real
      repository cannot produce that state. The fake matches the port's signature but not
      its behavioural contract, so the fix belongs in `tests/fakes` as much as in
      `postgres.py` — otherwise the next threshold bug is invisible the same way.

      **Fixed 2026-08-03.** The floor went into the *gate*, not the repository:
      `RecallGate.is_relevant(similarity)` is now its third decision, applied to each row
      before composition. `search` stays nearest-N with no floor because its other caller
      needs exactly that — `MemoryUpserter` decides insert-vs-merge from the far rows —
      so "how close counts" belongs with the policy that asks, and personas can override
      it like the rest of the gate.

      **0.85, measured against the real database and the real embedding model**, not
      picked: 26 probes in French, English and Italian, half about subjects genuinely
      stored and half genuinely absent, scored by best hit.

      | | range | n |
      | --- | --- | --- |
      | absent | 0.779 – 0.844 | 14 |
      | present | 0.852 – 0.942 | 12 |

      0 false recalls, 0 real recalls lost. Live-verified end to end afterwards: the
      zodiacal-light question returns 5 rows, top 0.844, **0 survive the floor**; "les
      techniques mnémotechniques" returns 5, top 0.942, **2 survive**.

      **The margin is ~0.008, and that is the honest headline.** The scale is compressed
      because `intfloat/multilingual-e5-large` runs *without* the `query:`/`passage:`
      prefixes it was trained with. Widening it is its own task — the next entry.

      Test-side, per the note above: `FakeMemoryRepository.search_results` now documents
      that an empty list models an empty *database*, the honesty test models its miss the
      way the real repository produces one (rows returned, all too far), and a new
      integration test pins the no-floor contract against real Postgres so the fake can't
      drift from it again. 5 new tests; 580 green.

- [x] **e5 is used without its `query:`/`passage:` prefixes (2026-08-03, TR-952/FR-309/
      TR-509)** — promoted out of the recall-floor entry above, which is where the cost
      showed up. `SentenceTransformerEmbeddingService.embed(text)` encodes raw text.
      `intfloat/multilingual-e5-large` was trained with an asymmetric pair of prefixes —
      `query: ` on the thing being searched *with*, `passage: ` on the thing being
      searched *for* — and omitting them collapses the usable range of the similarity
      scale for every comparison in the system.

      Measured consequence, 2026-08-03: unrelated French text scores ~0.84 against
      unrelated French text, absent subjects land at 0.779–0.844 and present ones at
      0.852–0.942, so `MIN_RECALL_SIMILARITY` separates real recalls from noise by
      **0.008**. Misses also skew higher in the language the memories are written in
      (French probes 0.807–0.844 against French memories, English 0.779–0.786), so one
      global floor is quietly language-biased. Both are properties of the missing
      prefixes, not of the data.

      This is not only recall's problem — it is every threshold in the project. The same
      compressed scale feeds `[memory] merge_threshold` (0.93) and
      `disambiguate_threshold` (0.75) in `MemoryUpserter`, the tutor's topic matching
      (`selection.py`), and `should_search`'s 0.93 dedup bar. All of them were tuned
      against the compressed scale and would need re-checking after the change, not just
      recall's floor.

      What it takes:
      - **The port has to learn the distinction.** `EmbeddingService.embed(text)` has no
        notion of query-vs-passage. Stored content is a passage (`upsert.py`: episode
        summaries, `"{name}: {description}"` for concepts and procedures); a turn being
        matched is a query (`session.py` step 4, `TriggerRecall`, the tutor's topic
        embedding). Both fakes and every caller move with it.
      - **Every stored row must be re-embedded.** `concepts`, `procedures` and `episodes`
        each carry an `embedding` column, and a prefixed query against unprefixed
        passages is worse than neither. There is exactly one migration today
        (`001_initial_schema.sql`) and no backfill mechanism, so this needs a script that
        re-encodes existing rows — plus a decision about bundle-installed content, which
        is authored elsewhere and installed with embeddings computed at install time.
      - **Re-measure, the same way.** Re-run the 26-probe sweep before and after; the
        change is only worth making if the absent/present gap widens materially, and the
        floor should then be re-derived from the new numbers rather than carried over.

      **Done 2026-08-04 (TR-508b), and the framing above was half wrong.** Measured before
      changing anything, three strategies against the real database, 31 probes, persona-
      scoped exactly as recall runs (re-run unscoped too — identical, so the top hit was
      always already in scope):

      | strategy | present | absent | margin | at the 0.85 floor | best possible |
      | --- | --- | --- | --- | --- | --- |
      | none (shipped) | 0.837–0.949 | 0.782–0.854 | −0.0168 | 1 false, 1 lost | 2 errors |
      | **asym** `query:`/`passage:` | 0.833–0.903 | 0.769–0.834 | **−0.0014** | **0 false**, 2 lost | 2 errors |
      | sym `query:` both | 0.834–0.960 | 0.788–0.885 | −0.0511 | 4 false, 3 lost | 6 errors |

      **The gap does not widen "materially" in the sense this entry predicted** — the
      best achievable error count is 2 either way. What asym actually buys is the
      *direction*: it pushes absent probes down (ceiling 0.8536 → 0.8341) rather than
      pulling present ones up, so zero false recalls becomes reachable at a sane floor.
      That is the failure worth spending on. `sym` is decisively worse and is recorded so
      it is not retried.

      **The dominant error source turned out to be language, not prefixes.** Every
      *per-language* margin is positive under every strategy (none: +0.025 fr, +0.006 en,
      +0.017 it) while the global one is negative — a French *absent* probe outscores an
      English *present* one. Persona scoping does not save it: GA holds French and English
      concepts together. asym mainly helps by evening this out (English +0.006 → +0.027).
      A language-aware floor is the real fix and is now its own open entry below.

      **A second finding, and the reason this could not ship prefix-only:**
      `merge_threshold = 0.93` sits inside the noise. Every one of the top 18 stored pairs
      is a must-NOT-merge — `oggi`/`adesso` 0.953, `tre`/`due` 0.925, `male`/`bene`
      (opposites) 0.919, `Italian Language`/`French Language` 0.930 — and nine clear 0.93.
      Short vocabulary in one language scores 0.92–0.96 on register alone. Raised to
      **0.97**, restricting auto-merge (which is unrecoverable and takes no LLM advice) to
      near-identical text and letting the disambiguator judge the rest.
      `disambiguate_threshold` stays 0.75: the prefixes lowered the unrelated-pair median
      (0.771 → 0.762), so the band is if anything safer, and there are no true duplicates
      in the database to derive a better number from.
      `authored_protection_threshold` stays 0.75 **deliberately**. The only measurement
      available is cross-persona (all 38 authored rows belong to the tutor, all 26 organic
      to GA) and `search` is persona-scoped, so that gate never fires today — deriving
      from it would repeat exactly the confounded-measurement mistake this file keeps
      recording. Flagged for when a persona holds both.

      **Shipped**: port split into `embed_query`/`embed_passage`, 9 call sites moved,
      `services.upsert.embeddable_text` shared with the backfill so the two cannot drift,
      `scripts/reembed_memories.py` (idempotent, `--dry-run`, `--backup`), all 86 rows
      re-embedded. `MIN_RECALL_SIMILARITY` 0.85 → **0.84**, off the error curve rather than
      the range. Verified end to end through the real repository afterwards: **0 false
      recalls, 1 lost of 16**, the honesty branch reachable.
      Test-side, eight tests were hard-coding 0.95 as "the auto-merge band" and broke when
      the threshold moved — they now derive from `DEFAULT_MERGE_THRESHOLD`, the same
      lesson as the extraction-window constants. `FakeEmbeddingService` records
      `query_calls`/`passage_calls` separately so a caller using the wrong role is
      visible. 607 green.

- [x] **Numbers ≥ 1000 are spoken as gibberish (2026-08-03, FR-106/TR-308)** — the only
      finding today with a one-file fix and no design question. `_NUMBER =
      re.compile(r"\d+(?:[.,]\d+)?")` has no notion of a thousands separator, so French
      "12 000" is matched as two independent numbers and spelled separately. Reproduced
      exactly against the live transcript:

      | written | spoken |
      | --- | --- |
      | `7 840 objets` | "sept huit cent quarante objets" |
      | `12 000 objets` | "douze zéro objets" |
      | `93 000 objets` | "quatre-vingt-treize zéro objets" |
      | `7,840 objets` | "sept virgule huit quatre objets" |
      | `en 1888` | "mille huit cent quatre-vingt-huit" ✓ |

      `num2words(0)` returning "zéro" is what turns round thousands into "douze zéro".
      The comma form breaks differently: `raw.replace(",", ".")` reads an English
      thousands comma as a decimal point. Years escape because they carry no separator,
      which is why this survived every previous session. Affects all five
      `_NUM2WORDS_LANGUAGES`. The fix needs the *language* to choose the pattern, not
      just to parameterise `num2words`: French `7,84` is legitimately 7.84, and the
      three-digit group is the only disambiguator.

      **Fixed the same day.** `_SEPARATORS` gives each language its grouping and decimal
      characters (`en` groups on `,`; `fr` on spaces; `es/it/pt` on `.` and spaces —
      spaces incl. NBSP/narrow NBSP always group, since none of these languages ever
      decimalises on one), and `_number_pattern` builds a per-language regex from them.
      The new handling sits behind a named `grouped` alternative requiring a separator
      followed by **exactly three digits**; everything else falls to a `plain` group that
      is the original pattern verbatim, so nothing but a genuinely grouped number takes
      the new path and the change has no regression surface. Verified on the real
      sentence from this session: "7 840 / 12 000 / 93 000" now read "sept mille huit cent
      quarante / douze mille / quatre-vingt-treize mille", with 1888 and 1973 unchanged.
      7 new tests; 476 unit + 73 integration green. Spec: FR-106 and TR-308 amended.

- [~] **TR-203/TR-204's VAD invalidates how TR-957 was measured (2026-08-03,
      TR-957/TR-951)** — not a code bug: the client does exactly what the spec says.
      Silence frames are never sent (TR-203) and >25 silent frames (~780 ms at the
      30 ms `FRAME_DURATION`) end the utterance (TR-204). The unrecorded consequence is
      that **Whisper never receives the utterance** — it receives the speech-classified
      fragments concatenated with all inter-word silence excised, the leading edge
      clipped before VAD locks on, and trailing quiet syllables dropped.

      TR-957 benchmarked `small` vs `medium` vs `large-v3-turbo` on **clean,
      complete, Kokoro-synthesised** utterances — pauses intact, nothing clipped. That is
      not the input the live system produces, so the benchmark measured a different task
      than the one being performed. The conclusion still holds (`medium` transcribed
      "aurores boréales" correctly on real mic audio today, first try, the word that cost
      nine turns of repair dialogue in the 2026-08-02 session), but the margin is
      narrower than those numbers imply, and part of `small`'s failure may have been
      mangled input rather than model capacity.

      Four of sixteen user turns came back truncated mid-clause, always at a natural
      thinking pause: "et on voit une couleur bleue … par rapport à nos". A ~780 ms
      threshold is shorter than a normal pause in reasoning-aloud speech.

      Worse, the waste: **49 of 65 `end_utterance` events (75%) transcribed to empty**,
      each paying a full `medium`-model STT pass plus language detection (they are
      detected as `en` at p=0.51 in a French session). Room noise re-triggers VAD the
      moment TR-205 restores the mic after playback; the observed ~1.8 s spacing is one
      noise burst plus the silence timeout. So three quarters of this box's STT budget
      went to silence — which reframes TR-957's `cpu_threads` tuning: the machine is not
      short of cores, it is spending them on nothing.
      Both knobs are already flagged as voice-configurable work under Phase 9 ("VAD
      silence-frame threshold"); this is the evidence for why they matter. Sending a
      short pre-roll of silence frames around each speech run, so Whisper sees natural
      audio, is the change most likely to pay for itself — measure before adopting.

      **Waste and truncation addressed 2026-08-04 (TR-201/TR-203/TR-204/TR-205/TR-958);
      the benchmark question below is still open.** Three changes, and the first is the one
      that matters:

      - **An onset gate.** An utterance now opens only after `onset_frames` consecutive
        speech frames (default 3, ~90 ms) instead of on the first one. That is the direct
        answer to 49-of-65: a noise blip can no longer arm a turn, and every rejected burst
        prints `[vad] ignored Nx-frame noise burst`. The gate costs no audio — a
        `preroll_frames` ring buffer (default 10, ~300 ms) carries the frames *before* the
        onset run and flushes them on open, which also fixes the leading-edge clip noted
        above. That pre-roll is the only silence Whisper now receives; **intra-utterance
        silence is still excised**, so the "Whisper never receives the utterance" half of
        this entry stands and the pre-roll experiment above is unmeasured and unadopted.
      - **`silence_frames` 25 → 50 (~750 ms → ~1.5 s), and configurable.** The user's own
        framing, and the right one: they are a slow talker, and how long a thinking pause
        runs is a property of the speaker, not of the software. All three knobs are `[vad]`
        in the client's `mamemai.toml` (TR-201). The cost is honest and paid on every turn —
        the server cannot start transcribing until the hangover elapses, so this is +0.75 s
        on a median 22.7 s turn, which is why it went to what a slow talker needs and no
        further rather than to a comfortable-looking round number.
      - **A server-side floor** (`[stt] min_utterance_ms`, default 300, TR-958). The onset
        gate is the fix; this is the backstop, because client and server are separately
        installed packages that can be out of step. Set low deliberately: a one-word French
        answer is ~300 ms of *voiced* audio (TR-203 strips the silence between words), so a
        higher floor would start eating real turns — the TR-956 asymmetry, where a false
        positive swallows a real turn in silence. Every drop prints its reason.

      **The client had no tests at all before this**, which is why both defects lived in a
      `sounddevice` callback closure for as long as they did. The state machine is now
      `mamemai_client/vad.py`, pure (bytes and booleans, no mic, no socket), with 22 tests;
      8 more server-side. 588 server green.

      **Not measured yet, and deliberately so.** Every number above is a *setting*, not a
      finding: the burst structure that produced 49 empty transcriptions is still unknown
      (~1.8 s spacing implies noise intermittent over about a second, not one short blip,
      and 3 consecutive frames may or may not be the right bar for that). The diagnostics
      were built to answer exactly that from one live session — count the `[vad] ignored`
      lines against the `[stt] skipped` ones and against real turns. Retune from those,
      then re-read TR-957's margin, which is what this entry was originally about.

- [ ] **Nothing in the pipeline checks whether stored content is true (2026-08-03)** —
      a design gap, recorded because this session made it concrete rather than
      theoretical. Five of the assistant's replies were confidently wrong: the aurora
      colour mechanism (invented a transmission/scattering story; the real mechanism is
      emission, with 630 nm red quenched at low altitude — the exact inverse of what it
      said), noctilucent clouds "emitting" red light, Arcturus placed in Canis Major and
      called the ninth-brightest star, four main-sequence/subgiant stars declared "toutes
      des géantes rouges ou bleues", black holes listed as a type of star, and
      main-sequence colour presented as an ageing sequence rather than set by mass.

      Two of those propagated **into the user's own turns**: the model asserted the
      aurora altitude inversion first, the user repeated it back accurately and asked
      "c'est juste?", and it confirmed its own error and built four paragraphs on it.
      Worthiness (FR-307) asks whether a conversation is worth remembering, not whether
      it is true; extraction lifts concepts from the transcript; FR-310 checks who *said*
      a term, not whether the claim holds. A high-content, on-topic, internally coherent
      conversation is precisely the shape that clears every gate — the failure mode is
      not noise getting in, it is fluent wrongness getting in.
      No fix proposed here: a factuality gate against a local ~8B model is its own
      project, and `llama3.1:8b`'s French-astronomy accuracy may simply be the operating
      limit (cf. TR-952 on model choice). Recorded so it is a known, chosen limitation
      rather than a surprise.

- [ ] **Two FR-310 provenance hazards confirmed on real data (2026-08-03, FR-310)** —
      the open hazard entry below predicted the first; the session produced both, and a
      third interaction worth recording. Extracting conv 23's first 16 turns (the slice
      that still works) proposes six concepts:
      - **`nuages noctiluc` passes as USER-SAID.** STT wrote "Noctiluc" for
        "noctiluques"; the assistant used the correct term; the user repeated the mangled
        form back, so the corrupted spelling is the one with provenance and the correct
        one has none. Exactly the structural problem recorded below, now observed with
        `medium` rather than `small` — the upgrade shrank the errors without removing the
        mechanism. Three spellings of one concept appeared today ("zodiacales" /
        "zodiaquales", "Noctiluc").
      - **`géantes bleues` is discarded as NO-PROVEN** although the user discussed it for
        three turns — they said "géante bleue" (singular) and STT rendered another
        attempt as "les géants de bleu". Whole-word matching cannot see inflection: the
        same failure as the "hiking"/"hiked" case documented at `upsert.py`, still live.
      - The user also **misheard a correct denial**. Asked whether noctilucent clouds had
        come up before, the assistant honestly said it had no such memory — in the final
        clause of a four-paragraph spoken reply — and the user's next turn opened "on a
        déjà parlé de ces nuages noctilucs". In voice-only with no barge-in (INV-4) there
        is one pass and no scanning back, so a disclaimer at the end of a monologue does
        not land. If a reply denies a memory, the denial has to lead. Worth an FR-309
        amendment.

- [ ] **Latency structure, and two things that will get worse (2026-08-03)** —
      recorded as measurement, no fix attached.
      - [x] **Nothing streams to the client.** `TurnResult.audio_chunks` is a `list[bytes]`;
        every sentence is synthesised before `ProcessTurn.execute` returns, and the
        handler only then sends them. So `[latency] Total to first audio` (median ~9.6s)
        measures when the server *synthesised* its first chunk, not when the user hears
        anything — the real wait is the total-turn figure, **median 22.7s of dead air**
        with no barge-in to escape it. TR-308 synthesises the first sentence "as soon as
        it completes, with no lead-in buffer ahead of it", which reads as intent to
        deliver early; the delivery path defeats it. Worth checking whether that is drift
        or a deliberate simplification — the metric name should change either way.

        **Drift, and now fixed (2026-08-04, TR-316).** It was not a deliberate
        simplification: `git show 5e83853^` — the server *before* the domain-layer
        refactor — synthesised each sentence inside the token loop and sent it
        immediately (`if is_sentence_end(buffer): await ws.send(synthesize(buffer))`).
        The refactor replaced that with `TurnResult.audio_chunks` and the behaviour was
        lost in the move. The user remembered latency being better; they were right.

        `ProcessTurn.execute` now takes an optional `emit_audio` sink and delivers each
        segment through it from inside the generation loop; the handler's sink is
        `ws.send`. With a sink, `audio_chunks` stays empty — a handler that supplies one
        *and* sends the list gets silence rather than every sentence twice, which is the
        right way round for a mistake to fail. The metric was renamed to
        `[latency] First audio sent`, since it finally measures what its old name claimed.

        **Why no test caught it, and what the new ones do differently.** Every existing
        assertion was of the form `assert result.audio_chunks` — the audio was all still
        *there*, just late, so "audio was produced" was exactly the assertion blind to
        this. The six new tests assert **when** audio left relative to generation, by
        interleaving LLM yields and sink deliveries in one event log. Their discriminating
        power was itself verified rather than assumed: the same assertion run against a
        deferred-delivery ordering returns False (`token0,token1,token2,audio0,…`) and
        against the streaming ordering returns True (`token0,audio0,token1,audio1,…`).
        601 server green.

        Expected effect, not yet live-measured: generation is 16.6s of the 22.7s turn, and
        the first sentence completes early in it, so most of that wait should now be
        playback the user is already hearing. The number to check on the next live session
        is `First audio sent`, and whether playback ever starves — nothing yet bounds the
        gap between generation and speech rate.
      - **`rolling_window_size` defaults to 50 and `server.py` never overrides it.** LLM
        first-token latency grew 2.4× across 16 turns (2.16s → 5.15s) and compaction
        cannot fire until turn 50. Its summarisation call is `await`ed inline at step 10,
        *before* `TurnResult` is returned, so on that turn it lands entirely inside the
        user's silence rather than overlapping playback.

- [x] **TTS and embeddings are stranded on CPU by a CUDA torch build on an AMD box
      (2026-08-03)** — the reason to stop reading this session's latency as a hardware
      ceiling. Checked rather than assumed, because the standing shorthand for this
      machine ("AMD GPU, so everything is CPU") is only true of one component:

      | component | runtime | on GPU? |
      | --- | --- | --- |
      | LLM (`llama3.1:8b`, Ollama) | ROCm | **yes** — device `ROCm0`, ~38 GB free |
      | STT (faster-whisper / CTranslate2) | CPU | **no, and no path** — no ROCm backend |
      | TTS (Kokoro / torch) | CPU | no — but only by configuration |
      | Embeddings (sentence-transformers / torch) | CPU | no — but only by configuration |

      Ollama's own log settles it: `llama_prepare_model_devices: using device ROCm0
      (…integrated GPU…) — ~38 GB free`, with the model's tensors and KV cache
      resident in `ROCm0` buffers. ROCm works on this machine. So the LLM stage was never
      competing for CPU threads, and TR-957's `cpu_threads` tuning was contending with
      TTS and embeddings, not with the model.

      Two independent things keep the other two torch components on CPU:
      1. **The installed torch is a CUDA build** — `torch 2.12.0+cu130`,
         `torch.cuda.is_available() == False`, `torch.version.hip is None`. There is no
         GPU path available to it at all on this hardware.
      2. **`[tts] device = "cpu"` in `mamemai.toml`**, and `SentenceTransformerEmbeddingService`
         passes no device to `SentenceTransformer(...)` — it has no device knob at all, so
         even a working ROCm torch would need a code change to use it.

      Why this matters for the numbers above: median total turn is 22.7s, of which STT is
      4.8s and the GPU-resident LLM is fast; most of the remaining ~13s after first-audio
      is Kokoro synthesising the rest of a ~1200-character reply on CPU. That is the
      largest single recoverable slice in the pipeline.
      **Not yet verified, and not to be assumed**: that a PyTorch ROCm wheel actually
      works for this GPU's architecture target. Ollama ships its own ROCm runtime and a HIP llama.cpp
      backend, which is evidence the hardware and driver are fine but is *not* evidence
      that upstream PyTorch supports it — and `rocminfo` is not even installed here, so
      there is no system ROCm to build against yet. Next step is to test a ROCm torch
      build against Kokoro alone and measure, before touching config or adding an
      embedding device knob.
      Ordering note: this is worth doing *after* the VAD waste below. Three quarters of
      STT passes today were spent on silence, so relieving CPU pressure there is free and
      changes what any GPU measurement means.

      **Resolved 2026-08-04 — and the premise was wrong. TTS belongs on the CPU.**
      Measured in the order this entry asked for: prove the GPU is reachable, then test
      Kokoro alone, then touch config.

      **1. PyTorch does support this GPU.** The open question ("not to be assumed") is
      settled: the machine is **Strix Halo, gfx1151** (`gfx_target_version 110501`,
      Radeon 8060S/8050S, 16 GiB VRAM + 22.8 GiB GTT), `/dev/kfd` is up, and the user has
      ACL access without a group change. `torch==2.12.1+rocm7.2` lists **gfx1151 among its
      build targets**, reports the device correctly, and agrees with the CPU to 2.5e-04 on
      a matmul and 9.5e-07 on an LSTM. `rocminfo` and a system ROCm are *not* needed — the
      wheel is self-contained. That version is also a patch above the CUDA build it
      replaced (2.12.0), so nothing else in the tree moved.

      Note for whoever installs it: `uv pip install` **stalls** on this wheel through the
      proxy here — 16 parallel connections, 2.3 KB/s, indefinitely. `curl` on the same URL
      gets 5.8 MB/s. `UV_CONCURRENT_DOWNLOADS=1` fixes it outright.

      **2. Kokoro on the GPU is 7x SLOWER.** The measurement this entry called for, real
      adapter, real French reply of 8 sentences, audio verified identical across devices
      first (duration and RMS within 0.1%):

      | | cold | warm/sentence | whole reply | realtime factor |
      | --- | --- | --- | --- | --- |
      | **CPU** | 3.61 s | **0.42 s** | **3.47 s** | 0.07x |
      | GPU, warm MIOpen cache | 13.35 s | 1.45 s | 25.69 s | 0.53x |
      | GPU, cold | 22.62 s | 1.46 s | 49.64 s | 1.03x |

      Not a configuration failure — a property of the model. Kokoro is 82M parameters
      dominated by *sequential* LSTM steps, so it is latency-bound; a wide parallel device
      adds per-kernel launch overhead and can parallelise almost none of it. MIOpen also
      falls back to `GemmFwdRest` with no workspace for the convolutions. And the cold
      column is per-machine JIT state (`~/.config/miopen`) that no fresh install has.

      Getting it to run at all needed a fix worth keeping even though the answer is "stay
      on CPU": Kokoro builds every DurationEncoder LSTM with `dropout=0.1` and
      `num_layers=1`, where dropout can never fire — but MIOpen compiles a dropout kernel
      from the argument anyway, and the ROCm wheel ships no `rocrand_xorwow.h`, so
      synthesis died with a bare `miopenStatusUnknownError`. Zeroing it is bit-identical
      (TR-959). Kept because without it `[tts] device = "cuda"` is an inscrutable crash on
      any ROCm box, and the trade could flip on a discrete card.

      **3. Embeddings do gain, modestly.** Same GPU, opposite result — e5-large is a 560M
      transformer, one wide parallel pass: **71 ms → 25 ms** per turn, 1.16 s → 0.81 s for
      a batch of 64. Against a 22.7 s turn that is 0.2%, so this is not a latency fix; it
      is for the offline pipeline and for the bulk re-embedding the e5-prefix task needs.
      `[embedding] device` added (TR-960), and `SentenceTransformerEmbeddingService` now
      takes a device at all, which it never did.

      **The general rule, now recorded: device placement is per model, not per machine**
      (TR-960). Auto-detect is actively unsafe once torch can see a GPU — it picks one for
      everything, and here that is right for one component and 7x wrong for the other. It
      bit the test suite immediately: `tests/integration/test_tts.py` constructs with no
      device, so the swap silently moved it onto the GPU and the suite went 10.5 s → 52.9 s
      while testing a path nothing ships. Pinned to `"cpu"`; suite back to 11.4 s.

      **Not made reproducible.** The ROCm build is installed in the venv but *not* pinned
      in `pyproject.toml`, so a fresh `uv sync` silently restores the CUDA build. Pinning
      it would break CUDA installs, so the fix is conditional resolution or wizard-side
      detection — deliberately deferred, and the reason to check `torch.version.hip`
      before trusting any future measurement here. 7 new tests; 595 server green.

- [ ] **The 22.7 s turn is the LLM writing too much, not the devices (2026-08-04,
      TR-951/TR-952/TR-308/TR-309)** — the finding that replaced the entry above, measured
      once its premise collapsed. PLAN had attributed "most of the remaining ~13 s after
      first-audio" to Kokoro on CPU; Kokoro synthesises a whole 8-sentence reply in 3.5 s.
      The time was somewhere nobody had timed, because the live instrumentation records
      *first token* and never total generation.

      Measured against the real model and config (`llama3.1:8b`, `num_ctx=16384`):

      | stage | time | share of turn |
      | --- | --- | --- |
      | STT (`medium`, 8 threads) | 4.8 s | 21% |
      | **LLM generation** | **16.6 s** | **73%** |
      | TTS (Kokoro, CPU) | 3.5 s | 15% |
      | embedding (recall gate) | 0.07 s | 0.3% |

      **First token is 0.20 s.** Generation runs at a flat **29.4 tok/s** across runs, and
      the replies were **1832 / 2486 / 887 characters**. So the dead air is not latency in
      any stage — it is the number of tokens the model is asked to produce, multiplied by a
      generation rate that is already what this GPU does. Nothing about device placement
      touches it.

      Three levers, in order of size, none measured yet:
      - **Reply length.** At 29 tok/s every 100 characters of French costs ~0.9 s of
        silence. A 2500-character answer is 22 s on its own. Nothing in the persona prompt
        or `ProcessTurn` bounds length today, and FR-101's spoken-reply framing arguably
        should — a voice assistant that talks for 25 s without barge-in (INV-4) is a
        different failure from a slow one.
      - **Nothing streams** (already recorded below): `TurnResult.audio_chunks` is a list,
        so all synthesis completes before the first byte is sent. With a 16.6 s generation
        in front of it, streaming would convert most of the wait into playback the user is
        already listening to — the largest *perceived* win available, and it needs no model
        change. TR-308 reads as intent to deliver early; the delivery path defeats it.
      - **A faster or smaller model**, weighed against TR-952's language-drift reasons for
        `llama3.1:8b`.
      Note the live `[latency] LLM first token` figures (2.16 s → 5.15 s across a session)
      are prompt *processing* growing with context, a different quantity from the 0.20 s
      measured here on a bare prompt — and both are dwarfed by generation either way.

- [x] **Cosmetic: benign strategy warning on every start (2026-08-03, TR-004)** — `[strategy]
      persona 'Coach' declares unknown strategy 'cognitive_coach' — nothing bound from
      this registry`, printed once per server start and once per offline run.
      `cognitive_coach` *is* registered for selection and assessment; it is absent only
      from `ENRICHMENT_STRATEGY_FACTORIES`, and the cognitive package has no enrichment
      strategy at all. This is the "not a gap, just not relevant to this capability" case
      the suppression comment describes, but the guard only covers a registry that is
      *entirely* empty (`RECALL_GATE_FACTORIES`), so a partially-populated one still
      warns. Suppress per-capability, or the warning trains people to ignore it.

      **Fixed the same day.** "Unknown" now means known to *no* registry
      (`_KNOWN_STRATEGY_NAMES`, the union of all four) rather than missing from the one
      being built, which is the question actually worth warning about. A name in no
      registry at all still warns — once per registry, which is noisy, but that persona
      genuinely is misconfigured, and the old empty-registry exemption had been hiding
      exactly that case for `RECALL_GATE_FACTORIES`. 4 new tests. Spec: TR-004 amended.

- [x] **STT was running on a sixth of this machine (2026-08-02, TR-957)** — chased down
      after concluding that Whisper `small` was the root cause behind three of the day's
      four findings. Benchmarked against Kokoro-synthesised audio of the exact utterances
      the live session mangled (clean audio, so accuracy here is a ceiling, not a
      prediction — but a model that fails the easy version cannot pass the hard one).

      | | per ~6s utterance | realtime | "aurores boréales" |
      | --- | --- | --- | --- |
      | small, 4 threads (shipped) | 1.30s | 0.23x | ✗ "ororboréales" / "oraux arboreals" |
      | small, 8 threads | 1.11s | 0.19x | ✗ (unstable across runs) |
      | medium, 4 threads | 4.14s | 0.72x | ✓ |
      | **medium, 8 threads** | **2.75s** | **0.48x** | ✓ |

      `cpu_threads` was never passed to `WhisperModel`, so CTranslate2 chose 4 on a 24-core
      box. It is now `[stt] cpu_threads`, default 8 — **measured, not `nproc`**: 16 and 24
      threads are both *slower* than 8 (14.4s and 15.3s against 13.8s over 28.7s of audio),
      the int8 GEMM saturating memory bandwidth before it runs out of cores. Transcripts are
      byte-identical at every thread count, so it is free latency.
      `beam_size` was measured first and is a dead end, recorded so it is not retried: 5→1
      saved 4% and *cost* accuracy. On CPU the encoder pass over a fixed 30s mel window
      dominates; beam width only touches the decoder. That prediction was wrong when first
      offered, which is why it was measured rather than assumed.
      `large-v3-turbo` was measured too and **rejected**: 4.06s per utterance against
      medium's 2.75s at the same 8 threads, for one extra correct letter. Its 4-layer
      decoder (against 32) is why it is fast elsewhere, but its encoder is large-v3-sized
      and on CPU the encoder dominates — the same reason `beam_size` did nothing. A GPU
      would likely reverse this; untested, since CTranslate2 has no ROCm backend and this
      machine's GPU is AMD.
      Net: medium at 8 threads costs **+1.45s per turn** against what shipped, and gets the
      word that cost nine turns and two discarded consolidations right every time.
      521 tests. Spec: TR-957 added, TR-951 amended.

- [x] **Extraction has the same length cliff worthiness had (2026-08-02, FR-307)** —
      found by re-consolidating the five conversations the worthiness fix rescued. Three
      of the five came back worthy, conv 22 (the 72-turn astronomy session) among them —
      and it still yielded **0 episodes and 0 concepts**. The whole database still contains
      no aurora, no noctilucent clouds, no zodiacal light, no halos.
      Not the FR-310 provenance gate — that was the first hypothesis and it is wrong,
      because nothing ever reaches it. `OllamaConsolidationExtractor.extract` returns
      empty for that conversation. Sliced, it is the same cliff as worthiness, with valid
      JSON throughout (not a swallowed `JSONDecodeError`):

      | transcript | result |
      | --- | --- |
      | first 12 turns | 4 concepts — Light Diffusion, Supernovae, Nebulae, Aurorae |
      | first 24 turns | 4 concepts — Light Diffusion, Aurorae, Halos, Zodiacal Light |
      | first 48 turns | **0** |
      | full 72 turns  | **0** |

      So the content is extractable and the model finds it readily — it just returns empty
      arrays once the transcript passes somewhere between 24 and 48 turns.
      Fix is analogous to `worthiness_transcripts` but genuinely harder, which is why it is
      not done here: worthiness only needed OR across windows, extraction needs results
      *merged* across them — concept dedupe by name/embedding before upsert, and episodes
      deduped so one event spanning a window boundary does not become two. Worth measuring
      whether a smaller window than 24 extracts *more* per window before choosing a size.
      Note also that the pipeline evaluates worthiness **once**, not by majority: conv 10
      and 17 tested 3/3 YES beforehand and came back NO on the single production call. The
      judgment is non-deterministic and single-shot; voting is only in the quality gate.

      **Fixed the same day.** `extraction_transcripts` splits a long conversation into
      consecutive 24-turn windows — all of them, unlike worthiness which samples both ends,
      because extraction must cover every turn — and `merge_extractions` folds the results.
      Concept de-dupe by name in that merge is load-bearing, not tidiness: the same concept
      from two windows would be upserted twice and the second upsert would promote the row
      the first just wrote, manufacturing the within-one-conversation recurrence FR-310
      rules out. A window whose JSON won't parse is now skipped instead of emptying the
      whole conversation.
      Verified on real data: conv 22 went 0 → **10** concepts, conv 8 (50 turns) 0 → 5,
      conv 9 (36 turns) 0 → 6 concepts + 1 episode.
      **A unit test caught a real data-loss bug in the first attempt at the fold.**
      `windows[-2] = windows[-2] + windows.pop()` reads correct and is not: Python resolves
      the assignment target *after* the right-hand side, so `-2` indexed the
      already-shortened list, the fold landed on the wrong window and the first 24 turns
      were silently dropped — while still producing the expected number of windows. The
      test asserts turn-by-turn coverage, not window count, which is the only reason it
      showed up.
      465 unit tests. Spec: FR-307 amended, TR-953 amended.

- [x] **Episode extraction still fabricates, and the length cliff was hiding it
      (2026-08-02, FR-307)** — found by the user asking what the newly-extracted episode
      from conv 9 actually was, since they remembered no episodic event in it. They were
      right. Extraction proposed:

          happened_at = 2024-03-12
          "User asked about weather forecasts and the assistant mentioned its
           knowledge cutoff in 202..."

      That is a conversation *about the assistant's own operation* filed as a dated
      personal event, with a date appearing nowhere in the conversation — apparently lifted
      from the model's own knowledge-cutoff remark. Conv 9 is greetings, weather, the date,
      an AI/XAI discussion and an Italian lesson. There is no episode in it.
      **Both existing guards failed.** The extraction prompt says episodes must be "NOT
      anything about the assistant's own operation" and must carry "a genuine, identifiable
      time or place distinct from 'during this chat'"; `_parse_extraction` drops episodes
      whose `happened_at` won't parse. A fabricated-but-well-formed date defeats all three.
      This is the 2026-07-18 incident recurring in the same shape.
      **Not currently in the database** — conv 9 was re-consolidated before the windowing
      fix, so extraction returned nothing and the fabrication was never proposed for real.
      But conv 9 is `worthiness=true`, so consolidating it now would store this. The two
      bugs were partly cancelling: the length cliff was suppressing a fabrication path, and
      fixing extraction un-suppressed it. Episodes are the user's life story (INV-10) and a
      fabricated one is worse than a missing one, so this ranks above the provenance hazard
      below.
      Fix needs evidence, not a guess, same as the worthiness rebalance: a labelled set of
      conversations with and without real episodes, then measure.
      **Leading candidate (user's proposal, 2026-08-02): extract episodes from the user's
      turns only.** It fits what this codebase already does twice over — the extraction
      floor counts only user turns ("assistant chatter must never inflate it"), and FR-114's
      lesson was that withholding a signal beats asking a model to ignore one. The
      fabricated episode is a summary of *assistant* content, so on a user-only transcript
      it is unconstructible rather than merely discouraged. That is a filter, not a plea,
      which is the kind of fix that has held here.
      The risk to measure is context loss: "Yes, in Tromsø." survives alone, "Yes, there."
      does not, and stripping the assistant can cost the entity that makes an episode an
      episode. This argues for episodes-from-user-turns while **concepts keep the full
      transcript** — the assistant's side is where correct terminology lives (cf.
      "Noctiluques" vs the STT-mangled "horreur boréale"). That means two passes per window
      rather than one, doubling extraction calls on top of windowing's multiplication
      (conv 22: 1 call → 3 → 6); worth timing before committing to it.
      Other directions, weaker: make the model quote the user turn grounding each episode
      and drop those without one (still a plea, and quoting is itself fabricable). A
      plausible-date-range check is *not* the answer — "I visited Rome in 2019" is
      legitimately old.

      **Fixed 2026-08-04 (TR-961). The user's proposal was right, and needed a second
      filter beside it.** The nine episodes in the database were read first: **all nine
      are non-episodes** — "Discussion sur les techniques mnémotechniques", "User inquired
      about the color of the sky", "User learned how to use the find command" — records of
      the chat, not of the user's life (INV-10). Five share one `happened_at`, the
      conversation's own timestamp.

      **1. Episodes now come from a separate pass over the user's turns only.** Concepts
      keep the full transcript, because the assistant's side is where correct terminology
      lives against STT-mangled user turns. That is two passes, and the cost is one extra
      call per conversation — not the doubling this entry feared, since the episode pass
      runs on a transcript roughly half the size and personas with
      `extracts_episodes=False` skip it entirely. `_extraction_system_prompt` gained
      `extract_concepts` as the mirror of `extract_episodes`, so neither pass pays for the
      other's array.
      Measured on conv 23 (32 turns, astronomy): the full transcript produced **"Hier soir,
      j'ai observé la nébuleuse de l'Oiseau avec mon télescope"** and **"Le week-end
      dernier, je suis allé au parc et j'ai vu le ciel nocturne"** — two dated first-person
      life events that never happened, worse than the conv 9 fabrication that opened this
      entry. User turns only: **0 episodes**.

      **2. Episodes dated inside their own conversation are dropped.** The prompt already
      demands a time "distinct from 'during this chat'" and cannot enforce it. **8 of the 9
      stored episodes fall inside their conversation's span**; the ninth misses by 0.02s
      because its timestamp was truncated to whole seconds, hence a 60s tolerance. This is
      what catches conv 9, where user-turns-only alone still returned "Spoke with Italian
      tutor Martino about Rome" — a real thing that happened, in the chat.
      The two filters are complementary and neither subsumes the other: the invented
      telescope episodes are dated plausibly in the past and survive filter 2; the
      Italian-tutor one survives filter 1.

      **Verified on the shipped path**: conversations 23 and 9 both return **0 episodes**,
      with the date filter printing its reason on 9. Concept output is unchanged in quality
      (conv 23: 14 concepts, 12 sound, one synonym and one property-as-concept — the same
      mix as before, no degradation from the split). The context-loss risk this entry
      flagged did not materialise, because concepts never moved off the full transcript.
      10 new tests; 617 green.

      **Left as it is**: the 9 fabricated episodes already in the database. They predate
      the fix and nothing removes them — worth deleting, but that is a destructive
      operation on real data and is the user's call, not a side effect of this change.

- [ ] **Hazard: the provenance gate prefers STT's mistakes to the real words** — surfaced
      by the fix above, not caused by it, and left open because it needs a design decision
      rather than a patch. FR-310 admits a brand-new organic concept only if the **user**
      literally named it (`_mentioned_in`, whole-word against their own turns). When STT
      corrupts what the user said, the corrupted form is what has provenance. On conv 22
      the surviving pair is `Halos` (correct) and **`Horreur Boréale`** — a nonexistent
      horror film, which is what Whisper wrote for "aurore boréale" — while `Noctiluques`,
      `Zodikallicht` and the rest are discarded for having been said only by the assistant,
      which had them right.
      So conv 22 should **not** be re-consolidated: extraction now works on it, and what it
      would store is worse than nothing. Left as it is.
      Mitigated going forward by the STT upgrade below (medium transcribes these words
      correctly), but the interaction is structural: any STT error that the user does not
      correct becomes the only spelling with provenance. Worth revisiting when there is
      live data on how often it bites.

- [x] **Worthiness said NO to almost everything (2026-08-02, FR-307)** — the second bug
      found monitoring the live consolidation run. Conversation 22 — 37 minutes, 72 turns,
      667 user words, far above the extraction floor — was marked `worthiness=false` and
      extracted nothing. Across the whole DB, **every French conversation had been judged
      unworthy** (0/4, against 9/18 for English).
      The language angle was the first hypothesis and it was *not* the main cause; recorded
      because it cost an investigation. A labelled probe set showed the shipped prompt at
      **23/45**: every negative case right (20/20) and nearly every positive wrong,
      including a plain English "I went to Norway last weekend and finally saw the northern
      lights, I work as an engineer". A global NO bias, with French tilting it further. The
      prompt gave YES one clause and NO five vivid ones, and the model obliged.
      Fix 1 — rebalance: YES criteria as their own enumerated list, NO narrowed to "nothing
      but". Same set: **40/45**. A language-neutrality clause and few-shot examples were
      each measured on top and changed nothing, so neither shipped.
      Fix 2 — windowing, because after the rebalance conv 22 was *still* 0/5. Slicing it
      proved the cause is **length, not content**: first 12 turns 5/5, first 24 5/5, last 24
      4/5, and even the 18 turns of pure STT-repair dialogue 5/5 — while first 48, the 54
      turns left after removing all repair dialogue, and the full 72 all scored 0/5.
      `worthiness_transcripts` now judges long conversations at both ends, worthy if either
      is. Short conversations are still exactly one call.
      Fix 3 — the parse. `"yes" in reply.lower()` read a bare "OUI" (observed) as NO, and
      would read "Consider it stored" as YES via the Spanish "si". `is_affirmative` decides
      on the first word, scans whole words never substrings, and treats an unreadable
      answer as not-yes. Shared by both providers and both judgments — 4 call sites.
      Re-run against all 14 real conversations above the floor: 8 verdicts changed, all in
      the right direction. Conv 22 now 3/3 YES; the long ones (50, 36 turns) flipped to YES;
      three short ones flipped *to* NO and were all correct — greetings plus a capability
      question, a tutor drill, and "what LLM model are you using", which is precisely the
      2026-07-18 failure the original prompt existed to prevent.
      455 unit + 73 integration green, plus a new manual gate
      (`test_worthiness_llm_quality_gate.py`, `MAMEMAI_TEST_LLM_QUALITY_GATE=1`) that makes
      this probe set permanent. Spec: FR-307 amended, TR-953 amended.
      **Re-consolidated** the same day (user reset `consolidated` on ids 8, 9, 10, 17, 22;
      pre-state dumped first). Result: 3 of 5 now worthy — 8, 9 and **22**, the astronomy
      session. 10 and 17 came back NO despite testing 3/3 YES, because the pipeline asks
      once rather than voting. Memory gained one concept (TLS) and **no episodes**: see the
      open extraction item above, which is why. Episodes were the whole point of re-running
      — concepts had already been extracted on the first pass, since FR-310 gates them
      independently of worthiness.

- [x] **The memory brief was generated from nothing (2026-08-02, FR-308)** — found by
      monitoring a live consolidation run, not by a test. The brief in the DB read *"Based
      on our conversation (which is non-existent), I don't have any information about the
      user yet… **User Profile:** No information available at this time"* while 63
      concepts, 9 episodes and 13 procedures sat in the same database — and FR-109 injects
      that into every session's system prompt, so the assistant opened every session being
      told it knew nothing about the user.
      Cause: `GenerateMemoryBrief` had **no memory repository at all**. It asked the LLM
      to "generate a concise brief of what you know about the user" with an empty context
      and saved the answer. Present since `899ba04`, the first domain-layer commit.
      It survived because its only test used `FakeLLMService(response="Martin enjoys
      hiking in the Alps…")`, which returns the same canned brief whatever prompt it is
      handed — a fake asserted on its *output*, so a missing repository read was invisible.
      That test now asserts on the **prompt**, and the 5 new unit tests all do the same.
      The brief is now summarised from recent episodes (INV-10, unscoped) + *organic*
      concepts across every persona, bounded (20/40, placeholders). Authored concepts are
      excluded: a bundle's curriculum is the owning persona's material, not a fact about
      the user, and the brief reaches personas that never declared `reads_across_personas`
      (FR-303). Branches on `origin`, never on persona (INV-6). With no material at all it
      now writes **nothing** rather than overwriting a good brief with a blank one.
      Verified against the real DB and real llama3.1:8b: 9 episodes + 25 organic concepts
      in (38 authored correctly excluded), a genuine 837-char profile out.
      423 unit + 73 integration green. Spec: FR-308 rewritten, TR-702 amended.

- [x] **Voice follows the text, not the configuration (2026-08-02, TR-305/TR-307)** —
      found live-testing the language directive: after switching, a **French voice read
      English sentences**. Two compounding causes, neither in the directive itself.
      `detection_candidates` was empty unless a persona had a *cast*, so for GA language
      detection never ran at all and the voice stayed pinned to `default_voice` for the
      whole reply; and `_session_voice` fell back to the anchor for any language absent
      from `persona.voices`, which for a cast-less persona is every language but one.
      So the voice was decided by configuration alone — and configuration only says what
      the persona was *asked* to speak. The directive changes it instantly; the model's
      actual output language is a request it may not honour for a turn (llama3.1:8b
      "wobbles occasionally but self-corrects"). Whichever way the wobble goes, the mouth
      was wrong.
      Detection now runs for every persona, over every installed language plus the
      persona's own response language and cast keys, and an unregistered language
      resolves through the machine's language→voice map before the anchor. py3langid is
      inline and returns None below a confidence floor, so short/ambiguous segments still
      keep the current voice (TR-307) and cost is microseconds.
      **A deliberate earlier optimisation was reversed**, and its test rewritten to say so:
      single-voice personas used to skip detection to avoid "a wasted call on every
      ordinary turn". The saving was real; a wrong accent costs the whole sentence.
      407 unit + 73 integration green (1 rewritten, 1 new reproducing the exact symptom).
      Spec: TR-305 amended.

- [x] **`ValueError: Unknown language code zh-cn` (2026-08-02, TR-307)** — the immediate
      sequel to the item above, hit on the next live turn. Broadening candidates to every
      *installed* language first fed `zh-cn` to py3langid, whose model is keyed by bare
      ISO 639-1; `set_languages` raises on any code it doesn't know and the exception came
      up through the synthesis loop, killing the turn. A vocabulary mismatch, not a
      detection failure — and it was invisible to the unit suite because every session
      test goes through `FakeLanguageDetector`, so the real adapter had never seen the
      candidate set it is handed in production.
      Translation now lives at the adapter boundary in both directions (IETF tag in, the
      caller's own tag back out — a leaked bare `zh` would miss `voices["zh-cn"]` and
      `KOKORO_DEFAULT_VOICES["zh-cn"]` and speak Chinese with an English mouth), unknown
      candidates are dropped rather than raised, and fewer than two usable candidates
      returns `None` like any other unusable call. First real unit test of the adapter
      itself (6 cases, incl. the whole `SUPPORTED_LANGUAGES` set as candidates).
      413 unit + 73 integration green. Spec: TR-307 amended.

- [x] **Ask for a language *in* that language; detection back to casts only
      (2026-08-02, FR-708/TR-305)** — user's call, and it swaps a downstream correction
      for removing the cause. The switch directive now exists in exactly one phrasing,
      the target language's own ("speak english", "parle français"); the phrasing in the
      language being left behind is gone. Rationale: the directive utterance is also an
      ordinary user turn and lands at the bottom of the LLM's context (`session.py:624`),
      so asking in the old language pits a fresh example of it against the switch
      instruction in the system prompt — which is exactly when llama3.1:8b was seen
      answering the old language for a turn. Accepted cost, stated in FR-708: a language
      you cannot pronounce at all is now reached from the wizard, not by voice.
      Rows from the old rule are **pruned** on startup rather than left ungenerated (a
      directive row outlives its config), which needed a `delete` on
      `DirectiveRepository` — the first one. `ensure_language_directives` had no unit test
      at all until now; it has 5, including the pruning and the "parle-moi en anglais no
      longer matches" case.
      With the cause handled, per-segment detection went back to **cast personas only**,
      undoing the previous day's broadening. Not because it didn't work but because it
      charged every persona for one persona's problem — and it dragged in a
      `fallback_voice_for` hop that, for a cast persona, *discards* the bundle-chosen
      anchor the moment a segment lands outside the cast. That hop is gone too.
      py3langid stays: the Italian tutor's two-voice lesson is built on it
      (`bundles/italian-a0-starter/bundle.toml` — "decided automatically from the language
      you actually write each sentence in"), and the live DB has that cast installed
      (`{"it": "if_sara|im_nicola", "default": "af_heart"}`). Dropping the lib would have
      quietly made that persona's own prompt false.
      418 unit + 73 integration green (2 rewritten to record the reversal, 5 new). ruff
      clean. Spec: FR-708 rewritten, TR-305/TR-307 amended.
      Not yet live-verified — needs a real session: switch language, confirm the old
      phrasing no longer triggers, and confirm the tutor still alternates voices.

- [x] **Consolidation catch-up on connect (2026-08-02, FR-404)** — closes the starvation
      found the same day. The pipeline was idle-triggered only: a timer starts on
      disconnect, any new connection cancels it. Sessions a few minutes apart therefore
      cancelled every timer before it fired, while replay kept running on connect — so
      `conversations` filled up, nothing consolidated, long-term memory froze and the
      brief went 13 days stale.
      Catch-up reuses the condition the cancelled timer was waiting on rather than
      inventing a second rule (`consolidation_is_due`, extracted into `services/memory.py`
      so the policy is testable without a server): if the quiet gap since the previous
      session already exceeds `idle_consolidation_minutes`, that window *did* elapse and
      the work is due now — the timer simply was not around to notice.
      Runs as a background task so the session starts immediately, guarded so a rapid
      reconnect never starts a second pipeline. **Stated cost**: consolidation is LLM-heavy
      and Ollama serialises, so the first turn or two of a catching-up session may be
      slower. Better than never consolidating, and self-limiting — once the backlog clears,
      a connect with nothing due returns immediately.
      406 unit + 73 integration green (5 new covering the predicate, including the
      boundary case and first-ever-launch). Spec: FR-404 amended.

- [x] **Language-switch directive (2026-08-02, FR-708/FR-701, INV-1 amended)** — the first
      voice-configurable setting, requested after the first live sessions.
      **It required amending a hard invariant, so it was escalated first.** INV-1 said the
      live path "writes only JSONL session logs"; FR-701 says every voice-configurable
      setting is a DB-backed `User`/`AssistantPersona` attribute. Those contradict: a
      setting changed by voice must be written from the live path. Latent until now
      because nothing voice-configurable was ever wired. INV-1 now reads "no *memory*
      writes" and enumerates configuration writes as its one exception — a correction, not
      a loosening: it was written when the only conceivable live write was memory
      formation, which is what it exists to prevent.
      One `set_response_language` directive per installed language, in two phrasings:
      the target's own ("parle français") and the user's ("speak french"). The second is
      the one that matters — without it, asking for a language you cannot yet produce
      would require producing it. 13 rows generated on the real DB across 7 languages.
      *(The second phrasing was dropped again later the same day — see the entry below.)*
      Changes the **active persona's** `response_language` + default voice, persisted, so
      it holds across sessions. Deliberately does **not** touch `User.primary_language`:
      INV-10 pins episode summaries to it so months in another language never rewrite the
      user's life story, and INV-14 makes that its own explicit act. A tutor answering in
      its target language is likewise unaffected unless asked directly.
      Availability is re-checked at execution despite generation being installed-only — a
      directive row outlives the config that made it, and switching to a voice-less
      language yields silence, not speech.
      Schema: `directives.target_persona_id` becomes nullable, new `target_language`; a
      directive now names a persona **or** a language. **The integration suite caught a
      real bug here**: the ALTERs were first placed with the personas block, which runs
      *before* `CREATE TABLE directives`, so a fresh database failed — the live DB only
      accepted them because the table already existed.
      401 unit + 73 integration green. Spec: new FR-708, INV-1 amended.
      **Not yet live-verified** — the point is to test it in the next session.

- [x] **Four live-session defects fixed (2026-08-02)** — from the first real GA sessions,
      8 of them between 15:19 and 16:10. Diagnosed from `~/.local/share/mamemai/sessions/`.
      **1. Keepalive drops — root cause corrected.** `websockets` defaults `max_queue=16`.
      Audio is sent one chunk per synthesised sentence; on a long reply the client's queue
      fills while it plays, its reader *pauses*, TCP backpressure stops the socket
      draining, and the peer's ping sits unread behind the audio → `1011 keepalive ping
      timeout` mid-answer. The 2026-07-20 note blamed turn *duration* (19.3–19.5s vs a
      "20s default"); that was wrong — the real window is interval+timeout = 40s and
      neither event loop blocks. What correlates is reply *length*, which is what fills
      the queue, exactly as that note observed without identifying why. Fixed on both ends
      (`max_queue=64`, ping 30/60).
      **2. Lost context — three simultaneous causes.** (a) `session_tail_turns = 0` was
      left in `mamemai.toml` from directive testing, disabling FR-109 entirely; reverted to
      10. (b) 7 of today's conversations were unconsolidated. (c) The memory brief was
      stale from 2026-07-20. Root cause of (b)/(c): **consolidation starvation** — replay
      runs on *connect*, but the full pipeline only on a 5-minute *idle* timer that any
      new connection cancels. Sessions minutes apart therefore never consolidate, and the
      keepalive bug made sessions minutes apart. Ran consolidation manually: 7 processed,
      0 pending, 3 new organic concepts. `SSL (Secure Sockets Layer)` landed at
      **`explored`** — the engagement ladder built this morning firing on real data,
      because SSL was discussed across two conversations. **The starvation itself is not
      fixed** — short bursts under 5 minutes apart still never consolidate.
      **3. Repeated introduction — a real bug in the seeded GA prompt.** It said "if the
      user asks what you can do… deliver the onboarding introduction", but
      `ONBOARDING_SCRIPT` is only injected when `needs_onboarding`. So on every normal
      session the model was told to deliver content it had not been given, and improvised
      one — three times in today's logs. Narrowed to an explicit request only, with "a
      session that starts without much context is not a first session". Fixed in the
      migration seed *and* the live row (the seed is `ON CONFLICT DO NOTHING`).
      **4. Unwanted French — not the DB, and not previous turns.** STT misdetected the
      plainly-English *"Can you explain me TLS please ?"* as French; FR-114 rendered
      `[lang:fr]`; the model mirrored it and answered in French, overriding its standing
      "always respond in en". The English accent is a second thing: GA has no cast keys,
      so per-segment voice detection never runs and French text was spoken by `af_heart`.
      Fixed by making the tag a persona declaration (`sees_turn_language`, default false):
      the tutor declares it, nobody else sees it. This was already listed as a leak in
      BRIEF_persona_contract.md Evidence §4 ("GA pays prompt pollution for it") — it turned
      out to be worse than pollution.
      396 unit + 73 integration green. Spec: FR-114 narrowed.
      **Still open from this session**: the language-switch directive (item 5, FR-701
      territory), and consolidation starvation.

- [x] **Live verification on `linux_workstation_amdgpu` (2026-08-02)** — first workstation session since
      2026-07-30, clearing a nine-deep "not yet live-verified" backlog. Three findings,
      two of them real bugs.
      **1. A migration would have corrupted the memory store.** The DB was two migrations
      behind. The 2026-07-30 Directive work's `ALTER TABLE concepts DROP COLUMN IF EXISTS
      directive` was written on the premise that directive rows "were never in that
      table's result set to begin with" — true for a fresh DB, false for this one, which
      held 4 of them. Dropping the column strips the only marker identifying them as
      control-plane data, leaving "Switch to Tutor Italiano" et al. as ordinary GA-owned
      concepts — and the same commit removed the `WHERE directive IS NULL` filter from
      search/list, so they would have become RAG-searchable content. Fixed with a guarded
      `DELETE` before the drop (idempotent via an `information_schema` check).
      **2. `extracts_episodes` verified end-to-end on real data.** Backfill correct (GA →
      `true`, Tutor Italiano → `false`); re-apply is a true no-op; and a persona
      #2-shaped row (`strategy` **and** `extracts_episodes=true`) survives re-application
      — the exact case the nullable-then-constrain shape was designed for, now confirmed
      empirically rather than on paper. Full `pg_dump` taken first.
      **3. The 2026-07-30 integration failures are fixed** — see the concept-gate entry
      below. Integration suite now **73 passed, 0 failed** (was 2 failed since 2026-07-30).
      Also corrected: two integration tests I had broken myself by adding a required
      `persona_repo` to `ConsolidateMemory` without running the integration suite.
      **CLAUDE.md was stale in three ways** and is fixed: this machine *is* `linux_workstation_amdgpu` (not
      "a laptop"), its venv is not frozen, and `test_config.py` collects fine here.
- [x] **Concept gate split into provenance + significance (2026-08-02, FR-310/TR-609)** —
      root-caused the two integration tests failing since 2026-07-30, misdiagnosed then as
      "`_mentioned_in` rejecting a case that looks like it should pass". The real cause:
      `_mentioned_in` is whole-word literal, so a user saying "hiking" then "hiked"
      registers **once**, and `min_concept_engagement_turns=2` then discarded the concept.
      A silent false negative — the mirror image of the false positive the literal gate was
      introduced to fix.
      The floor was doing two jobs. Provenance ("did the user introduce this, or only the
      assistant?") is binary, and is the only job that gate can do well. Significance was
      being proxied by a turn count, which double-counted filters FR-307 already applies
      (the extraction floor drops thin conversations; worthiness explicitly rejects "small
      talk, greetings, trivial exchanges") and is a poor signal anyway — something
      important can be said once, something trivial five times.
      **Provenance:** floor 2 → 1. The 2026-07-20 sibling incident still resolves correctly
      at 1, because the user never literally named AI/NLP/Transfer Learning — that fix was
      always literal matching, never the count.
      **Significance:** a new ladder — an *organic* concept the user names again in a later
      conversation is promoted one `EngagementLevel`, saturating at `INTEGRATED`.
      Consolidation runs one conversation per transaction, so a promotion is by
      construction a distinct conversation, and recurrence is matched by embedding, so no
      stemming is needed in any of the 7 languages. This **activates a dead axis**: organic
      concepts entered at `MENTIONED` and `_max_engagement(MENTIONED, MENTIONED)` left them
      there forever, so `MENTIONED` meant both "said once" and "said every time" and
      `EXPLORED`/`INTEGRATED` were unreachable (confirmed on the real DB: 22 organic
      concepts, all `MENTIONED`; 38 authored, all `UNSEEN`).
      **Authored content is excluded from the ladder** — the load-bearing exclusion. It
      keeps the single `UNSEEN → MENTIONED` bump, nothing more: a persona whose activity
      elicits its own material (prompts for an item, user repeats it back) would otherwise
      manufacture the very signal FR-506 ranks on and walk its own curriculum to
      `INTEGRATED`. Mastery of authored content belongs to the owning persona's
      `persona_state`. Branches on `origin`, a generic first-class field — never on
      persona, so INV-6 holds.
      Answered along the way: organic and authored concepts **never merge across personas**
      (`upsert_concept` scopes its search by `persona_id`, INV-9), and within a persona a
      merge preserves `origin` rather than producing a hybrid — `origin` says where the
      text came from, `engagement_level` says how much the user engaged; orthogonal.
      366 unit tests (7 new for the ladder, 3 rewritten — one of which inverts a recorded
      2026-07-20 judgment and says so in its docstring) + 73 integration green. The
      persona-leak test caught a first draft naming the tutor in generic code. Spec: FR-310
      rewritten, TR-609 amended, GLOSSARY's *Engagement level* and *Concept engagement
      gate* rewritten.
      **Not yet verified against real usage**: whether one-off `MENTIONED` concepts
      accumulate as noise. Deliberately not calibrated against this machine's 11
      conversations — too small a sample to be evidence.

- [x] **Persona-leak test + `_TUTOR_DEBUG` rename (2026-07-31, INV-6)** — first item
      landed from [BRIEF_persona_contract.md](BRIEF_persona_contract.md), the design
      note on collapsing the four persona extension ports into one turn-shaped
      contract. New `server/tests/unit/test_no_persona_leak.py` fails if `domain/` or
      `services/` names a concrete persona's domain (`tutor|CEFR|HVPT|learner`);
      `lesson`/`vocabulary` deliberately excluded as legitimate generic vocabulary.
      Making it pass took 25 comment/docstring rewordings plus the rename of the last
      persona-specific identifier in the core (`_TUTOR_DEBUG` → `_PERSONA_DEBUG`, env
      `MAMEMAI_TEST_TUTOR_DEBUG` → `MAMEMAI_TEST_PERSONA_DEBUG`, trace prefix
      `[tutor-debug]` → `[persona-debug]`). Generic `infrastructure/` is knowingly out
      of scope (4 remaining sites, listed in the brief). No CI runner exists yet — the
      test runs under the CONTRIBUTING pre-PR gate.
- [x] **Stray bracket markers never spoken (2026-07-31, FR-106/TR-308)** — first item
      from [BRIEF_inband_tags.md](BRIEF_inband_tags.md). Closes the 2026-07-18
      cast-role bracket bug (`[SPEAKER:default]`, `[TARGETTEACHER]`, `[GUIDE]` read
      aloud as "bracket speaker colon guide bracket") and stops the localized
      `[Focalizza: …]` case being spoken. **The premise in the brief was wrong**: the
      bundle prompt has said "there is no tag or marker to place" since TR-305
      (2026-07-13), so the model emits these *unprompted* — the generic
      `_SPOKEN_MARKER` strip in `_strip_markdown` is the fix, not the backstop.
      Marker-shaped only (one unspaced token + optional `:` payload), so `[1, 2, 3]`
      and `[see below]` survive. Bundle prompt also strengthened to a positive
      prohibition, but that reached **new installs only** — `InstallPersonaBundle`
      ignored `[persona]` for an existing persona (upgrade semantics deferred).
      **No longer true as of 2026-08-02**: a reinstall now refreshes the authored half of
      the definition, `system_prompt` included, so this fix reaches existing installs by
      reinstalling the bundle (see the persona-only bundle entry below).
- [x] **Confident STT hallucinations blocked (2026-08-01, FR-103/TR-956)** — closes the
      second of the two 2026-07-18 live-testing bugs (the first was the cast-role
      brackets above). A silent mic transcribed as "Thanks for watching, and I'll see
      you in the next video." and *cleared* the `no_speech_prob >= 0.6` filter: Whisper
      was confident about the hallucination, so the acoustic filter structurally could
      not catch it. Third line of defence added in `infrastructure/stt.py` — a
      stock-phrase blocklist matched per segment on normalised text (punctuation, case
      and diacritics folded for matching only), covering the YouTube-outro and
      caption-credit families incl. the Italian variants. Per-segment so a bad segment
      never discards real speech beside it; every drop prints an `[stt] dropped stock
      hallucination` line, since a false positive would otherwise silently eat a turn.
      22 new unit tests (pure text, no model needed). **Blocklist entries are extended
      from observed output, not speculation** — that bar is in TR-956.
- [x] **In-band response tags retired (2026-08-02, TR-304 retired; FR-112 retired,
      FR-502 suspended)** — [BRIEF_inband_tags.md](BRIEF_inband_tags.md), done in one
      step instead of the three staged there, because investigating step 2 collapsed it.
      **The finding: `[TOPIC_BREAK]`/`[TOPIC_CONTINUATION]` had a parser but no
      producer.** No system prompt, bundle, config or wizard template has ever asked a
      model to emit them — so the only mid-session `BREAK` that ever fired is the
      deterministic persona-switch override (TR-315, 2026-07-20), which is exactly what
      the 2026-07-20 live test saw when a GA→tutor→GA session collapsed into one
      `Conversation` row. "Move boundary classification offline" was therefore not
      relocating working machinery but **building a classifier that never existed**;
      retiring the parser costs nothing that was running. `[FOCUS:]` was the opposite —
      a real feature with exactly one producer, the tutor bundle prompt.
      Removed from `services/session.py`: `_try_resolve_prefixes`, `_extract_tag`,
      `_tag_might_still_open`, `_resolve_boundary_marker`, `_BOUNDARY_MARKERS`, and
      `_PREFIX_SCAN_WINDOW_CHARS` — the 200-char lead-in buffer every persona paid on
      every turn. The streaming loop now synthesises each sentence the moment it
      completes with nothing in front of it (the real win: first-audio latency).
      Kept deliberately: the persona-switch break (deterministic, load-bearing, never an
      LLM signal — only co-located with the tag channel); `ConversationBoundaryType`, the
      log's marker field and `replay.py` untouched, so INV-5's kept-forever logs stay
      readable; and `select_items(focus=…)`/`TutorFocus`/`FocusInterpreter`, which cuts
      against the no-orphans rule by explicit decision — the replacement is user-turn
      detection and rebuilding the interpreter for it would be churn.
      **Two gaps now open by decision, both recorded in the spec rather than left
      implicit**: (1) no session wish reaches selection, every persona gets its default
      batch; (2) two unrelated topics in one uninterrupted single-persona stretch
      consolidate as one conversation — one worthiness verdict, one summary, extraction
      over mixed content. Both wait on the same thing the contract does: a second persona
      to design against and real sessions to calibrate with.
      344 unit tests green; 6 rewritten from "the tag works" to "the tag is inert",
      following the `test_persona_tag_in_llm_response_is_now_inert` precedent. `ruff
      check` clean. **Not yet live-verified**: the first-audio latency improvement is the
      whole point and is unmeasured — needs a real workstation turn to confirm, and it is
      the one item on the Phase 12/13 live-smoke lists that this change makes newly worth
      measuring.
- [x] **Episode-extraction proxy retired (2026-08-02, FR-407)** — first sequencing item
      from [BRIEF_persona_contract.md](BRIEF_persona_contract.md), promoted ahead of the
      rest when designing persona #2 showed it blocked a bootstrap path, not just
      correctness: concepts already flow for strategy personas (`memory.py:129-137`,
      deliberate) but episodes were cut off, so the cognitive persona's
      autobiographical-recall family could never be filled by its own sessions.
      `extract_episodes = strategy is None` is replaced by `AssistantPersona.
      extracts_episodes` — a **first-class attribute**, not a `settings` key, precisely
      because generic code must read it and INV-6 keeps `settings`/`persona_state` opaque.
      A registry keyed on `strategy` in the composition root was considered and rejected
      as the same proxy in different clothing. Named for the fact (*does this persona's
      conversation record real events?*) rather than the mechanism, so it survives a
      persona that assesses **and** remembers — the combination the proxy structurally
      could not express, and exactly what persona #2 is.
      `ConsolidateMemory` gains a `PersonaRepository` dependency (wired to
      `ctx.offline_persona_repo`) and falls back to the generic default (extract) only if
      the persona row is missing — shouldn't happen, FK-backed.
      **Migration is idempotent by construction**: the column is added *nullable*,
      backfilled `(strategy IS NULL)` only `WHERE extracts_episodes IS NULL`, then
      constrained `NOT NULL DEFAULT TRUE`. Since `001_initial_schema.sql` is re-applied on
      every start, a naive `ADD COLUMN ... DEFAULT TRUE` + blanket `UPDATE ... WHERE
      strategy IS NOT NULL` would have re-clobbered persona #2 back to `false` on the next
      restart — the nullable-then-constrain shape is what prevents that.
      Bundle format carries `[persona] extracts_episodes` (bool, rejected not coerced);
      the shipped Italian tutor bundle declares `false` explicitly; `save()`'s UPDATE
      branch carries the column so a rename/deactivate can't silently reset it.
      336 unit tests green (up from 331 — 3 consolidation cases incl. an explicit
      regression that an assessment strategy alone no longer suppresses episodes, 3
      bundle-format cases, 1 install pass-through; 2 new Postgres integration tests for
      the round trip and the update path, not runnable on this laptop). `ruff check`
      clean. Spec: FR-407 rewritten, INV-10/TR-703/TR-706/TR-903 amended, GLOSSARY gains
      **Rehearsal persona** + a rewritten **Consolidation gates** entry stating the two
      gates as the separate questions they always were; `docs/AUTHORING_BUNDLES.md`
      documents the new key.
      **Not yet live-verified**: needs a workstation run to confirm the migration applies
      cleanly to the existing DB and the seeded tutor persona lands on `false` via the
      backfill (its row predates the bundle key).
- [~] **Persona #2 — the cognitive persona: first slice built (2026-08-02)** —
      [BRIEF_persona_cognitive.md](BRIEF_persona_cognitive.md). `infrastructure/cognitive/`
      (selection + assessment + its own `persona_state` vocabulary), a persona-only bundle
      at `bundles/cognitive-coach/`, registered in both factory registries.
      **End-to-end verified against real Postgres** (test DB): the bundle installs with 0
      items, `reads_across_personas`/`extracts_episodes` persist, `response_language` is
      derived from the user (a French user got a French-speaking Coach from an
      English-authored bundle), and the switch directive is auto-created in French
      ("basculer", "coach").
      **Two gaps found while building, neither in the brief's four.**
      *Gap 5* — `list_items` raises for episodes, so the autobiographical family (the
      brief's "most motivating") had no way to reach its material at all. Fixed with
      `list_episodes`, recency-ordered; additive, no spec argument.
      *Gap 6, the serious one* — **FR-303 made the persona impossible as designed.**
      Concept/procedure knowledge is persona-scoped end-to-end, so a persona that exists
      to quiz the user on *their* concepts could only ever see concepts extracted from its
      own sessions: it could test you on what you told it while it was testing you. The
      brief asserts this persona "reads the whole store" and never noticed the store
      forbids it. Resolved by separating **read** scope from **write** scope:
      `AssistantPersona.reads_across_personas` (default false, so nothing else changes),
      read-only, INV-9's cascade and every write path untouched. Episodes never needed it
      — INV-10 already made them shared, which is what let the tutor anchor to the user's
      life.
      Two further format assumptions only held for language personas and were relaxed:
      `languages` may be empty (no restriction, not narrowed to primary) and
      `response_language` may be absent (derived at install), both mirroring the existing
      pair-independence rule for `voices["default"]`.
      Design choices worth noting: family rotation is **per day, not per session** —
      `select_items` gets no session id, and the date makes it deterministic across a
      reconnect. Assessment uses a **transparent heuristic, not an LLM judge**, and errs
      toward "retrieved": a false miss silently shortens an item's half-life and hammers
      something the user knows, and no one can audit a verdict they never see.
      393 unit tests (14 new) + 73 integration green.
      **Deferred, unchanged from the brief**: delayed-recall debt (Gap 2 — still the first
      thing this persona will hit), divergence-flag persistence (Gap 4; the *spoken* half
      is in the prompt and pinned by a test), voice-selectable exercise type (declared
      commands), curated reasoning items (third item kind).
      **Live-verified 2026-08-07**: installed into the real `mamemai` DB (`Coach`,
      `response_language=fr` auto-derived, `reads_across_personas=t`), server run against
      real Postgres + real `llama3.1:8b`. First real finding: the switch trigger itself
      was unreachable. `matches_trigger` (`services/directives.py`) is a plain
      case-insensitive substring check, and the French keyword was the bare infinitive
      `"basculer"` — but nobody says that; live attempts ("bascule vers mon coach", "on
      bascule vers le coach", "qu'on bascule au coach") all used conjugated/nominal forms
      that don't contain the substring "basculer", so none matched. (Two attempts also
      independently exceeded `max_words=6`.) Fixed by keying French on the shared root
      `"bascul"` instead of the full infinitive — matches every conjugation deterministically,
      no conjugation table, same substring mechanism. `docs/PERSONAS.md`'s language table
      updated to match. Not evaluated: whether the same bare-infinitive/conjugation gap
      exists for es/it/pt — untested live, no evidence either way.
      **Round 2, same evening**: with the trigger fixed, the switch fired mechanically on
      the first correctly-worded attempt (verified in the session JSONL: `marker: break` +
      `persona_id` flip to Coach right after "basculons vers le coach"; every later
      "bascule…" was a correct silent no-op, target already active) — but it didn't *feel*
      like it worked. `llama3.1:8b`, still holding this session's `recent_turns` from
      before the switch plus the unscoped memory brief (FR-109, reaches every persona
      regardless of `reads_across_personas`), kept GA's topic and even claimed it "can't
      switch personas" for several turns. Fixed with a `switched_to_persona_name`
      injection symmetric to the existing blocked-switch one (TR-315) — "this already
      happened, own it, don't continue the old topic." Separately, Coach's one-exercise-
      per-turn design (TR-306) kept gluing exercise questions onto farewell replies
      ("Bye" → goodbye + "Comment compterais-tu les objets dans un panier…"), since
      nothing in the pipeline recognized session-ending intent. Rather than heuristic
      exit-intent detection, modeled it the same deterministic way as persona switching
      (user's call, 2026-08-07): a third `Directive.action`, `end_session` (FR-115,
      TR-317) — one closed-vocabulary farewell word per installed language
      (`ensure_end_session_directive`, same idempotent/prune posture as the language
      directives), skips recall/selection like any directive turn, injects a
      goodbye-only instruction, and now actually closes the WebSocket connection after
      the reply (`TurnResult.session_ended` → `server.py`'s handler breaks its loop
      instead of continuing) — verified the client has no reconnect loop around its own
      connection, so this really ends the session, not just that turn's content.
      559 unit tests green (14 new across `test_session.py`/`test_directives.py`).
      **Round 2 live-verified same evening**: all three fixes confirmed against the real
      server/client. Switch confirmation: "Bascule vers mon coach" → Coach owns the role
      immediately, no more "can't switch" denial; later correctly self-identified when
      asked "Est-ce que tu es mon coach?". End-session directive: "C'est tout, au
      revoir!" matched and produced a clean single goodbye, no glued-on exercise
      question. Connection close: `session_closed`/`clean_exit: true` logged in the same
      second as the goodbye reply, and `ss -tnp` confirmed no active TCP connection on
      :8765 afterward while the server itself stayed up for the next one.
      **Still open, unrelated to round 2**: Coach's very first reply after switching
      still opened with the fabricated "aurores boréales" topic — the unscoped
      `memory_brief` (FR-109) reaching every persona regardless of
      `reads_across_personas`, flagged in round 1, not yet fixed. Also: this session's
      exercises ran mostly on Coach's own invented content (word lists, stories) rather
      than the user's real concepts/episodes, only switching to real-content exercises
      once the user explicitly asked for "un exercice de mémoire" — worth checking
      later whether `_next_selected_item`/pacing is under-serving real items each turn,
      or whether the store is just sparse and the model fills the gap with invention;
      not yet diagnosed.
      **Open thread, not yet designed (2026-08-07)**: an "erase this (false) episode"
      capability — user hit this live, repeatedly asking Coach to forget the fabricated
      aurora-borealis episode. Coach's own bundle system prompt already anticipates the
      shape ("do not decide which is correct... suggest they sort it out with the
      general assistant, which can correct the record") but the model didn't follow it
      live, just apologized and moved on — an instruction-following gap, not a missing
      design. The harder gap: GA has no erase mechanism either yet. Unlike
      `switch_persona`/`end_session`, "erase" can't be a pure closed-vocabulary
      Directive — those need no interpretation, but "erase this episode" needs to
      identify *which* one. Leading idea, undeveloped: target whatever memory item is
      currently in the turn's working context (just recalled/discussed) rather than
      parsing a description, keeping the trigger itself deterministic. Reassuring
      constraint either way: only the *extracted* Episode/Concept row would be deleted —
      the raw JSONL session log stays untouched and permanent (INV-5), so the source
      material survives even a wrong erase, recoverable in principle by re-extraction.
- [ ] ~~**Persona #2 — the cognitive persona**~~ (original entry) —
      [BRIEF_persona_cognitive.md](BRIEF_persona_cognitive.md), designed 2026-08-01. A
      cognitive-training persona (short frequent voice sessions: story recall, retrieval
      practice over the user's own concepts/procedures, autobiographical recall) chosen
      because the author will actually use it *and* it is deliberately dissimilar to the
      tutor — a consumer of the memory substrate rather than an author of content, no
      target language, no cast, anti-curriculum selection. Grounded deliberately in what
      the evidence supports (near transfer, retrieval practice, cognitive reserve) and
      not in the far-transfer claims commercial brain training is built on. Checking its
      needs against the code produced **four verified gaps** (no content on the
      activation turn, `session.py:688`; no per-persona runtime state for a delayed-recall
      debt; selection policy hard-coded generically while this persona needs three; no
      durable emission addressed to another persona) and **one reverse leak** — the
      retention/half-life machinery is a *generic* capability trapped inside the tutor.
      Two of the six candidates were disproved on inspection, including a "speak first"
      hook that turned out unnecessary (sessions always start on GA, so the persona is
      always entered by a switch directive, and the opening is that turn's reply).
- [x] **Retention formula freed into `domain/` (2026-08-02, FR-506/TR-802)** — second
      sequencing item from [BRIEF_persona_contract.md](BRIEF_persona_contract.md), and the
      first repair of the *reverse* leak: not persona logic escaping into generic code but
      a generic capability trapped inside a persona. New `domain.model.retention(
      days_since, half_life_days)` (beside `cosine_similarity`); the tutor's `_retention`
      (`infrastructure/language_tutor/selection.py:69`) now reads its own `persona_state`
      keys and defers the curve. **Only the arithmetic moved** — key names
      (`STATE_HALF_LIFE_DAYS`), the growth-on-success/shrink-on-error policy, and the
      `-1.0` "no usable state, most due" sentinel all stayed with the tutor; a domain
      function that looked up `"half_life_days"` itself would have been the INV-6
      violation this was meant to avoid, dressed as a refactor.
      Two boundary calls: a non-positive `half_life_days` **raises** rather than returning
      a sentinel (what an unusable SRS state ranks as is persona policy — generic code
      must not answer it), while a negative `days_since` (clock skew) is clamped to 0,
      which is degenerate-input arithmetic and matches `cosine_similarity`'s zero-vector
      posture in the same module.
      Tutor behaviour is unchanged — `_retention` returns identical values. 345 unit tests
      green (up from 336 — 9 new in `tests/unit/domain/test_retention.py`), `ruff check`
      clean. One documented float64 limit found while testing: past ~1074 half-lives the
      curve underflows to exactly 0.0 and such items tie, harmless for ranking precisely
      because the persona's sentinel is negative. Spec: TR-802 amended; GLOSSARY's
      **Retention** entry moved out of the language-tutor tier into *Recall & selection*
      and rewritten to say which half is generic.
- [x] **Persona-only bundles + `[persona]` update semantics (2026-08-02, FR-601/TR-901/
      TR-904)** — the last format-level blocker on shipping persona #2.
      **Persona-only.** `bundle_toml.py` rejected any bundle with no lesson files, while
      the format explicitly supported the mirror case (content-only omits `[persona]`).
      That asymmetry was accidental, and it meant a persona which *consumes* the memory
      substrate rather than authoring curriculum — exactly the cognitive persona — needed
      a dummy lesson file to install. The rule is now "a bundle must carry a persona, or
      content, or both"; only the genuinely empty bundle is rejected.
      **Update semantics, deferred since Phase 11, now settled.** A reinstall used to
      ignore `[persona]` entirely. Tolerable for a content bundle; fatal for one that is
      *nothing but* `[persona]`, which could then never be updated at all. The split is by
      **who can know the value**: the bundle refreshes `system_prompt`, `settings`,
      `extracts_episodes` and `languages` (author-owned — an improved prompt is the main
      thing an upgrade delivers); it never touches `name`, `voices`, `speaking_rate` or
      `response_language` (all voice-editable — a content update silently undoing a spoken
      preference is the worst kind of surprise). `strategy`/`persona_key` stay immutable,
      so a bundle cannot rebind an installed persona to different code.
      Same shape as two rules already here: `upsert_concept` keeps curated wording
      immutable while the user's engagement moves (TR-609), and `persona_state` is written
      only by its owner (INV-6). Each side owns its fields. Unconditional rather than
      version-gated — comparing versions needs a semver ordering the format doesn't
      specify.
      Concrete payoff beyond persona #2: the 2026-07-31 marker-prohibition prompt fix was
      recorded as reaching "**new installs only**" for exactly this reason. It now reaches
      existing installs too.
      372 unit tests (6 new: persona-only parse/install/log, reinstall-updates,
      reinstall-preserves-voice-edits, strategy-never-rebound) + 73 integration green;
      3 rewritten from the old ignore-on-exists behaviour. Spec: FR-601, TR-901, TR-904
      rewritten; GLOSSARY *Persona bundle*; `docs/AUTHORING_BUNDLES.md` gains two author-
      facing rules.
      **Deliberately not done**: the third item kind (exercise protocols / validated
      reasoning items). A persona-only bundle carries no items at all, so it isn't a
      blocker — it becomes one only when the cognitive persona ships curated content, and
      it is a change to a versioned format third-party authors target. Separate piece of
      work, tracked in [BRIEF_persona_cognitive.md](BRIEF_persona_cognitive.md).
      **Not yet live-verified**: no persona-only bundle has been installed against the
      real DB — the fixture bundle used by the integration suite still has lessons.
- [x] **Selection pacing declared by the persona (2026-08-02, TR-306)** — sequencing item
      4 from [BRIEF_persona_contract.md](BRIEF_persona_contract.md), the last prerequisite
      before persona #2 can be built.
      Two rules lived in `ProcessTurn` and were therefore every persona's law: an
      exhausted batch is never re-queried, and the turn a directive switches to a persona
      brings no content. Both are reasonable UX choices *for a planned-lesson persona*;
      the second blocks persona #2 outright (Gap 1 in the cognitive brief — its session
      opening *is* its first exercise, so it would greet empty-handed and burn a turn of a
      ten-minute session). Now declared: `SelectionPacing` on `PersonaSelectionPort`, with
      `refetch_when_exhausted` and `include_activation_turn`. Defaults reproduce the old
      behaviour exactly, and a strategy declaring no `pacing` gets the default via
      `getattr` — absence stays the default rather than boilerplate in every strategy, a
      property the contract brief explicitly wants preserved. The tutor now states its two
      choices in its own module instead of having them assumed by the pipeline.
      One rule is unconditional regardless of declaration: a **blocked** switch never
      counts as an activation turn — nobody was activated, the active persona is
      explaining that another is unavailable, which is no moment to hand it an exercise.
      **Narrower than the brief sketched, for a real reason.** "Shrinks `WorkingMemory`"
      did not happen: `selection_batches` is *session* state and strategies are
      startup-constructed singletons, so a strategy holding its own batch would leak it
      into the next session. Only the policy moved. That is now recorded in the contract
      brief as fresh evidence for its "no home for cross-session persona state" gap —
      reached from the opposite direction: not that personas *want* durable state, but
      that generic code cannot hand them session-scoped state without inventing a place
      to put it.
      Also deliberately not done: **items-per-turn**. No evidenced need — the "three
      policies at once" argument in the cognitive brief turns out to be about *selection*
      (composing one batch from novelty/forgetting-curve/salience rankings), which was
      always free inside `select_items` and never blocked. Making it configurable would
      ripple through the injection renderer for a hypothetical.
      375 unit tests (3 new: refetch re-queries, activation turn brings content, blocked
      switch never activates) + 73 integration green; 1 renamed to say its default is now
      *declared* rather than hard-coded. `ruff` clean. The persona-leak test again caught
      a first draft — the port's own comment justified the defaults in terms of the tutor.
      Spec: TR-306 rewritten; GLOSSARY gains **Selection pacing** and rewrites **Selection
      batch**.
      **Not yet live-verified**: no persona declares non-default pacing yet, so neither
      flag has run in a real session — persona #2 is the first consumer.
- [x] **GA-unchanged regression guard (2026-08-02, FR-201/INV-6)** — insurance taken
      before building persona #2, prompted by asking whether that work can touch GA.
      The answer is *not structurally*: GA is nowhere special-cased in `ProcessTurn`, it
      is simply the persona with `strategy = None`, so **the shared turn pipeline IS GA's
      code path**. Today's four prerequisite changes leave it behaviourally identical
      (every default reproduces the old rule; pacing isn't even consulted, since
      `_next_selected_item` returns at `strategy is None` first). But the gaps persona #2
      still faces — runtime state, durable emission, declared commands — can only be
      solved by editing code GA executes.
      New `TestGeneralAssistantIsolation` (`test_session.py`): runs the same GA turn with
      and without a second strategy-bearing persona registered — with its own directive,
      its own selection strategy, **non-default pacing on both flags**, and its own
      episode declaration — and asserts the entire LLM call (system prompt + every
      message) is byte-identical, the other persona's strategy is never consulted, and no
      spurious switch/boundary/log write appears. Complements
      `test_no_persona_leak.py`, which guards persona *vocabulary* in generic code; this
      guards persona *behaviour* reaching another persona's turn, which nothing covered.
      **Mutation-tested rather than assumed**: a plausible regression (falling back to any
      registered selection strategy when the active persona has none) makes 2 of the 3
      fail. The third does not, by construction — `FakeLLMService` returns a canned
      response whatever prompt it gets, so it cannot detect prompt drift; its docstring
      says so and states the axis it does guard.
      378 unit tests green.
- [ ] **Decide the persona-contract sequencing** — the brief's prerequisite (a
      deliberately dissimilar persona #2) is now satisfied on paper; its Part 3 is the
      evidence the contract was waiting for. Two elements were added to the proposed shape
      from it: **durable emission** (a persona producing something generic addressed to
      another persona — the divergence flag) and **declared commands** (persona-scoped
      `Directive.action` values that generic code routes without interpreting; today it is
      a single `== "switch_persona"` test at `session.py:621`). Still independent of the
      contract: moving pacing policy out of `_next_selected_item`.

- [x] **Recall gating replaces the per-turn LLM classification call (2026-07-16,
      FR-309/TR-314)** — prototyped per discussion: `RecallIntentDetector` (an LLM call
      classifying "remember when…" intent + extracting a query + memory types on
      *every* turn, local or remote) was judged fragile/over-designed and retired
      outright, not kept alongside the replacement. New `RecallGate` port
      (`services/ports.py`, in the persona-extension-port family) — `should_embed(text)`
      short-circuits trivial short utterances before any embedding is computed;
      `should_search(max_similarity_to_prior_searches)` skips a fresh DB round trip when
      the turn's embedding is nearly identical to *any* prior search this session for
      the active persona — not only the immediately preceding one — reusing that
      search's cached results instead of paying for another lookup. Persona-scoped like
      `PersonaSelectionPort` et al., but with a real default rather than a no-op
      fallback: `DefaultRecallGate` (`infrastructure/recall_gate.py`, `min_words=3`,
      `dedup_threshold=0.93` — the latter reuses the existing merge-threshold "same
      thing" bar rather than a new placeholder) covers GA and anything unregistered;
      `LanguageTutorRecallGate(DefaultRecallGate)` overrides only `should_embed` to
      always return `True` — a tutor's one-word vocabulary answers are exactly the
      content worth searching, unlike GA's throwaway "yes"/"ok" replies. New pure
      `domain.model.cosine_similarity` (general formula, doesn't assume L2-normalised
      input) compares the current turn's embedding against **every** entry in
      `WorkingMemory.recall_history[persona_id]` (persona-keyed list, oldest first, of
      every real search's embedding + results this session) entirely in-process — no DB
      round trip for this comparison, distinct from pgvector's own `<=>` operator. No
      more type-restricted search (`memory_types` classification) or extracted-query
      text — the turn's raw text is embedded directly and every `MemoryType` is
      searched, since the existing top-5-by-similarity ranking already sorts it out and
      the old classification step bought less precision than it looked like.
      **Design refinement caught before commit**: comparing only against the *last*
      search (the first cut) missed that nothing new can enter long-term memory
      mid-session (INV-1) — the searchable set is frozen for the whole conversation, so
      a repeat of *any* earlier query, not only the immediately preceding one, would
      deterministically return the same results again. Reworked to cache every real
      search this session (not just the latest) and take the max similarity across the
      whole history — same port signature, no interface change, just what the argument
      represents and what `ProcessTurn` computes before calling it.
      **Real side-effect, not a target of this change**: removing recall from the live
      path also simplified server.py's provider branch — recall no longer needs an
      OpenAI-compatible twin at all (it was never really an LLM call's business to vary
      with `[llm].provider`), so `OpenAICompatibleRecallIntentDetector` was deleted
      rather than kept. `RecallTriggered`/`RecallSource` (domain events) and
      `RecallIntentDetector` (domain protocol) removed entirely — nothing else
      constructed them once the mechanism was gone, and this project doesn't keep
      orphaned code around "just in case."
      265 server unit tests green (up from 245 — new `test_cosine_similarity.py`,
      `test_recall_gate.py`, `TestRecallGating` in `test_session.py` including a
      dedicated test proving an *older*, non-latest history entry is found and reused
      over a more recent unrelated one; the existing weak recall test was rewritten to
      actually assert recalled content reaches the LLM's system prompt, not just that
      some LLM call happened). `ruff check` clean on every touched file. Spec: FR-302
      reworded (no longer "explicit recall intent"), new FR-309; TR-302 reworded, new
      TR-314; TR-955 corrected (recall no longer varies with `[llm].provider`);
      GLOSSARY's `Recall` entry rewritten, new `Recall gate` entry.
      **Not yet live-verified**: word-count/dedup-threshold defaults (3 words, 0.93)
      are reasoned choices, not calibrated against real conversations — same
      calibration-pending posture as every other threshold in this project. Worth
      watching once real usage exists: does 3 words correctly separate "yes"/"no" from
      genuine short recalls; does 0.93 dedup correctly catch topic-repeats without
      suppressing a genuinely new question phrased similarly to a recent one; whether
      an unbounded per-persona `recall_history` list ever needs a cap in practice (not
      added speculatively — realistic session lengths keep it small).
- [x] **Wizard: identify non-NVIDIA GPUs (2026-07-16)** — prerequisite for the public-LLM
      item below, done first per discussion: a real AMD Ryzen AI APU box (Linux) had
      Ollama accelerating the LLM perfectly well, but the wizard's `NvidiaSmiGPUDetector`
      (CUDA-only via `nvidia-smi`) reported a flat "no GPU detected," understating what was
      actually there. `GPUDetector` (renamed adapter: `SystemGPUDetector`,
      `infrastructure/gpu.py`) gained `detect_gpu() -> DetectedGPU | None`
      (`domain/model.py`), a Linux-only sysfs fallback (`/sys/class/drm/card*/device`:
      PCI `vendor` id → amd/intel/nvidia/unknown; amdgpu's own `mem_info_vram_total` +
      `mem_info_gtt_total` summed for a real memory estimate — the same sysfs fields
      Phase 12's archived findings used by hand to characterize this exact box's ~38.8 GB
      GPU-reachable memory), called only as a fallback when `detect_vram_gb()` (unchanged,
      still CUDA-only) finds nothing. Wired into two of the three steps that inject
      `GPUDetector`, deliberately not the third: `DetectComputeDevice`'s message now names
      the GPU instead of implying nothing is there (`compute_device` itself is unaffected —
      still `cpu`, no ROCm STT/TTS adapter exists); `SelectLLM`'s fit hints now use the
      identified GPU's memory when available, since Ollama can actually place the LLM on
      it; `ResolveSTTEngine` deliberately left untouched (Whisper runs on CPU regardless of
      GPU vendor, so a non-CUDA memory figure has no bearing on that model-size choice).
      9 new infra unit tests (`test_gpu.py`, fake sysfs trees via `tmp_path`) + 6 new/updated
      step tests; 83/85 setup unit tests green (the 2 failures are the pre-existing,
      already-tracked Windows chmod gap). Not yet live-verified against a real AMD box in
      this session (this laptop has none) — worth confirming on the Strix Halo workstation
      next time it's touched.
- [x] **Public/cloud LLM adapter as a wizard-selectable option (2026-07-16, FR-707/TR-955)**
      — live conversation can now use any OpenAI-compatible remote endpoint (OpenRouter,
      OpenAI, a self-hosted vLLM/LM Studio server, ...), toml-direct API-key storage per
      discussion (same precedent as `database.url`'s plaintext password, 0600-permissioned).
      **Split out `OpenAICompatibleLLMService`/`OpenAICompatibleRecallIntentDetector`**
      (new `infrastructure/llm/openai_compatible.py`) from `OpenRouterLLMService`/
      `OpenRouterRecallIntentDetector` — they were already a generic
      `openai.AsyncOpenAI(api_key=, base_url=)` wrapper defaulting to openrouter.ai, so
      the rename/move is mostly a naming-accuracy fix (CLAUDE.md's push-back-on-imprecise-
      naming rule), not new logic; `base_url`/`model` now required (no generic default
      makes sense), `api_key` optional (coalesced to a placeholder string — the openai
      client wants some value even against endpoints that don't check one). The offline
      OpenRouter family (worthiness/disambiguation/synthesis/extraction) is untouched,
      stays un-wired (TR-953).
      **Real design gap found and fixed before wiring**: `RecallIntentDetector.detect()`
      is ALSO a live, per-turn, blocking-before-reply LLM call (`ProcessTurn`, before the
      main completion) — moves together with `LLMService`, not left on local Ollama, or a
      remote-live install would still pay a CPU-inference tax every turn.
      **Second real gap found**: `GenerateMemoryBrief` (offline) was reusing the same
      `ctx.llm` object as live conversation — swapping `ctx.llm` to remote would have
      silently sent brief-generation prompts to the third party too. Fixed: new
      `ServerContext.offline_llm`, a dedicated always-Ollama `OllamaLLMService` instance,
      never aliased to the live `llm` (two instances constructed even when both are
      "ollama" — simpler/safer than special-casing one provider).
      **Config** (`infrastructure/config.py`): `[llm].provider` ("ollama" default |
      "openai_compatible"), `base_url`, `remote_model`, `api_key` — fail-fast
      `RuntimeError` at config-load time if `openai_compatible` is missing `base_url`/
      `remote_model`. `model`/`ollama_host` keep meaning "the local Ollama model," now
      always for the offline pipeline regardless of `provider` — fully backward
      compatible (absent `provider` ⇒ `"ollama"`, identical behaviour to before this
      existed). `server.py`'s composition root branches once on `cfg.llm_provider`;
      the `openai_compatible` import stays inside that branch (not a static top-level
      import) so a fully-local deployment's import graph doesn't touch `openai` at all,
      matching the existing lazy-reexport posture in `infrastructure/llm/__init__.py`.
      **Wizard**: new `ConfigureLLMProvider` step (before `SelectLLM`, now flow step 6 —
      every later step's docstring number shifted +1) asks local-vs-remote, collects
      `base_url`/`remote_model`/optional `api_key` when remote (blank key ⇒ `None`, not
      `""`); re-run pre-fill marks the current choice `(current)`, matching `SelectLLM`'s
      convention. `SelectLLM` (unchanged catalogue/pull logic) always still runs
      regardless of the live choice — its prompt text says so explicitly when provider is
      remote, so picking an Ollama model right after choosing "remote" doesn't read as
      contradictory. `ShowWelcome`'s stale "no cloud fallback" prerequisite bullet fixed;
      `cli.py`'s completion summary now reports both the live and offline model when
      remote. `TomlConfigWriter` omits `provider`/`base_url`/`remote_model`/`api_key`
      entirely for the common local case (minimal-diff-for-default posture, matching
      `tts_device` etc.) — the reader's own defaults have to be right for that to be
      correct, so the two were designed and tested together.
      **Spec**: FR-707 (FUNCTIONAL.md), TR-951/952/953 updated + new TR-955
      (TECHNICAL.md), GLOSSARY's `LLM` entry (TECHNICAL.md/GLOSSARY.md);
      `server/config/mamemai.example.toml` documents the new keys (also fixed a stale
      `docs/INSTALL_SERVER.md` cross-reference left over from the doc-consolidation
      commit). 245 server unit tests green (up from 233 — 12 new `test_config.py` cases;
      that module needed a local `platformdirs` shim to even collect on this laptop's
      frozen venv, same known gap as ever, not a new one) + 98 setup unit tests green (2
      pre-existing Windows chmod failures, unrelated). `ruff check` clean on every touched
      file in both packages.
      **Not yet live-verified**: no real OpenAI-compatible endpoint, no GPU-less box, and
      no `openai`/`psycopg` in this laptop's frozen venv to actually run `mamemai-server`
      end-to-end here — next real verification needs a workstation (or any box) with
      network access to a real endpoint, confirming: `uv sync` picks up `openai` cleanly,
      a live turn actually completes via the remote endpoint, recall detection round-trips
      correctly, and the offline pipeline still produces a memory brief via local Ollama
      while `provider = "openai_compatible"` is set.
- [x] **Native Windows `mamemai-server` support** (2026-07-17) — root-caused and fixed the
      numpy half of the earlier 2026-07-16 finding: the resolver was anchored to
      `numpy==1.26.4` (no cp313 Windows wheel) not by Kokoro's G2P chain as first
      suspected, but by the vestigial `tts` optional-dependency group (Coqui `TTS` →
      `gruut[de]==2.2.3` → `numpy<2.0.0`) — a group already dead weight (`TTS` requires
      `python<3.12`, the project requires `python>=3.13`; Kokoro was already its intended
      replacement per the removed comment). Deleted the `tts` extra from
      `server/pyproject.toml`, re-ran `uv lock --upgrade-package numpy --native-tls`:
      numpy now resolves to 2.5.1 with a native `win_amd64`/cp313 wheel; 253/253 unit
      tests still green (confirmed `TTS`/Coqui was never imported from source, only the
      unrelated Kokoro-based `infrastructure/tts.py` module which is unaffected).
      Confirmed via a real `uv sync --native-tls` on this laptop that this was the *only*
      Windows-unbuildable package removed — one real blocker remains and is expected,
      not a bug: `curated-tokenizers` (Cython/C++, pulled in via
      `kokoro`→`misaki[en]`→`spacy-curated-transformers`), which has no prebuilt wheel on
      any OS and needs a C/C++ compiler to build — exactly the role `build-essential`
      already plays on Linux. On Windows that means Microsoft C++ Build Tools, which
      needs an admin account this laptop doesn't have, so `uv sync` still can't complete
      *here* — but a normal Windows user with admin rights now can. Documented in
      `docs/INSTALLATION.md` as a first-class native-Windows install path (C++ Build
      Tools step, updated OS support table, Ollama/Docker Desktop Windows notes, remote
      LLM provider bullet for FR-707), with WSL2 demoted to the no-admin fallback.
      **Not yet live-verified**: no admin-rights Windows box available in this session to
      confirm `uv sync` completes end-to-end and STT/TTS actually work once Build Tools
      are installed — next real verification needs exactly that.
      **Follow-up (2026-07-17, same day)**: Postgres install docs were also fixed to stop
      documenting our own OS-specific install steps and instead point to PostgreSQL's and
      pgvector's own per-OS install guides, with Docker demoted to the explicit
      "sidesteps building pgvector on Windows" alternative. That surfaced a real gap:
      `ConfigureDatabaseConnection` (setup wizard step 3) unconditionally offered
      "peer" auth with no platform check at all, even though its own docstring already
      said peer is Linux/macOS-only (`getpeereid()`/`SO_PEERCRED`, confirmed absent on
      Windows via PostgreSQL's own docs) — a Windows user would have picked it, watched
      it fail, and gotten a `systemctl reload`-flavored hint that doesn't apply. Verified
      PostgreSQL's Windows equivalent, SSPI, is real and applicable here (works for a
      local non-domain account via NTLM fallback, not just Kerberos/AD; official Windows
      libpq/psycopg builds always compile in SSPI support, no extra dependency). Added a
      `sys.platform`-gated branch to the step (`sspi` choice + DSN
      `postgresql://mamemai@localhost:5432/mamemai` + a Windows-flavored pg_ident.conf/
      pg_hba.conf/`Restart-Service` hint on failure, mirroring the existing peer-auth
      path) and 4 new unit tests (platform-gated choice lists, SSPI success, SSPI failure
      hint, re-run "keep" dispatches to the SSPI hint too); 102/104 setup unit tests green
      (the 2 pre-existing Windows chmod failures, unrelated). Documented in
      `docs/INSTALLATION.md` as a third `<details>` block alongside peer and password
      auth. **Not yet live-verified**: no real Windows Postgres instance available this
      session to confirm the SSPI handshake actually completes end-to-end.
- [x] **Concept/Procedure offline-consolidation redesign** (2026-07-20,
      FR-307/FR-310/FR-407/FR-504, TR-606/609/703/705-708) — triggered by the
      2026-07-18 live-testing false-positive review (`project_extraction_false_positives_needs_review`
      memory): concepts/procedures bypassing worthiness entirely was too permissive,
      and a debugging session got fabricated into a personal-event episode despite the
      worthiness LLM correctly judging the conversation substantial. Four changes:
      1. Procedures are never extracted from live conversation, any persona —
      authoring-only (bundle install, persona enrichment); `ExtractionResult.procedures`
      removed, `_extraction_system_prompt` drops the schema section unconditionally.
      `bundle_install`/`EnrichMemory` procedure upserts now force
      `update_description=False` — an authored Procedure's content is immutable once
      installed, only engagement/`persona_state` move (previously only tutor-gated
      conversation extraction had this protection; bundle reinstall and
      cluster-proposal merges didn't).
      2. New `Concept.origin` field (`"authored"` vs `"organic"`, immutable like
      `language`; new `concepts.origin` DB column, `ALTER TABLE ... ADD COLUMN IF NOT
      EXISTS` idempotent-reapply pattern matching the existing `directive` column).
      `MemoryUpserter.upsert_concept` is origin-aware (TR-609): a live-extraction
      candidate landing within `authored_protection_threshold` (0.75) of an existing
      authored concept is a touch, never a rewrite, regardless of persona — replaces
      the old blanket `allow_insert=False` block for strategy personas (removed
      entirely, along with the now-dead `allow_insert` param on both `upsert_concept`/
      `upsert_procedure`). A genuinely new organic insert additionally needs
      `min_concept_engagement_turns` (2) of the conversation's own user turns to
      literally name it — an assistant-only mention no longer creates a permanent
      concept. (This started as an embedding-similarity check; see the same-day
      follow-up below for why it's literal-mention instead.)
      3. FR-407/504 relaxed for concepts only: a tutor session can now produce a
      genuinely new organic concept if distinct from curriculum and
      engagement-gated (episodes stay fully blocked for strategy personas, as before —
      lesson drills aren't events).
      4. Cheap extraction floor ahead of any LLM call (TR-707): `min_user_turns=2`/
      `min_user_words=40` (user turns only, assistant chatter excluded) — below it,
      worthiness + extraction are skipped outright. Worthiness/extraction prompts
      (`WORTHINESS_SYSTEM_PROMPT`, now deduplicated between the Ollama/OpenRouter
      evaluators, which carried byte-identical strings before) explicitly exclude
      assistant-operational content and require genuine time/place grounding for an
      episode; `_parse_extraction` drops an episode with no real `happened_at` instead
      of silently backdating it to the conversation's own timestamp — was masking
      exactly the fabricated-episode failure mode above.
      289/289 server unit tests green, with new coverage for the floor/
      engagement-gate/authored-protection paths (`test_consolidation.py`,
      `test_enrichment.py`, `test_extraction_prompt.py`). Committed as `775b9f5`.
      **Live-verified same day** (see follow-up below) — real workstation run against
      real Postgres + llama3.1:8b confirmed the concept-noise and episode-content-quality
      fixes both work as intended; surfaced a real gap in the engagement gate, fixed
      same day.
      **Follow-up (2026-07-20, same day): concept engagement gate replaced with literal
      mention, not embedding similarity.** Live run: a GA conversation discussed AI/XAI/
      NLP/Transfer Learning, all introduced together in one assistant monologue; the
      user asked a single specific follow-up about XAI only. All four still passed the
      embedding-similarity engagement check and got created as new concepts — the
      check couldn't distinguish "broadly the same topic" (any AI-flavored user turn)
      from "specifically about this sibling concept," since concepts extracted from one
      monologue on a shared topic sit close together in embedding space. Root cause
      diagnosed by the user, not a threshold problem: raising `min_concept_engagement_turns`
      wouldn't have helped either, since even XAI (the one legitimate follow-up) only
      had 2 AI-related user turns behind it total — the fix needed precision, not
      volume. Replaced `_has_engagement`'s embedding-similarity-to-turn-text check with
      `_mentioned_in` (`services/upsert.py`): whole-word, case-insensitive literal match
      of the candidate's name (or a parenthetical abbreviation split out of it, e.g.
      "XAI" from "Explainable AI (XAI)") against the user's own turns —
      `concept_engagement_similarity` and its 0.55 threshold are gone entirely, no
      longer needed since the check no longer touches embeddings at all. The user's own
      framing: this is an *intersection* of what the conversation introduced with what
      the user's own words actually named, not a similarity-based *union*. Also
      confirmed via this run that the whole session collapsed into a single
      `Conversation` row tagged with GA's `persona_id` (pre-existing behavior —
      `replay.py`'s `_group_into_conversations` groups by topic-continuation markers,
      not by persona) — so the tutor-specific FR-407 relaxation still hasn't been
      exercised under a real tutor-scoped conversation; would need a tutor-only session
      to test that path. 293/293 unit tests green (4 new regression tests reproducing
      the exact sibling-concept scenario, `TestSiblingConceptsFromOneMonologue`).
      **Not yet re-verified live** with this fix in place — same workstation, same kind
      of AI-discussion conversation, next time.
- [x] **Persona switch now forces a conversation boundary** (2026-07-20, FR-207,
      TR-315/403) — found from the same live test above: the GA→tutor→GA session
      collapsed into one `Conversation` row tagged with GA's `persona_id`, because
      `replay.py`'s `_group_into_conversations` only re-evaluates a group's
      `persona_id` when a `ConversationBoundaryType.BREAK` marker closes it, and that
      marker previously came from exactly one source — the LLM's own
      `[TOPIC_CONTINUATION]`/`[TOPIC_BREAK]` self-tag (TR-304) — entirely disconnected
      from the deterministic Directive-based persona switch (FR-207, TR-315). Real
      correctness gap, not cosmetic: silently broke FR-303 (persona-scoped knowledge —
      extraction/upsert ran entirely under whichever persona was active first),
      TR-704 assessment dispatch (the tutor's own `PersonaAssessmentPort`/SRS tracking
      never fires for its own lesson content when it isn't the group's persona), and
      left FR-504's "no episodes from tutor conversations" unenforced in this case
      (didn't bite in the live test only because `worthy` happened to come back false).
      Fix: `session.py`'s `ProcessTurn.execute` (step 7) unconditionally overrides
      `boundary_marker` to `BREAK` whenever `persona_switched is not None`, regardless
      of what the LLM's own tag said — deterministic, same posture as the switch
      decision itself never being left to LLM judgment. One accepted imprecision: the
      switch-confirmation reply (already generated *as* the new persona, per FR-207)
      lands as the trailing turn of the *old* group rather than the leading turn of the
      new one, since `_group_into_conversations`'s BREAK handling is "close inclusive,
      then reopen" — not worth restructuring boundary semantics for one transitional
      turn. 295/295 unit tests green (2 new: `test_persona_switch_forces_conversation_boundary`,
      `test_persona_switch_boundary_overrides_topic_continuation_tag` in
      `test_session.py`). **Not yet live-verified**: needs a real multi-persona session
      on the workstation to confirm it now produces separate, correctly-scoped
      `Conversation` rows — this is also the prerequisite for finally live-testing the
      FR-310 tutor-side concept relaxation, which couldn't be exercised before this fix.
- [x] **Directive matching replaced with a deterministic keyword rule, no embedding
      call (2026-07-30, FR-207, TR-315)** — live CPU-only usage showed the per-turn
      embedding-similarity match against `WorkingMemory.directive_concepts` (a second,
      unconditional `EmbeddingService.embed()` call every turn) was the dominant
      avoidable per-turn CPU cost. Replaced with a plain "all these keywords, in a
      short utterance" rule (`services/directives.py`: `build_trigger`/
      `matches_trigger`) — a directive phrase is a short, closed-vocabulary command,
      exactly the case where a rule outperforms a similarity search. Trigger keywords =
      a translated action verb (`_SWITCH_VERB`, one entry per `SUPPORTED_LANGUAGE`
      code, first-pass translations not reviewed by a native speaker of each language —
      revisit once there's live usage to check against) + the persona's own name split
      into words; `max_words = max(6, len(keywords) + 3)`. One trigger `Concept` per
      language in `persona.languages` (already folds in the user's primary language,
      TR-904) — so the switch works in the persona's own languages too, not only the
      language it was installed in — falling back to the persona's `response_language`
      for a live-created persona with an empty `languages` list. The GA-owned
      "return to GA" directive gets one trigger per this machine's *installed*
      language (FR-705), via a second translation table (`_GENERAL_ASSISTANT_NAME`)
      since that label is GA's own fixed wording, not a user-authored persona name.
      `PersonaDirectiveSync` no longer takes an `EmbeddingService` at all; directive
      `Concept` rows now store `embedding=None` (column already nullable; TR-509's RAG
      search already excluded `directive IS NOT NULL` rows regardless). Known
      limitation, accepted rather than solved: the `max_words` ceiling is computed via
      `str.split()` (same measure `DefaultRecallGate` already uses) which under-counts
      languages without whitespace word boundaries (ja/zh-cn) — the ceiling becomes a
      no-op for those rather than wrong; the keyword conjunction alone still keeps
      false positives rare. 303/303 unit tests green (new/rewritten coverage in
      `test_directives.py` + `test_session.py`, including an explicit
      false-positive-avoidance regression: mentioning a persona's name in ordinary
      conversation, without the action verb, must not trigger a switch). **Not yet
      live-verified**: needs a real session on CPU-only hardware to confirm both the
      switch still fires reliably and the per-turn latency actually drops.
- [x] **Directive first-classed off `Concept` entirely (2026-07-30, FR-207, TR-315,
      TR-503/510, INV-9)** — same-day follow-up to the item above, prompted by a design
      question about whether the user could ask to be reminded of/discuss directives:
      answer was no either way (recall's `search()` never touched them, and the system
      prompt never mentions them — deliberately, the actual FR-202 language-drift fix),
      but the audit showed a directive `Concept` row was riding on that type for
      storage plumbing alone — `engagement_level`, `origin`, `persona_state` unused,
      `embedding` always `NULL` after the item above. New `domain.model.Directive` (`id`,
      `target_persona_id`, `action`, `language`, `trigger`, `description`) and its own
      `directives` table/`DirectiveRepository` port (`services/ports.py`) — `list_all()`,
      `upsert()` (update-in-place supported for a future editable-trigger feature, even
      though `PersonaDirectiveSync` only ever inserts today). `target_persona_id` is a
      real FK with `ON DELETE CASCADE` against `personas(id)` — the concrete payoff:
      `PersonaDirectiveSync.sync_removed` and `RemovePersona`'s explicit cleanup call
      are both gone entirely, replaced by the same cascade guarantee concepts/procedures
      already had (INV-9), rather than the old GA-owned-Concept scheme's hand-rolled
      workaround (a directive's `persona_id` was GA's, not the actual target, so INV-9's
      existing cascade never covered it). `Concept.directive` field, `concepts.directive`
      column, and `MemoryRepository.list_directives`/`delete_concept` are all removed;
      `concepts` search/list queries lost their `WHERE directive IS NULL` filter — moot
      now, not just satisfied, since directives were never in that table's result set
      to begin with. Migration: `directives` table added, `concepts.directive` dropped
      (`ALTER TABLE concepts DROP COLUMN IF EXISTS directive`) — safe at this project's
      pre-1.0/single-user stage, no data migration needed. 300/300 unit tests green
      (3 unit tests removed — persona-delete-cascades-to-directive can no longer be
      exercised via fakes, same as concepts/procedures cascade already couldn't be —
      3 new integration tests added instead, `TestPSDirectiveRepository` in
      `test_postgres.py`, covering upsert/list/cascade against real Postgres).
      **Not yet live-verified** (needs the real-Postgres integration suite on the
      workstation — migration hasn't been re-applied there yet either).
- [x] **Blocked switch to a deactivated persona (2026-07-30, FR-207, TR-315)** — found
      via a design question about the item above: `DeactivatePersona` only flips
      `is_active`, doesn't touch the persona's Directive row (only removal cascades,
      INV-9), so the trigger kept matching — and neither `SwitchPersona` nor
      `PersonaRepository.get()` ever checked `is_active` anywhere in the live pipeline
      (confirmed by grep: zero reads outside `domain/model.py`'s own field/setters and
      `postgres.py`'s storage round-trip). Net effect before this fix: asking to switch
      to a deactivated persona silently switched to it — the DB's `is_active` flag never
      actually gated anything at the point of use. Fixed in `ProcessTurn.execute` step
      3b: a directive match's target is now fetched via `persona_repo.get()`; `None` (a
      dangling reference — shouldn't happen, FK-backed) or `not is_active` both skip the
      real switch, the latter setting `blocked_switch_persona_name` instead. Also added:
      a turn-scoped context injection (`_compose_working_context`'s new
      `blocked_switch_persona_name` param) — same mechanism/position as recalled
      memories/the selected item, not a standing fixture — so the active persona's own
      reply can explain the target is unavailable, in its own words. Deliberately not a
      repeat of the FR-202/FR-203 persona-listing scaffolding: it names at most one
      persona, only on the rare turn a blocked attempt occurs, and only because the
      user already said that name first (reactive, not proactive) — the same
      language-drift risk that pattern caused doesn't apply here. A blocked switch is
      still treated as a directive turn for recall/selection-skip purposes (TR-306/
      TR-314) — the utterance was a switch attempt either way, not a content question.
      303/303 unit tests green (3 new: blocked switch stays on the active persona,
      the explanatory context is actually injected into the LLM messages, recall/
      selection are skipped on a blocked-switch turn same as a real one). **Live-verified
      2026-07-30** on the GPU workstation (`linux_workstation_amdgpu`) against real Postgres + real Ollama
      (llama3.1:8b) — see the item directly below, which is what that live run actually
      found.
- [x] **Ambiguous Directive match resolved by most-keywords-wins, not first-in-list
      (2026-07-30, FR-207, TR-315)** — found live-testing the item above: seeded a
      "Coach" persona (trigger keywords: switch, coach) and a deactivated "Old Coach"
      persona (trigger keywords: switch, old, coach) and said "Switch to Old Coach." —
      Coach's keyword set is a strict subset of Old Coach's, so **both** triggers
      matched; `next()` picking the first match in `Directive.id` order silently
      resolved to Coach (created first), not the persona the user actually named — the
      blocked-switch-to-deactivated-persona path was never even reached. Root cause
      is a real gap in the deterministic-keyword design, not caught by any unit test
      (none used one persona's name as a strict subset of another's). Fixed in
      `ProcessTurn.execute` step 3b: among every Directive whose trigger matches, the
      one requiring the most keywords wins (more specific/better-evidenced), not
      first-in-list; ties still fall back to list order. Confirmed live, same session,
      immediately after the fix: "Switch to Old Coach." now correctly resolves to Old
      Coach, is blocked (deactivated), and llama3.1:8b's real reply was "Unfortunately,
      Old Coach is not available at this time." — unprompted-verbatim, generated
      naturally from the turn-scoped context injection. All three scenarios (switch to
      an active persona: 0 embedding calls; blocked switch to a deactivated persona: 0
      embedding calls, correct persona, real LLM explains it; ordinary mention of a
      persona's name with no action verb: no directive fires, normal recall runs, 1
      embedding call) verified end-to-end against real Postgres + real Ollama on this
      machine. 304/304 unit tests green (1 new: `test_ambiguous_directive_prefers_
      more_specific_match`). **Separately found, not fixed (pre-existing, predates
      all Directive work by 2 commits — 775b9f5/c74b6e3):**
      `test_consolidation_pipeline.py::test_disconnect_replay_consolidate_updates_db_
      state` and `::test_unworthy_conversation_still_extracts_concepts_but_no_episodes`
      both fail against real Postgres here — a "hiking" concept clears the extraction
      floor (min_user_turns/min_user_words) but never gets inserted (0 rows), meaning
      `MemoryUpserter.upsert_concept`'s engagement gate (`_mentioned_in`, literal
      whole-word match) is rejecting a case that looks like it should pass; not
      investigated further, flagged for a separate look.
- [x] **Recall gate narrowed to explicit questions only, plus reply-side memory
      honesty (2026-07-30, FR-309, TR-303/314)** — same CPU-cost motivation as the
      Directive work above, next system reviewed. `DefaultRecallGate.should_embed`
      (used by GA and any persona without its own override) previously fired on any
      utterance clearing a word-count floor (3), statement or not; now requires a
      literal `"?"` in the STT transcript too (faster-whisper doesn't strip
      punctuation, so a genuine question normally comes through with one — an
      accepted-imprecise proxy, not guaranteed, same posture as the word-count floor
      itself) — a plain statement, however long, no longer triggers recall at all.
      `min_words` raised 3 → 6 alongside it, specifically to also exclude short
      reflexive questions ("How are you?", "What's up?") that a "?"-only check
      wouldn't catch. `LanguageTutorRecallGate` (always-`True` override) is
      deliberately untouched — confirmed with the user — a tutor's single-word
      vocabulary answers are never themselves phrased as a question, so gating the
      tutor on `"?"` would have gutted its own reason for existing; documented instead
      (`docs/PERSONAS.md`) as the tutor's own distinct behaviour. Documented for the
      user generally too: recalling something now requires an explicit question, not
      just a mention.
      Second half, prompted by the user wanting recall to be legible, not just
      cheaper: `ProcessTurn.execute` now tracks `recall_attempted` (`should_embed`
      cleared this turn) and threads it into `_compose_working_context`, which
      injects one of two turn-scoped instructions — "acknowledge drawing on a past
      conversation" when memories were actually found, or "say plainly you checked
      and found nothing" when a real attempt came up empty — and neither when recall
      never ran on the turn at all (status quo). Same injection tier as recalled
      memories/selected item/blocked-switch explanation — turn-scoped, not a standing
      fixture. 307/307 unit tests green (rewrote `test_recall_gate.py`'s
      `DefaultRecallGate` cases for the new `"?"`/`min_words=6` behaviour; 3 new in
      `test_session.py`: found → acknowledgment present, attempted-but-empty → honesty
      line present, never-attempted → neither present). **Live-verified 2026-07-30**
      on `linux_workstation_amdgpu` against real Postgres + real Ollama (llama3.1:8b) + the real
      embedding model: a statement and a short reflexive question both correctly
      skipped recall (0 embedding calls each), a real question against a seeded
      memory (a Paris trip) found it and the reply naturally referenced the specific
      recalled details, and a real question with nothing to find got an honest "I
      don't have that" reply instead of a guess. No bugs found this round (contrast
      with the Directive work two entries up, where live-testing caught two).
- [x] **Language tutor's RecallGate override removed — same rule for everyone now
      (2026-07-30, FR-309, TR-314)** — same-day follow-up: the user questioned
      `LanguageTutorRecallGate` (always-`True`, so even a bare one-word vocabulary
      answer like "agua" triggered a full recall search) given the new
      question-gating above. Checked the code to confirm the intuition: the item
      currently being tested is already injected via `LanguageTutorSelectionStrategy.
      select_items` (`persona_state`/spaced-repetition scheduling, a completely
      separate mechanism from recall) — a one-word answer to the tutor's own prompt
      doesn't need its own additional generic memory search on top of that context.
      Removed `LanguageTutorRecallGate` entirely (file deleted, not deprecated in
      place — nothing else referenced it) and emptied `RECALL_GATE_FACTORIES` in
      `server.py`, so every persona now falls back to the same `DefaultRecallGate`
      (question + `min_words=6`) with zero active overrides. Kept the registry itself
      (parallel to `SELECTION_STRATEGY_FACTORIES` et al.) rather than deleting the
      mechanism — a future persona-type may still need a distinct recall policy.
      **Live-testing this change caught a real regression before it shipped**:
      `_build_strategies`'s generic "unknown strategy" warning fired for every tutor
      persona on every connection once `RECALL_GATE_FACTORIES` went empty — the
      strategy name isn't unknown at all, the whole registry is just intentionally
      empty now, but the warning couldn't tell the difference. Fixed: only warn when
      the registry actually has *some* entries (a real gap) — silent when it has
      none (nothing to gap against). 306/306 unit tests green (removed
      `TestLanguageTutorRecallGate`, added a regression test confirming a one-word
      vocab-style answer is now skipped by the plain `DefaultRecallGate`); live
      re-verified after the warning fix — `_build_recall_gates` returns `{}` with a
      real `language_tutor` persona in the DB and no spurious warning, and a live
      one-word "agua" answer to a real tutor persona correctly triggers zero
      embedding calls.
- [ ] **Phase 13 live smoke on the workstation**: fresh onboarding shows only installed
      languages; speak a second installed language to the GA (reply mirrors, voice
      switches); speak an uninstalled language (primary-language reminder names
      mamemai-setup); tutor lesson — confirm `[lang:]` tags appear in the composed context
      (`MAMEMAI_TEST_PERSONA_DEBUG=1`, prints `[persona-debug]` lines — renamed from
      `MAMEMAI_TEST_TUTOR_DEBUG`/`[tutor-debug]` on 2026-07-31), the tutor treats a
      wrong-language attempt gently, and no tag is
      ever spoken; re-run mamemai-setup and confirm the existing-install banner +
      pre-checked languages. Workstation config catch-up: add `[languages] installed`
      to its mamemai.toml (or leave absent for all-supported behaviour).
- [ ] **Phase 12 live smoke with the real client (mic/speakers)**: the tutor flow is
      verified via the plain-text LLM quality gate
      (`server/tests/integration/test_tutor_llm_quality_gate.py`, 5/5 against
      `aya-expanse`) and scripted-WebSocket runs, but never with real audio hardware —
      confirm the persona switch, the two-teacher cast audibly switching voices,
      HVPT rotation across sessions, and the overall acoustic experience.

---

## Phase 9 — Live Voice-Command Wiring (not yet designed)

Placeholder for the next design session (per the Phase 8 close-out, 2026-07-07).
Nothing in this section is a committed decision.

- [ ] LLM tool-calling / intent detection so GA can actually trigger, mid-conversation:
      `UpdateIdleConsolidationMinutes`, `DeactivatePersona`/`ReactivatePersona`, and
      voice / `speaking_rate` / `voices`-map changes via `EditPersona`. Phase 8 built
      the data model and use cases; none are wired to live conversation yet.
      *New evidence since this was written (Phase 12, 2026-07-13)*: bracket-tag emission
      on `aya-expanse` has a real reliability ceiling — the front-loaded few-shot fixed
      `[PERSONA:]`/`[FOCUS:]` (8/8), and Ollama tool/function-calling (`aya-expanse`
      lists `tools` as a capability) remains an unexplored alternative signaling channel
      worth evaluating in this design.
- [ ] **Prerequisite**: discovery/registry for "what's voice-configurable" — a small
      declarative registry (e.g. a `VoiceConfigurableField` descriptor: entity, field,
      type, validator, use case to invoke) that both `ONBOARDING_SCRIPT` and the
      tool-calling layer read from, replacing hand-written prose that is already stale
      (`idle_consolidation_minutes`/`speaking_rate` exist but aren't mentioned there).
      Likely a prerequisite for the item above, needs its own design session — see
      `project_mamemai_open_questions` item 15.
- [ ] `StartSession`'s two hardcoded, unwired constructor defaults
      (`session_tail_turns=10`, `session_continuation_threshold_hours=24.0` in
      `services/session.py`) — candidate promotion to `User` fields under the same
      DB-attribute placement rule (open-questions item 14).
- [ ] VAD threshold **voice**-configurability — client-side; needs a new server→client
      WS message, since the client is fully stateless. Partially addressed 2026-08-04:
      `[vad] onset_frames/silence_frames/preroll_frames` are now client *config-file*
      settings (TR-201), so tuning no longer requires a code edit. Voice-configurability
      is still open and is the harder half — these live on the client, outside the DB
      where every other user-facing setting sits (FR-701).

---

## Wizard (Phase 7) leftovers

- [ ] `--client` flow (`cli.py` still raises `NotImplementedError`)
- [ ] `--uninstall` flow (same)
- [ ] Kokoro voice-pack pre-download: the wizard now downloads Whisper models, Piper
      voices, and the embedding model (`steps.py`), but Kokoro voice packs are still
      lazily fetched from HF on first live use — fails on an offline/locked-down server
      (`HF_HUB_OFFLINE=1`), and the standing cause of the 5 skipped integration tests
      (es/it/pt/ja/zh-cn voice packs).
- [ ] Corporate-proxy / `SSL_CERT_FILE` handling for wizard downloads should be
      automatic (or at least an actionable error), not a manual env-var/`--system-certs`
      ritual — the raw failure is a misleading httpx traceback (details in the archive
      and `project_gpu_workstation_environment` memory).
- [ ] Install-time CUDA-compat verification: `CheckPrerequisites`/`RunHealthChecks`
      should catch CUDA library mismatches (the `nvidia-cublas-cu12` vs CUDA-13
      `nvidia-cublas` class of bug) rather than relying on the pyproject pin never
      drifting.
- [ ] `ServerWebSocketHealthCheck` only checks "something is listening on the port",
      not "mamemai-server actually started with working STT/TTS" — a subprocess-launch
      check needs the server's own venv, deferred.
- [ ] 2 chmod-related setup tests fail on Windows (unfixable there; run on Linux).

*Stale gap closed, for the record*: the old "no wizard step collects Postgres
connection details" gap is gone — `ConfigureDatabaseConnection` exists and pre-fills
on re-runs (FR-706).

---

## Calibration — blocked on real usage data

All instrumented and shippable with placeholder values; revisit once real sessions
accumulate:

- [ ] Merge/disambiguate upsert thresholds (0.93 / 0.75) — the hypernym vs.
      same-name-different-domain cases sit uncomfortably close (0.86–0.87); real
      embedding-calibration numbers are in the archived Phase 3 notes.
- [ ] Tutor retention ranking + half-life parameters — assessment writes
      `persona_state` from day one; selection keeps ranking by `engagement_level`
      until data justifies flipping `settings["ranking"] = "retention"`.
- [x] ~~`_PREFIX_SCAN_WINDOW_CHARS` (200)~~ — **moot 2026-08-02**: the constant is gone
      with the in-band tag channel (TR-304), so there is no longer a
      latency-vs-recognition tradeoff to calibrate. Nothing is scanned for before the
      first sentence is spoken.
- [ ] `episode_anchor_threshold` (0.6) and `elicitation_cap` (2).
- [ ] Threshold promotion to persona-scoped, voice-configurable fields (Phase 9
      candidate) — blocked on the same calibration data.
- [ ] `authored_protection_threshold` (0.75) — FR-310 concept-origin gate (2026-07-20),
      still untested against real usage. `min_concept_engagement_turns` is **no longer a
      calibration item** as of 2026-08-02: it went to 1 and stopped being a threshold at
      all, because the question it answers (provenance — did the user introduce this?) is
      binary. What used to be tuned by raising it is now the engagement ladder, which is
      also not a threshold. Nothing left to calibrate on either.
- [ ] `min_user_turns`/`min_user_words` (2/40) — `ConsolidateMemory`'s extraction floor
      (FR-307/TR-707, 2026-07-20).

## Deferred by explicit decision — waiting for real tutoring sessions

- [ ] Pronunciation-signal mining: faster-whisper's per-segment `avg_logprob` /
      `no_speech_prob` / language-probability distribution, currently discarded.
- [ ] Any constrained-STT experiment.
- [ ] `User.secondary_languages` dead-column removal (never populated; superseded by
      installed languages — glossary already marks it "removal pending").

## Known gaps / accepted limitations (revisit when relevant)

- **Localized FOCUS marker** — **closed 2026-08-02.** The tutor spontaneously emitted
  `[Focalizza: …]` (flagged 2026-07-13); it stopped being spoken on 2026-07-31 (FR-106
  marker strip) but was still lost as a signal. The failure mode is now gone at the root:
  there is no focus signal to lose, because the channel itself was retired (TR-304). What
  replaces it — detecting the wish on the user turn, where a translated control token
  cannot arise — is deferred, and tracked as the FR-502 suspension rather than here.
- **No session wish reaches selection** (2026-08-02, FR-502 suspended): every persona
  gets its default batch. Deliberate — see [BRIEF_inband_tags.md](BRIEF_inband_tags.md).
- **One persona, two topics, one conversation** (2026-08-02, FR-112 retired): an
  uninterrupted single-persona stretch covering unrelated topics consolidates as a single
  `Conversation` — one worthiness verdict, one summary, extraction over mixed content.
  A persona switch is now the only mid-session split. Accepted; offline segmentation
  waits for real multi-topic sessions to calibrate against.
- **TPRS cross-session story recap** no longer persists (the consolidation gates for
  strategy personas removed episode extraction); no replacement mechanism —
  deliberate open gap.
- **WebSocket keepalive timeout too tight for a real turn** (found 2026-07-20 live
  testing on the workstation): both client (`client.py`) and server (`server.py`)
  call `websockets.connect`/`websockets.serve` with no `ping_interval`/`ping_timeout`
  override, so both default to the library's ~20s. Two real turns during today's
  testing (llama3.1:8b) took 19.28s and 19.54s end-to-end — already within a second of
  that default — and a longer reply (more TTS segments to synthesize) tipped one turn
  over the edge, killing the connection with `ConnectionClosedError: 1011 internal
  error, keepalive ping timeout`. Traced to genuine per-turn latency, not a hang or
  bug in either the client's receive loop or the server's turn-processing loop (both
  checked — server's `async for msg in ws` serializes turns correctly, client's
  audio-playback wait runs off the event loop via `run_in_executor`). This is the
  same class of issue already known from gemma3:27b ("slow enough to cause
  keepalive-ping-timeout crashes on long turns," see
  `project_ga_language_drift_known_limitation` memory) — now recurring even on the
  supposedly-fast model, since a single voice turn is inherently long-running.
  User's call: longer responses should be an expected, supported case, not something
  to route around — needs a real fix (raise `ping_interval`/`ping_timeout` on both
  ends, e.g. to 60s), deliberately deferred, not done this session.
- **Replay idempotency race** (Phase 5): `TurnLogReplayer`'s check-then-insert isn't
  atomic across the live + offline DB connections; a reconnect landing in a narrow
  window could duplicate one session's rows. Fix sketch if it ever bites: a
  `replayed_sessions(session_id UUID PRIMARY KEY)` claim table with
  `INSERT ... ON CONFLICT DO NOTHING RETURNING`.
- **Acoustic echo** on the client — no echo cancellation; mic muting during playback
  is the current mitigation (INV-4 no-barge-in makes this tolerable).
- **Markdown stripping** is pattern-based and still incomplete for exotic LLM output.
- **Coqui TTS licence conflict** (MPL-2.0 Exhibit B vs AGPL-3.0) — deferred; Piper and
  Kokoro are the working engines.
- **Korean unsupported**: Kokoro has no Korean pipeline; MeloTTS rejected on dependency
  footprint. Revisit if a lighter Korean TTS appears.
- **Client-side first-launch onboarding proposal** (fold server address, port, and
  language into one client-side questionary wizard instead of the server-driven
  `select_language` flow) — archived Phase 4 "revisit" note, still undecided.
- **Laptop `server/` venv frozen**: `uv.lock` now pins numpy 2.5.1 (fixed 2026-07-17,
  see the Windows support item above), but `uv sync` still can't complete on this laptop
  — `curated-tokenizers` needs a C/C++ compiler and this laptop has no admin rights to
  install one. Always `uv run --no-sync` here regardless. Locally,
  `tests/unit/infrastructure/test_config.py` fails collection (`platformdirs` missing,
  never installed since `uv sync` has never completed on this laptop) — `--ignore` it;
  it runs on the workstation.
