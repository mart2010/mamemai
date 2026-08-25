# One global recall floor cannot fit three languages

**Requirements:** FR-309, TR-314
**Status:** open — design decision needed, nothing measured yet for the fix itself

## Problem

`MIN_RECALL_SIMILARITY` is a single global constant gating recall. Measured
per-language, the margin between "absent" and "present" probes is positive for
every supported language (fr/en/it) — but the *global* margin isn't. The single
floor works today only because the language-specific bands happen to overlap;
that overlap isn't guaranteed to hold as the mix of stored languages changes.

## Scope

Decide how the recall floor should vary by language, and implement it.
`ProcessTurn` already has the turn's STT-detected language in hand at the recall
step, so the plumbing to condition on it exists — this is a design/calibration
question, not a wiring one.

## Out of scope

Re-deriving `MIN_RECALL_SIMILARITY` itself or any other embedding threshold
(merge/disambiguate thresholds, etc.) — this is specifically about whether one
global number can represent all supported languages.
