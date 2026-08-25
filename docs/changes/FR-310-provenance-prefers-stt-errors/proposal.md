# The provenance gate prefers STT's mistakes to the real words

**Requirements:** FR-310
**Status:** open — needs a design decision, not a patch

## Problem

FR-310 admits a brand-new organic concept only if the **user** literally named
it (`_mentioned_in`, whole-word against their own turns). When STT corrupts what
the user said, the corrupted form is what has provenance — the assistant may
have used the correct term, but only the assistant said it, so the correct
spelling is discarded while the mangled one (which the user repeated back) is
stored. Confirmed twice on real data (2026-08-02 and 2026-08-03).

## Scope

Decide how provenance should be established when STT is a plausible source of
corruption between what the user said and what was transcribed. Any fix needs
to stay a deterministic filter, not a plea to the model — the posture that has
held for FR-114 and `matches_trigger`.

## Out of scope

The naming/synonym-cluster problem in extraction itself (related but separate —
see `docs/changes/FR-307-extraction-naming-dedup/`, which surfaced the same
conv 22 data from the dedupe angle).
