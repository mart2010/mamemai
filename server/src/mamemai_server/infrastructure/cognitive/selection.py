# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Cognitive-training selection — the second concrete PersonaSelectionPort.

Where the language tutor selects from curriculum it authored, this persona selects from
whatever the user's own memory already holds, and turns each item into a *retrieval
prompt* rather than an exposure. The distinction is the whole point: the testing effect
comes from the act of retrieval, so an item must arrive as something to be recalled,
never as something to be read out (docs/BRIEF_persona_cognitive.md, Part 1).

Exercise families are derived from mamemai's memory types rather than invented, because
those types already partition along the same lines cognitive psychology partitions
memory: Concept/semantic, Procedure/procedural, Episode/episodic. Families that need no
substrate at all (invented stories, word lists, mental arithmetic, verbal fluency) are
driven entirely by the persona's system prompt and never reach this module — they are
the floor that keeps the persona working against an empty store, not the differentiator.
"""
from collections.abc import Callable, Sequence
from datetime import date, datetime, UTC
from uuid import UUID

from ...domain.model import MemoryType, retention
from ...services.ports import (
    MemoryItem,
    MemoryRepository,
    PersonaRepository,
    SelectedItem,
    SelectionPacing,
)

from .state import (
    DEFAULT_INITIAL_HALF_LIFE_DAYS,
    STATE_HALF_LIFE_DAYS,
    STATE_LAST_PRACTISED_AT,
)

STRATEGY_NAME = "cognitive_coach"

# AssistantPersona.settings keys (this persona's vocabulary, opaque to generic code).
SETTING_FAMILIES = "exercise_families"   # ordered rotation of substrate-backed families
SETTING_BATCH_SIZE = "batch_size"

# Substrate-backed families, by the memory type each retrieves from. A family absent
# from the persona's settings simply never comes up.
FAMILY_CONCEPT = "concept"
FAMILY_PROCEDURE = "procedure"
FAMILY_EPISODE = "episode"
DEFAULT_FAMILIES = (FAMILY_CONCEPT, FAMILY_PROCEDURE, FAMILY_EPISODE)

_FAMILY_MEMORY_TYPE = {
    FAMILY_CONCEPT: MemoryType.CONCEPT,
    FAMILY_PROCEDURE: MemoryType.PROCEDURE,
}

# Short by design. This persona's sessions are meant to be brief and frequent — the same
# little-and-often rationale as language practice — so a batch is a handful of items,
# not a lesson plan.
DEFAULT_BATCH_SIZE = 5


def _due_key(item: MemoryItem, today: date) -> float:
    """Forgetting-curve ranking: ascending retention, so the item closest to being lost
    comes first. This is the *opposite* of novelty ranking, and deliberately so — the
    whole value of practising over your own store is catching things on the way out.

    An item this persona has never quizzed sorts ahead of everything (-1.0), because a
    first retrieval is worth more than a well-timed repeat. Same sentinel convention the
    language tutor uses, and for the same reason; the shared part is the curve itself
    (`domain.model.retention`), never the state it reads.
    """
    state = getattr(item, "persona_state", None)
    if not state:
        return -1.0
    try:
        last = date.fromisoformat(state[STATE_LAST_PRACTISED_AT])
        half_life = float(state.get(STATE_HALF_LIFE_DAYS, DEFAULT_INITIAL_HALF_LIFE_DAYS))
    except (KeyError, TypeError, ValueError):
        return -1.0
    if half_life <= 0:
        return -1.0
    return retention((today - last).days, half_life)


def _retrieval_prompt(item: MemoryItem, family: str) -> str:
    """The instruction that turns a stored item into a retrieval attempt.

    Deliberately says what to elicit, never the answer: generic code injects
    `SelectedItem.context` verbatim into the turn, so anything stated here reaches the
    model, and a prompt that leaks the answer would turn a test back into a restudy.
    """
    if family == FAMILY_EPISODE:
        return (
            "Ask the user what they remember about this, without restating it yourself: "
            "when it happened, who was there, what led up to it. If what they recall "
            "differs from the note, say so plainly and show both versions — never decide "
            "which is right. The note can be wrong, their memory can have drifted, or the "
            "world can have moved on, and you cannot tell those apart."
        )
    if family == FAMILY_PROCEDURE:
        return (
            "Ask the user to walk through this procedure from memory, in their own words, "
            "one step at a time. Do not list the steps yourself first, and do not correct "
            "mid-flow — let them finish, then compare."
        )
    return (
        "Ask the user to explain this in their own words, from memory, without you "
        "defining it first. Follow with one 'why' or 'how' question that only makes sense "
        "if they actually recalled it."
    )


class CognitiveSelectionStrategy:
    """Implements PersonaSelectionPort for the cognitive-training persona.

    Monotype per day rather than per session: today is one family, tomorrow another. The
    brief's own framing ("today is stories, tomorrow is concepts"), and it falls out of
    the date rather than needing a session identifier — which `select_items` is not given
    and, under the current ports, could not be. Rotation is therefore deterministic and
    stateless: the same day always yields the same family, so a reconnect mid-day resumes
    the same kind of work instead of jumping to a different exercise.
    """

    # Both non-default, and both are why the pacing declaration had to exist (TR-306).
    # - This persona's session opening IS its first exercise. Greeting empty-handed and
    #   arriving with content only on the following turn wastes a turn of a session
    #   designed to last minutes.
    # - Its batches are small and its sessions short enough to exhaust one routinely;
    #   running dry means fetch more, not wind down.
    pacing = SelectionPacing(refetch_when_exhausted=True, include_activation_turn=True)

    def __init__(
        self,
        memory_repo: MemoryRepository,
        persona_repo: PersonaRepository,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._memory_repo = memory_repo
        self._persona_repo = persona_repo
        self._now = now_fn

    def family_for(self, today: date, families: Sequence[str]) -> str | None:
        """Which family today belongs to. Public because it is the persona's own notion
        of "what are we doing today", and its assessment strategy needs the same answer
        for the same day."""
        if not families:
            return None
        return families[today.toordinal() % len(families)]

    def select_items(
        self,
        persona_id: UUID,
        focus: str | None = None,
        limit: int = 10,
    ) -> Sequence[SelectedItem]:
        persona = self._persona_repo.get(persona_id)
        settings = (persona.settings if persona else None) or {}
        families = tuple(settings.get(SETTING_FAMILIES) or DEFAULT_FAMILIES)
        batch_size = min(int(settings.get(SETTING_BATCH_SIZE, DEFAULT_BATCH_SIZE)), limit)

        today = self._now().date()
        family = self.family_for(today, families)
        if family is None:
            return []

        items = self._items_for(family, persona_id, persona, batch_size)
        if not items:
            # An empty substrate degrades this persona rather than breaking it: the
            # generated families carry the session, driven by the system prompt alone.
            return []

        return [
            SelectedItem(item=item, context=_retrieval_prompt(item, family))
            for item in items[:batch_size]
        ]

    def _items_for(
        self, family: str, persona_id: UUID, persona: object, batch_size: int
    ) -> list[MemoryItem]:
        today = self._now().date()
        if family == FAMILY_EPISODE:
            # Recency, not the forgetting curve: an episode is not something the user is
            # trying to retain on a schedule, and "what happened lately" is what makes the
            # autobiographical family worth doing. Episodes carry no persona scope at all
            # (INV-10), so this needs no permission — only a way to list them, which is
            # what `list_episodes` added.
            return list(self._memory_repo.list_episodes(limit=batch_size))

        memory_type = _FAMILY_MEMORY_TYPE[family]
        # The read scope is the persona's own declaration (FR-303). This persona works
        # from the user's whole store — the concepts and procedures they built up talking
        # to any persona — so scoping the read to its own id would leave it quizzing only
        # on what it was told during its own sessions, which is circular.
        scope = None if getattr(persona, "reads_across_personas", False) else persona_id
        items = self._memory_repo.list_items(scope, (memory_type,))

        # ...but only what the USER produced. Reading across personas also reaches
        # curriculum other personas *authored* — a language tutor's vocabulary list, say —
        # and that is not this persona's material: it belongs to the persona that wrote it
        # and drills it on its own schedule. Worse, authored items carry no state from
        # this persona, so they all rank "never practised" and would crowd out the user's
        # own knowledge entirely. `origin` is exactly the distinction needed and already
        # exists (FR-310): "organic" is what the user's own conversations produced.
        items = [i for i in items if getattr(i, "origin", "organic") == "organic"]

        items.sort(key=lambda i: _due_key(i, today))
        return items
