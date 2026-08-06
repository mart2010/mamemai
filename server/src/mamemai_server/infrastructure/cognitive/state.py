# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""This persona's own `persona_state` vocabulary (INV-6).

Single-writer contract, the same one the memory model already states: written only by
this persona's assessment strategy, read only by its selection strategy. Generic code
never learns these names.

They deliberately overlap textually with the language tutor's — both track a half-life,
because both do spaced retrieval — and they are still *separate declarations*. The
retention *formula* was made generic (`domain.model.retention`, 2026-08-02) precisely so
two personas could share the curve without sharing a key namespace: standardising the
keys would invite generic code to read inside `persona_state`. Generalise the
calculation, not the data.
"""

STATE_LAST_PRACTISED_AT = "last_practised_at"  # ISO date — day granularity, as with any spaced schedule
STATE_HALF_LIFE_DAYS = "half_life_days"        # grows on a successful retrieval, shrinks on a miss
STATE_RETRIEVALS = "retrievals"                # successful retrievals only, never mere exposures
STATE_MISSES = "misses"
STATE_SESSIONS_PRACTISED = "sessions_practised"

# Starting half-life for an item this persona has never quizzed. Deliberately short: the
# first retrieval of something the user last thought about weeks ago is the point of the
# exercise, so a new item should come up soon rather than be assumed known.
DEFAULT_INITIAL_HALF_LIFE_DAYS = 1.0

# Growth on success / shrink on a miss. Placeholders pending real usage, same
# instrument-now-calibrate-later posture as every other threshold in this project.
DEFAULT_HALF_LIFE_GROWTH = 2.0
DEFAULT_HALF_LIFE_SHRINK = 0.5
MIN_HALF_LIFE_DAYS = 0.5
