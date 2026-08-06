# Archived spec narrative

`docs/spec/` (`SPEC.md`, `GLOSSARY.md`, `FUNCTIONAL.md`, `TECHNICAL.md`) states
**current** behaviour only — present tense, no change-log prose. Requirements
accumulate dated narrative over time regardless (a measurement that justified a
threshold, an incident write-up that motivated a rule, a "was X until date Y"
comparison) — useful when it was written, but it makes the spec harder to read as it
piles up, and it goes stale as soon as the current value changes again.

When trimming a requirement's prose back down to what's true now — whether as part of
a spec-changing commit (SPEC.md's alignment loop) or a periodic drift review — the
narrative being removed moves here instead of being deleted outright, one file per
requirement or per review pass:

```
docs/archive/spec/<ID>_<short-topic>.md      # e.g. FR-307_windowing_history.md
docs/archive/spec/drift_review_YYYY-MM-DD.md # a whole review pass's worth of trims
```

Each file should say which requirement ID(s) it came from and roughly when, so it's
still greppable from the live spec if the history is ever needed. Nothing here is
authoritative for current behaviour — `docs/spec/` always wins; this is provenance,
not a second copy of the spec.
