# Nothing in the pipeline checks whether stored content is true

**Requirements:** none yet — no fix proposed, recorded as a known, chosen
limitation
**Status:** open — no design proposed

## Problem

A high-content, on-topic, internally coherent conversation is precisely the
shape that clears every existing gate — worthiness (FR-307) asks whether a
conversation is worth remembering, not whether it's true; extraction lifts
concepts from the transcript; FR-310 checks who *said* a term, not whether the
claim holds. Nothing checks factual accuracy. The failure mode is fluent
wrongness getting in, not noise.

## Scope

Undecided. Recorded so this is a known, chosen limitation rather than a
surprise — not yet scoped into a fix.

## Out of scope (for now)

A factuality gate against a local ~8B model is its own project; `llama3.1:8b`'s
domain accuracy (French astronomy, observed here) may simply be the operating
limit (cf. TR-952 on model choice).
