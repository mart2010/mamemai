# Persona #2 (the cognitive persona) — remaining gaps after the first slice

**Requirements/design:** [BRIEF_persona_cognitive.md](../../BRIEF_persona_cognitive.md)
**Status:** in progress — first slice built and live-verified; several gaps remain

## Problem

The cognitive persona ("Coach") — the deliberately dissimilar second persona
that made the persona-contract designable — has its first slice built,
installed, and live-verified against real Postgres/Ollama. Several gaps the
original brief listed as deferred are still open, plus new ones found live on
2026-08-07 that the brief didn't anticipate.

## Scope

Track the persona's remaining rough edges through to a persona that's genuinely
comfortable to use daily, per the brief's own goal. See `tasks.md` for the
current list — it spans brief-deferred gaps (delayed-recall debt,
divergence-flag persistence, voice-selectable exercise type, curated reasoning
items) and live-session findings (memory brief leaking GA's topic into Coach's
opening turn, exercises running on invented rather than real content, an
undesigned "erase this false episode" request).

## Out of scope

The persona-contract abstraction itself (collapsing the four extension ports) —
see `docs/changes/persona-contract-sequencing/`, which this persona's Part 3 was
built to inform.
