# Tasks

- [x] Investigate mechanical dedupe candidates (2026-08-04) — both measured dead
      or insufficient, see findings below.
- [ ] **Blocked on live data.** Decide, once a monitored session on current STT
      reproduces (or fails to reproduce) the defect, whether a canonicalisation
      pass is still needed at all.
- [ ] If still needed: normalise `merge_extractions`' key with plural and
      stopword stripping (small, deterministic, no LLM — worth 2 of 23 concepts
      in the 2026-08-04 sample, held back pending the live-data decision above).

## Findings (2026-08-03 → 2026-08-04)

**2026-08-03, largest quality defect found by the window sweep.** Reading actual
concept names from 48 runs across 4 conversations:

- **Synonym clusters.** One phenomenon under five names in a *single* run
  (`Lumière zodiacale` / `Zodiaque lumineuse` / `Cirrus zodiacal` / `Zodiaque de
  Bénou` / `Zodikallicht`), again live on 2026-08-03 during the `num_ctx`
  verification. `merge_extractions` de-duplicates on exact lowercased name, so
  none of these collide; downstream `upsert_concept` embeds
  `"{name}: {description}"` and merges only above 0.93 (later raised to 0.97),
  which differently-worded pairs will not reach.
- **Morphological pairs** the same exact-match dedupe misses: `Halos`/`halo`,
  `parhélie`/`parhélies`, `Noctiluques`/`Nuages noctilucs`.
- **STT corruption promoted to concepts**: `Horreur Boréale`, `Horreur Polaire`,
  `Horaires boréaux` and `Parellis` (for *aurores boréales* and *parhélies*),
  `Troux noirs`, `Matère noire`. Some are **grounded** — the user repeated the
  mangled form — so FR-310's provenance gate lets them in while the correct
  spelling has no provenance (see `docs/changes/FR-310-provenance-prefers-stt-errors/`).
- **Bare-word concepts** with no content: `glace`, `latitude`, `atmosphère`,
  `phénomène météorologique`, and one apparent hallucination, `Norton`.

**2026-08-04, both mechanical candidates measured dead.** Conv 22 and 23 still
had their turns in the database, so the defect was reproduced on demand — real
extractor, real transcripts, nothing upserted. Conv 22 returns **23 concepts
naming ~8 subjects**: five for zodiacal light, eight around aurora, two each for
halo and noctilucent clouds.

| approach | result |
| --- | --- |
| exact-lower key (ships) | 23 distinct — baseline |
| + strip accents | 23 — collapses nothing |
| + strip plurals | 22 — catches `Lumières zodiacales`/`lumière zodiacale` |
| + strip stopwords | 21 — also catches `Halos`/`Phénomène des halos` |
| embedding dedupe | 88.5% pair accuracy, **ordering inverted** |
| LLM grouping (3 runs, temp 0) | ~90%, stable, but unusable as-is |

**Embedding dedupe is dead, for a reason worth keeping.** Labelling all 23 by the
subject they actually name: same-subject pairs score 0.875–0.974, different-
subject pairs 0.857–0.958 — a margin of **−0.083**, with the order actively
wrong in places. Every one of these is a French description of an atmospheric
optical phenomenon, most opening "Phénomène … qui se produit lorsque" — **the
embedding reads register, not referent**, the same finding that forced
`merge_threshold` to 0.97. No scalar similarity bar can serve both jobs; raising
that bar makes downstream merging of these clusters *less* likely, not more.

**LLM grouping half-works.** `llama3.1:8b` given all 23 at once is stable at
temperature 0 and reliably finds the useful groups in 3/3 runs, but also drops
5–6 of the 23 entirely, merges unrelated items, splits STT-corrupted names into
their own group, and picks bad canonical names. Shippable only wrapped in
deterministic guards (unplaced items default to singletons, members must match
input names exactly, canonical name chosen by rule not by the model).

**The reframe.** Most of conv 22's mess is not naming variation at all:
`Horreur boréale` came back described as *"un type d'horreur liée à
l'environnement polaire"* — a **fabricated concept**, the model inventing a
definition to fit an STT error. The chain is STT mangles the term → the user
repeats the mangled form → FR-310 admits it on provenance → the model
confabulates a description. Deduplicating those stores garbage once instead of
five times.

**Conv 22 predates the STT upgrade to `medium`; conv 23 does not.** Conv 23
returns **11 concepts naming 11 distinct subjects — zero synonym clusters**, one
STT artifact. Confounded by length (72 turns/5 windows vs 32/2), so not proof,
but it's the strongest available signal that this defect was substantially an
STT-quality problem already fixed. **Deliberately deferred**: building a
canonicalisation pass now would target a defect that may no longer occur. What
decides it is one monitored session on current STT.
