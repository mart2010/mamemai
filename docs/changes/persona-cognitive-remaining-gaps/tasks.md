# Tasks

Deferred from the original brief, unchanged:
- [ ] Delayed-recall debt (brief's Gap 2 — the first thing this persona will hit)
- [ ] Divergence-flag persistence (brief's Gap 4; the *spoken* half is in the
      prompt and pinned by a test)
- [ ] Voice-selectable exercise type (declared commands)
- [ ] Curated reasoning items (third item kind)

Found live, 2026-08-07:
- [ ] Unscoped `memory_brief` (FR-109) reaches every persona regardless of
      `reads_across_personas` — Coach's first reply after switching opened with
      a fabricated GA-session topic. Flagged in round 1, not yet fixed.
- [ ] Check whether `_next_selected_item`/pacing is under-serving real items
      each turn, or the store is just sparse and the model fills the gap with
      invention — this session's exercises ran mostly on Coach's own invented
      content until the user explicitly asked for "un exercice de mémoire."
      Not yet diagnosed.
- [ ] **Undesigned**: an "erase this (false) episode" capability. User hit this
      live, repeatedly asking Coach to forget a fabricated episode. Coach's own
      bundle prompt anticipates the shape ("suggest they sort it out with the
      general assistant") but the model didn't follow it live. GA has no erase
      mechanism either. Can't be a pure closed-vocabulary Directive like
      `switch_persona`/`end_session` — "erase this episode" needs to identify
      *which* one. Leading idea, undeveloped: target whatever memory item is
      currently in the turn's working context rather than parsing a
      description. Only the *extracted* Episode/Concept row would be
      deleted — the raw JSONL session log stays untouched (INV-5), so the
      source material survives even a wrong erase.

## Findings — first slice (2026-08-02)

`infrastructure/cognitive/` (selection + assessment + its own `persona_state`
vocabulary), a persona-only bundle at `bundles/cognitive-coach/`, registered in
both factory registries.

End-to-end verified against real Postgres (test DB): bundle installs with 0
items, `reads_across_personas`/`extracts_episodes` persist, `response_language`
derived from the user (a French user got a French-speaking Coach from an
English-authored bundle), switch directive auto-created in French ("basculer",
"coach").

**Two gaps found while building, neither in the brief's original four.**
*Gap 5* — `list_items` raised for episodes, so the autobiographical family had
no way to reach its material. Fixed with `list_episodes`, recency-ordered.
*Gap 6, the serious one* — **FR-303 made the persona impossible as designed.**
Concept/procedure knowledge is persona-scoped end-to-end, so a persona that
exists to quiz the user on *their* concepts could only ever see concepts
extracted from its own sessions. Resolved by separating **read** scope from
**write** scope: `AssistantPersona.reads_across_personas` (default false,
read-only, INV-9's cascade and every write path untouched). Episodes never
needed it — INV-10 already made them shared.

Design choices: family rotation is per day, not per session (deterministic
across a reconnect); assessment uses a transparent heuristic, not an LLM judge,
erring toward "retrieved" (a false miss silently shortens an item's half-life
rather than producing an unauditable verdict).

## Findings — live verification, round 1 (2026-08-07)

Installed into the real `mamemai` DB (`Coach`, `response_language=fr`
auto-derived, `reads_across_personas=t`). **The switch trigger itself was
unreachable**: French was keyed on the bare infinitive `"basculer"`, but real
attempts ("bascule vers mon coach", "on bascule vers le coach") all used
conjugated/nominal forms that don't contain that substring, so none matched.
Fixed by keying French on the shared root `"bascul"` — matches every
conjugation deterministically, no conjugation table. Not evaluated: whether the
same bare-infinitive/conjugation gap exists for es/it/pt.

## Findings — live verification, round 2, same evening (2026-08-07)

With the trigger fixed, the switch fired mechanically but didn't *feel* like it
worked: `llama3.1:8b`, still holding pre-switch `recent_turns` plus the
unscoped memory brief, kept GA's topic for several turns and even claimed it
"can't switch personas." Fixed with a `switched_to_persona_name` injection
symmetric to the existing blocked-switch one (TR-315).

Separately, Coach's one-exercise-per-turn design (TR-306) kept gluing exercise
questions onto farewell replies, since nothing recognized session-ending
intent. Modeled the same deterministic way as persona switching: a third
`Directive.action`, `end_session` (FR-115, TR-317) — closed-vocabulary
farewell word per installed language, skips recall/selection, injects a
goodbye-only instruction, and closes the WebSocket after the reply.

All three fixes live-verified against the real server/client the same evening:
switch confirmation with no more "can't switch" denial and correct
self-identification afterward; end-session directive producing a clean single
goodbye with no glued-on exercise; connection close confirmed via
`session_closed`/`clean_exit: true` and `ss -tnp` showing no active TCP
connection afterward while the server stayed up. 559 unit tests green.
