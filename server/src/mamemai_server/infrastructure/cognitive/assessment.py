# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Cognitive-training assessment — offline, post-upsert (TR-704).

Three things that are easily conflated are kept apart here, per
docs/BRIEF_persona_cognitive.md:

- **Measure** — yes. Without knowing whether a retrieval succeeded, the persona cannot
  schedule the next one, and spaced retrieval is the whole mechanism.
- **Display** — no. Nothing here reaches the user. No spoken score, no streak, no verdict.
- **Persist** — only what cross-session scheduling needs, in this persona's own opaque
  `persona_state` (INV-6).

Measuring without displaying is coherent precisely because the testing effect comes from
the act of retrieval, not from being told how you did. It also avoids the failure mode a
persona in this space has to avoid: day-to-day variance (sleep, mood, time of day) dwarfs
any real trend, so a visible score invites the user to read noise as decline. "My score
dropped three times — am I declining?" is a question this persona must never manufacture.
"""
from collections.abc import Sequence
from datetime import date, datetime, UTC
from uuid import UUID

from ...domain.model import Conversation, MemoryType, Procedure, Speaker
from ...services.ports import ItemAssessment, MemoryItem, PersonaRepository

from .state import (
    DEFAULT_HALF_LIFE_GROWTH,
    DEFAULT_HALF_LIFE_SHRINK,
    DEFAULT_INITIAL_HALF_LIFE_DAYS,
    MIN_HALF_LIFE_DAYS,
    STATE_HALF_LIFE_DAYS,
    STATE_LAST_PRACTISED_AT,
    STATE_MISSES,
    STATE_RETRIEVALS,
    STATE_SESSIONS_PRACTISED,
)

# settings keys (this persona's vocabulary, opaque to generic code)
SETTING_HALF_LIFE_GROWTH = "half_life_growth"
SETTING_HALF_LIFE_SHRINK = "half_life_shrink"
SETTING_INITIAL_HALF_LIFE = "initial_half_life_days"


def _looked_retrieved(item: MemoryItem, conversation: Conversation) -> bool:
    """Did the user appear to actually retrieve this item, or merely sit through it?

    Deliberately a cheap, transparent heuristic rather than an LLM judgment, and the
    reason is not cost. A wrong "you missed that" is invisible here — it silently
    shortens an item's half-life and surfaces it more often — so a rule the user could
    have explained to themselves beats a verdict nobody can audit. It errs toward
    *retrieved*: the cost of a false success is one delayed repeat, the cost of a false
    miss is a persona that keeps hammering something the user knows perfectly well.

    Genuine judgment of answer quality wants an LLM (the tutor has `PracticeJudge` for
    exactly that). Doing it here would mean designing a second judge before this persona
    has ever run; that waits for real sessions.
    """
    name = getattr(item, "name", None)
    if not name:
        return False
    said = [t.content for t in conversation.turns if t.speaker is Speaker.USER]
    if not said:
        return False
    # Substantive engagement with the item, not a bare acknowledgement: the user's own
    # words must name it, in a turn long enough to be an attempt at recall.
    lowered = name.lower()
    return any(lowered in text.lower() and len(text.split()) >= 5 for text in said)


class CognitiveAssessmentStrategy:
    """Implements PersonaAssessmentPort for the cognitive-training persona.

    Writes only to items this persona actually put in front of the user this
    conversation (`touched_items`, TR-704). Note the asymmetry with selection: this
    persona may *read* across personas (FR-303, `reads_across_personas`), and it writes
    `persona_state` on items it does not own. That is safe by the single-writer contract
    — `persona_state` is keyed to the writing persona's own vocabulary and no other
    persona reads these keys — and it is the point: scheduling a user's *own* concept for
    re-retrieval is meaningless if the schedule cannot be recorded against that concept.
    """

    def __init__(self, persona_repo: PersonaRepository, now_fn=lambda: datetime.now(UTC)) -> None:
        self._persona_repo = persona_repo
        self._now = now_fn

    def assess_items(
        self,
        persona_id: UUID,
        conversation: Conversation,
        touched_items: Sequence[MemoryItem],
    ) -> Sequence[ItemAssessment]:
        persona = self._persona_repo.get(persona_id)
        settings = (persona.settings if persona else None) or {}
        growth = float(settings.get(SETTING_HALF_LIFE_GROWTH, DEFAULT_HALF_LIFE_GROWTH))
        shrink = float(settings.get(SETTING_HALF_LIFE_SHRINK, DEFAULT_HALF_LIFE_SHRINK))
        initial = float(settings.get(SETTING_INITIAL_HALF_LIFE, DEFAULT_INITIAL_HALF_LIFE_DAYS))
        today: date = self._now().date()

        assessments: list[ItemAssessment] = []
        for item in touched_items:
            if item.id is None:
                continue
            existing = dict(getattr(item, "persona_state", None) or {})
            retrieved = _looked_retrieved(item, conversation)

            half_life = float(existing.get(STATE_HALF_LIFE_DAYS, initial))
            if retrieved:
                half_life *= growth
            else:
                half_life = max(half_life * shrink, MIN_HALF_LIFE_DAYS)

            assessments.append(ItemAssessment(
                item_id=item.id,
                memory_type=MemoryType.PROCEDURE if isinstance(item, Procedure) else MemoryType.CONCEPT,
                persona_state={
                    **existing,
                    STATE_LAST_PRACTISED_AT: today.isoformat(),
                    STATE_HALF_LIFE_DAYS: half_life,
                    STATE_RETRIEVALS: int(existing.get(STATE_RETRIEVALS, 0)) + (1 if retrieved else 0),
                    STATE_MISSES: int(existing.get(STATE_MISSES, 0)) + (0 if retrieved else 1),
                    STATE_SESSIONS_PRACTISED: int(existing.get(STATE_SESSIONS_PRACTISED, 0)) + 1,
                },
            ))
        return assessments
