# One subject, five names — extraction's naming problem

**Requirements:** FR-307, FR-304, TR-609
**Status:** open — deliberately deferred, blocked on live data rather than design

## Problem

Extraction repeatedly returns multiple concept rows for one real-world subject:
synonym clusters (five names for one phenomenon in a single run), morphological
pairs (`Halos`/`halo`), STT-corrupted spellings promoted to their own concepts,
and bare-word non-concepts (`glace`, `latitude`). Roughly half of what extraction
returns is one of these, at every extraction-window size measured, and it is the
largest quality defect the window-sizing investigation found.

## Scope

Reduce duplicate/junk concept rows from extraction without introducing a scalar
similarity threshold that can't distinguish "same subject, different wording"
from "different subject, same register" (see the embedding-dedupe finding below —
that approach is measured dead).

## Out of scope

Extraction window sizing itself (settled at `EXTRACTION_WINDOW_TURNS = 16`, see
`docs/archive/PLAN_2026-07-16_to_2026-08-08.md`). The factuality problem
(fabricated-but-plausible content) is a related but separate concern — see
`docs/changes/factuality-gate/`.
