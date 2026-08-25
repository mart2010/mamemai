# Decide the persona-contract sequencing

**Requirements/design:** [BRIEF_persona_contract.md](../../BRIEF_persona_contract.md)
**Status:** open — prerequisite satisfied, sequencing decision not yet made

## Problem

`BRIEF_persona_contract.md` names a deliberately dissimilar persona #2 as its
prerequisite for collapsing the four persona extension ports into one
turn-shaped contract. That prerequisite is now satisfied on paper (see
`docs/changes/persona-cognitive-remaining-gaps/`), and building it surfaced two
elements to add to the contract's proposed shape beyond what the brief
originally sketched: **durable emission** (a persona producing something
generic addressed to another persona, e.g. the divergence flag) and **declared
commands** (persona-scoped `Directive.action` values that generic code routes
without interpreting — today it's a single `== "switch_persona"` test at
`session.py:621`).

## Scope

Decide whether/how to sequence the contract work now that its prerequisite is
met, incorporating the two new elements above. Independent of the contract
either way: moving pacing policy out of `_next_selected_item`.

## Out of scope

The cognitive persona's own remaining gaps — tracked separately in
`docs/changes/persona-cognitive-remaining-gaps/`.
