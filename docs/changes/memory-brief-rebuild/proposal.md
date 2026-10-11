# The memory brief is rebuilt from a recency window, not from the user's accumulated state

**Requirements:** FR-109, FR-308 (INV-10 for the unscoped episodes) — to be updated in the
same commit as any fix
**Status:** open — **measure first**: no product change until the eval baseline exists
(see [eval/docs/PLAN.md](../../../eval/docs/PLAN.md), S2.8)

## Problem

`GenerateMemoryBrief` (`services/memory.py`) builds the "what I know about the user" text
injected into every session (FR-109) as follows, after each offline run:

1. take the 20 most recent episodes (`DEFAULT_BRIEF_EPISODES`) and the **last 40 organic
   concepts** (`DEFAULT_BRIEF_CONCEPTS`) — counts, not a token budget;
2. ask the LLM to summarise "based only on the material", **without the previous brief**;
3. save the result over the old one; write nothing when there is no material.

Consequences:

- **Old facts fall out.** Anything older than the window silently leaves the brief,
  including exactly the stable facts (home, family, job, tastes, routines) that should
  never leave it.
- **"Most recent" is insertion order**, not recency of update: `list_items` is
  insertion-ordered (the curriculum contract) and the code takes the tail, so a concept
  that was merged or re-engaged today does not move up (FR-304 enriches in place).
- **Stable personal facts have no store.** A fact like "lives in Quimper" reaches the brief
  only if the extractor happened to save it as a concept or an episode — the extraction
  prompt asks for "facts or knowledge the user learned or discussed" and does not clearly
  cover self-descriptions.
- **No notion of change.** The brief never sees its predecessor, so "moved from Lyon to
  Paris" is only right if both the stored items and the LLM happen to get it right.

## Options (not decided)

- **A. Persistent user profile + budgeted rebuild.** An explicit extraction path for stable
  facts about the user with replace-on-change semantics (reusing the FR-304 upsert, which
  already updates "from current state to next"); the brief is regenerated from the whole
  durable state — every profile fact, plus recent/significant episodes within a *token*
  budget ranked by engagement and recency of update. Stays a disposable, regenerable view.
- **B. Incremental brief:** `brief' = LLM(brief, delta)` with a periodic full rebuild.
  Cheap per run, but drifts (each rewrite loses detail — poor with an ~8B model), persists
  errors until the next rebuild, and turns the brief into state that cannot be re-derived.
- **C. Minimal patch:** rank by update recency and engagement, budget by tokens, pass the
  previous brief as context. Does not give stable facts a home.

## Decision procedure

Build the eval `brief` Suite first, run it on **today's code** for a baseline, then compare
A / B / C on the *same* Ledgers and replayed scenarios (coverage of current facts, stale-value
rate after a revision, invention rate, effect of history length). The choice is taken from
those Scores, recorded in `tasks.md`, and FR-308 is rewritten with the fix.

## Out of scope

Changing what the brief is *for* (system-prompt injection at session start, FR-109).
