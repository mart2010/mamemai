# docs/changes/ — in-flight change proposals

Each subdirectory is one in-flight change: a problem being investigated or a
decision still open, with its history kept local to it instead of accumulating in
one ever-growing file.

```
docs/changes/<slug>/
  proposal.md   # always: the problem, the affected FR-/TR-/INV- IDs, scope
  design.md     # only for changes big enough to need one — the same role
                # BRIEF_*.md docs at the project root play for larger decisions
  tasks.md      # the [ ]/[~]/[x] checklist and the dated findings/measurements
                # that justify it
```

## Lifecycle

1. **Open** — create the folder before code starts, with at least `proposal.md`.
2. **Work** — tasks get checked off; findings, measurements and live-verification
   notes accumulate in `tasks.md` (or `design.md`), local to this change, not in
   `../PLAN.md`.
3. **Spec update** — unchanged from [docs/spec/SPEC.md](../spec/SPEC.md)'s
   alignment loop: a change that alters observable behaviour, a format, a
   threshold or an invariant updates the affected `FR-`/`TR-`/`INV-` requirement
   in the same commit. `docs/spec/` is always the authority; nothing here
   overrides it.
4. **Archive** — once resolved, the whole folder moves to
   `docs/archive/changes/<slug>/` verbatim, no trimming — the same
   "freeze it, don't delete it" rule [docs/archive/README.md](../archive/README.md)
   already applies to completed phases and BRIEFs.

## Relationship to other docs

- [../PLAN.md](../PLAN.md) stays a short project-status summary plus an index of
  what's currently open here — it doesn't carry the narrative itself any more.
- [../BACKLOG.md](../BACKLOG.md) holds items not yet promoted to an active
  change: not-yet-designed features, known leftovers, calibration constants
  waiting on real usage data, and limitations accepted by explicit decision. A
  backlog line gets its own folder here only once someone actually starts on it.
- `../BRIEF_*.md` at the project root continue to serve as the larger design
  notes for existing decisions; a new design note of that size becomes a
  `design.md` inside the relevant change folder instead of a new top-level
  `BRIEF_*.md`.
