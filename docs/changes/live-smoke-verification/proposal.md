# Phase 12/13 live smoke tests

**Requirements:** installed-languages contract, GA response-language mirroring
(Phase 13); tutor persona/cast/HVPT flow (Phase 12)
**Status:** open — manual verification checklists, not yet run

## Problem

Two features shipped and unit/integration-tested but never verified against
real hardware in a live session: the Phase 13 installed-languages behaviour
(GA mirrors installed languages, correctly reminds/redirects for uninstalled
ones) and the Phase 12 tutor flow with real mic/speaker audio (persona switch,
two-teacher cast voice switching, HVPT rotation).

## Scope

Run both checklists on the workstation with real hardware; no code change
implied unless a checklist step surfaces a bug.
