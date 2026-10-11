# PLAN.md — Mamemai Implementation Plan

## Status Legend
- `[ ]` not started
- `[~]` in progress
- `[x]` done

## Where the project stands (2026-08-08)

Phases 1–13 are implemented and verified: domain + service layers, all infrastructure
adapters (Postgres+pgvector, faster-whisper, Ollama/OpenRouter, Kokoro, e5 embeddings),
the full WebSocket server, offline consolidation pipeline, MemoryBrief generation and
injection, the install wizard (incl. model downloads and re-run pre-fill), the config
placement / persona-lifecycle refactor, persona extension ports, the bundle format +
`InstallPersonaBundle`, the Italian language-tutor persona (selection / assessment /
enrichment strategies, two-teacher cast via per-segment language detection), the
installed-languages contract + GA response-language mirroring, the first slice of the
cognitive persona (Coach), and the context-overflow / turn-latency / VAD-waste fixes
found monitoring real GPU-workstation sessions between 2026-07-16 and 2026-08-08.

The full task-by-task record — findings, live-verification notes, benchmark numbers,
rejected alternatives — is archived in
[docs/archive/PLAN_phases_1-13.md](archive/PLAN_phases_1-13.md) (through 2026-07-14) and
[docs/archive/PLAN_2026-07-16_to_2026-08-08.md](archive/PLAN_2026-07-16_to_2026-08-08.md)
(2026-07-16 through 2026-08-08); references elsewhere in the repo (code comments, briefs,
tests) to "PLAN.md Phase N" resolve in the first one. The spec ([docs/spec/](spec/)) is
the present-tense description of behaviour; this file tracks only what's still open, in
[docs/changes/](changes/README.md) — [docs/BACKLOG.md](BACKLOG.md) holds what hasn't been
promoted to an active change yet.

---

## Open changes

See [docs/changes/README.md](changes/README.md) for the convention this section indexes.
Each row is a `docs/changes/<slug>/` folder — read `proposal.md` there for the problem
statement and `tasks.md` for the checklist and findings.

| Change | Status | Requirements / design |
| --- | --- | --- |
| [FR-309-language-recall-floor](changes/FR-309-language-recall-floor/proposal.md) | open | FR-309, TR-314 |
| [FR-307-extraction-naming-dedup](changes/FR-307-extraction-naming-dedup/proposal.md) | open — deferred pending live data | FR-307, FR-304, TR-609 |
| [TR-951-token-budgets](changes/TR-951-token-budgets/proposal.md) | in progress — parts 1 & 3 done, 2 & 4 open | TR-951, TR-952, TR-309, FR-707 |
| [TR-957-vad-benchmark-remeasure](changes/TR-957-vad-benchmark-remeasure/proposal.md) | in progress — fixes shipped, remeasure open | TR-957, TR-951 |
| [factuality-gate](changes/factuality-gate/proposal.md) | open — no design proposed yet | — |
| [FR-310-provenance-prefers-stt-errors](changes/FR-310-provenance-prefers-stt-errors/proposal.md) | open | FR-310 |
| [turn-latency-reply-length](changes/turn-latency-reply-length/proposal.md) | open — streaming fix shipped, 3 levers open | TR-951, TR-952, TR-308, TR-309 |
| [persona-cognitive-remaining-gaps](changes/persona-cognitive-remaining-gaps/proposal.md) | in progress | [BRIEF_persona_cognitive.md](BRIEF_persona_cognitive.md) |
| [persona-contract-sequencing](changes/persona-contract-sequencing/proposal.md) | open | [BRIEF_persona_contract.md](BRIEF_persona_contract.md) |
| [live-smoke-verification](changes/live-smoke-verification/proposal.md) | open — manual checklist, not yet run | Phase 12/13 |
| [memory-brief-rebuild](changes/memory-brief-rebuild/proposal.md) | open — measure first (eval baseline), then fix | FR-109, FR-308 |
| [eval/](../eval/docs/PLAN.md) | in progress — design agreed 2026-10-11; slice 0 done, slice 1 next | [eval/docs/DESIGN.md](../eval/docs/DESIGN.md) (own plan, glossary, EV-INV invariants) |

See [docs/BACKLOG.md](BACKLOG.md) for not-yet-designed features, known leftovers,
calibration constants waiting on real usage data, and accepted limitations — items there
get promoted to a row in this table (and a `docs/changes/` folder) once someone starts
on them.
