# An explicit num_ctx, and budgets counted in tokens

**Requirements:** TR-951, TR-952, TR-309, FR-707
**Status:** in progress — parts 1 and 3 done, parts 2 and 4 open

## Problem

The context-overflow finding (Ollama's 4096-token default silently truncating
long conversations, root-caused 2026-08-03) has a four-part design response
spanning server config, the wizard, and both halves of the pipeline. Parts 1
(`[llm] num_ctx` in `mamemai.toml`) and 3 (loud overflow warnings) are shipped.
Parts 2 (a context ceiling in the wizard's LLM catalogue) and 4 (budgets
expressed in tokens rather than turn counts) are still open.

## Scope

- Part 2: add the model's context length to `LLMCatalogueEntry`, and size
  `num_ctx` as `min(model ceiling, what VRAM allows, what the workload needs)`.
- Part 4: express both the live rolling-window compaction budget (`TR-309`,
  currently `total_turn_count % 50`) and any other turn-count-based budget in
  tokens, so they scale with `num_ctx` instead of being independently-tuned
  constants.

## Out of scope

Remote (OpenAI-compatible) provider budgeting — different mechanism (surface the
provider's own context error) reaching the same guarantee, not a `num_ctx` knob.
Whether windowing (`extraction_transcripts`/`merge_extractions`) becomes
unnecessary now that `num_ctx` is explicit — open question, not this change's
job to answer (see the note under Findings).
