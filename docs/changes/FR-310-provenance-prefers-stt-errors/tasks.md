# Tasks

- [ ] Design a fix. Structural nature of the problem (below) rules out a simple
      threshold change — needs to stay a deterministic filter, not a plea.
- [ ] Revisit sizing/urgency once there's live data on how often this actually
      bites (conv 22, the case that surfaced it, is being left as-is precisely
      because re-consolidating it now would store the bad result).

## Findings (2026-08-02) — first surfaced

Surfaced by the same-day concept-gate split fix, not caused by it. On conv 22
the surviving pair is `Halos` (correct) and **`Horreur Boréale`** — a
nonexistent horror film, which is what Whisper wrote for "aurore boréale" —
while `Noctiluques`, `Zodikallicht` and the rest are discarded for having been
said only by the assistant, which had them right.

So conv 22 should **not** be re-consolidated: extraction now works on it, and
what it would store is worse than nothing. Left as it is.

Mitigated going forward by the STT upgrade to `medium` (transcribes these words
correctly), but the interaction is structural: any STT error the user doesn't
correct becomes the only spelling with provenance.

## Findings (2026-08-03) — confirmed on new data, plus a third interaction

The hazard predicted above, reproduced, plus a third pattern. Extracting conv
23's first 16 turns (the slice that still works) proposes six concepts:

- **`nuages noctiluc` passes as USER-SAID.** STT wrote "Noctiluc" for
  "noctiluques"; the assistant used the correct term; the user repeated the
  mangled form back, so the corrupted spelling has provenance and the correct
  one doesn't. Same mechanism, now observed with `medium` rather than
  `small` — the STT upgrade shrank the errors without removing the mechanism.
  Three spellings of one concept appeared in this single session
  ("zodiacales" / "zodiaquales", "Noctiluc").
- **`géantes bleues` is discarded as NO-PROVEN** although the user discussed it
  for three turns — they said "géante bleue" (singular) and STT rendered
  another attempt as "les géants de bleu". Whole-word matching cannot see
  inflection: the same failure as the "hiking"/"hiked" case documented at
  `upsert.py`, still live.
- **The user also misheard a correct denial.** Asked whether noctilucent
  clouds had come up before, the assistant honestly said it had no such
  memory — in the final clause of a four-paragraph spoken reply — and the
  user's next turn opened "on a déjà parlé de ces nuages noctilucs." In
  voice-only with no barge-in (INV-4) there is one pass and no scanning back,
  so a disclaimer at the end of a monologue doesn't land. If a reply denies a
  memory, the denial has to lead. Worth an FR-309 amendment, tracked here since
  it's the same root session's finding.
