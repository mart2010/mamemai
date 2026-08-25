# Tasks

- [x] **Part 1 — `[llm] num_ctx` in `mamemai.toml`.** Ollama-scoped,
      wizard-written. Default shipped: **16384**. Needed whatever `[llm]
      provider` is set to — the offline pipeline stays on local Ollama (TR-955)
      regardless of the live provider.
- [ ] **Part 2 — a context ceiling in the wizard's LLM catalogue.**
      `LLMCatalogueEntry` records `model_id`, `vram`, `languages`, `recommended`,
      `reasoning` — add the model's context length (`ollama show` reports it,
      e.g. `llama3.1:8b` → 131072). The ceiling alone isn't the answer: KV cache
      scales with `num_ctx`, so the value must be
      `min(model ceiling, what VRAM allows, what the workload needs)`. The
      wizard already computes `VRAMEstimate`/`FitLevel` per entry, so this
      extends an existing fit calculation. Re-running the wizard to change model
      must re-derive it (FR-706 already re-runs from recorded state).
- [x] **Part 3 — overflow stops being silent.** An `[llm]` stdout line (TR-311
      instrumentation family) for the live path; a skipped/flagged window rather
      than a silent empty result offline. Estimator: chars/3 (over-counts for
      these languages, the safe direction for a warning-only decision — no
      tokenizer dependency taken, since the decision is only ever
      shout-or-don't, never refuse-work).
- [ ] **Part 4 — budgets in tokens, not turns.** TR-309 compacts at
      `total_turn_count % 50 == 0`, dropping the oldest 25 — a turn count, the
      same bad proxy that made `EXTRACTION_WINDOW_TURNS = 24` wrong. Measured on
      conv 23, the live context crosses 4096 tokens at **turn 18**; the
      compaction mechanism doesn't fire until turn 50 — 32 turns after overflow
      starts. Both budgets should be expressed against the context and then
      follow `num_ctx` automatically.

## Findings (2026-08-03)

**The overflow, decisively demonstrated.** `num_ctx` defaults to 4096 and Ollama
silently truncates anything longer — nothing errors, nothing warns. Same
transcript, only the context changed:

| conv 22 slice | transcript | `num_ctx=4096` | `num_ctx=16384` |
| --- | --- | --- | --- |
| first 48 turns | 18 355 chars | **0 concepts** | **8 concepts, 2 episodes** |
| full 72 turns | 28 005 chars | **0 concepts** | **6 concepts, 3 episodes** |

264 measurements, 4 conversations, 3 repeats each: the boundary is transcript
**size**, not turn count, and it's razor sharp — 8 000–13 999 chars scores
3–13% zero-rate, 14 000+ chars scores **100%** zero-rate. ~14 000 chars of
French is ~4 400 tokens — the 4096 window plus the system prompt.

**Not confined to the offline path.** The live conversation context passes 4096
tokens around turn 18 (measured on conv 23: 2 526 tokens at turn 12, 4 141 at
18, 6 969 at 32) and `recent_turns` is not compacted until
`rolling_window_size = 50`. Whether that let a false-recall through is plausible
but unproven.

**Live-verified after parts 1 and 3 shipped**, conv 22 (72 turns, 27 322
chars), nothing else changed:

| | concepts | episodes | wall time |
| --- | --- | --- | --- |
| `num_ctx=4096` | 0 (all 3 windows refused) | 0 | 0.0s |
| `num_ctx=16384` | 14 | 0 | 73.8s |

**Open question surfaced by that same run**: the 14 concepts included
`Lumières zodiacales`, `Lumière zodiacale`, `Zodiaque lumineuse` and `Cirrus
zodiacal` — one subject, four records, split across window boundaries. That's
the strongest evidence yet that raising `num_ctx` may make windowing
unnecessary rather than merely better-tuned — tracked as an open question here,
not a task, since it needs its own measurement before any change. Related:
whether worthiness's both-ends sampling (`worthiness_transcripts`) is likewise
redundant now that the cliff that motivated it is understood to be a truncation
artefact.
