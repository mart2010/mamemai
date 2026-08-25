# Tasks

- [ ] Decide the approach:
  - **Per-language constants** — needs a probe set per language, and
    re-derivation as the DB grows.
  - **Normalise the score against that language's own background similarity** —
    needs no constants, but is unmeasured.
- [ ] Implement the chosen approach in the recall-gate/recall-floor path.
- [ ] Re-measure against real data the same way the finding below was measured.

## Findings (2026-08-04)

Promoted out of the e5-prefix investigation, which is where it was measured (see
the archived `FR-309-language-recall-floor` predecessor work in
`docs/archive/PLAN_2026-07-16_to_2026-08-08.md` for the full e5 prefix story).
Every per-language margin is positive; the global one is not, so the single
`MIN_RECALL_SIMILARITY` works only because the bands happen to overlap:

| | fr | en | it |
| --- | --- | --- | --- |
| margin under asym | +0.018 | +0.027 | +0.017 |

The absent ceiling differs by language (fr 0.834, en 0.806, it 0.826), so one
number is simultaneously too strict for one language and too loose for
another — and it shifts as the mix of stored languages changes.
