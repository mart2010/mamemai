"""Cognitive-training selection strategy — persona #2's live hook.

Spec: FR-303, FR-506, TR-306, TR-609.
"""
from datetime import date, datetime, UTC
from uuid import uuid4

from mamemai_server.domain.model import (
    AssistantPersona,
    Concept,
    DEFAULT_VOICE_ROLE,
    Episode,
    Language,
    Procedure,
)
from mamemai_server.infrastructure.cognitive.selection import (
    DEFAULT_FAMILIES,
    FAMILY_CONCEPT,
    FAMILY_EPISODE,
    FAMILY_PROCEDURE,
    CognitiveSelectionStrategy,
)
from mamemai_server.infrastructure.cognitive.state import (
    STATE_HALF_LIFE_DAYS,
    STATE_LAST_PRACTISED_AT,
)

from tests.fakes.fakes import FakeMemoryRepository, FakePersonaRepository

PERSONA_ID = uuid4()
OTHER_PERSONA_ID = uuid4()
_NOW = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)  # a "procedure" day under the default rotation


def _persona(reads_across_personas: bool = True, settings: dict | None = None) -> AssistantPersona:
    return AssistantPersona(
        id=PERSONA_ID,
        name="Coach",
        system_prompt="You run practice sessions.",
        languages=[],
        response_language=Language("en"),
        voices={DEFAULT_VOICE_ROLE: "af_heart"},
        is_system=False,
        created_at=_NOW,
        updated_at=_NOW,
        strategy="cognitive_coach",
        settings=settings,
        reads_across_personas=reads_across_personas,
    )


def _concept(name: str, id_: int, persona_id=OTHER_PERSONA_ID, persona_state=None) -> Concept:
    return Concept(
        id=id_, persona_id=persona_id, name=name, description=f"About {name}.",
        language=Language("en"), persona_state=persona_state,
    )


def _strategy(memory_repo, persona, now=_NOW) -> CognitiveSelectionStrategy:
    persona_repo = FakePersonaRepository()
    if persona is not None:
        persona_repo.save(persona)
    return CognitiveSelectionStrategy(memory_repo, persona_repo, now_fn=lambda: now)


class TestFamilyRotation:
    """Monotype per day, not per session: today is one family, tomorrow another."""

    def test_rotation_is_deterministic_for_a_given_day(self):
        """Spec: TR-306 — the same day must always yield the same family, so a reconnect
        mid-day resumes the same kind of work rather than jumping to another exercise.
        `select_items` is given no session identifier, so the date is what makes this
        stateless and repeatable."""
        s = _strategy(FakeMemoryRepository(), None)
        first = s.family_for(date(2026, 8, 3), DEFAULT_FAMILIES)
        assert first == s.family_for(date(2026, 8, 3), DEFAULT_FAMILIES)

    def test_consecutive_days_cycle_through_every_family(self):
        """Spec: TR-306 — over a full cycle each family comes up exactly once, so no
        family is starved."""
        s = _strategy(FakeMemoryRepository(), None)
        seen = [s.family_for(date(2026, 8, 3) .fromordinal(date(2026, 8, 3).toordinal() + d), DEFAULT_FAMILIES)
                for d in range(len(DEFAULT_FAMILIES))]
        assert sorted(seen) == sorted(DEFAULT_FAMILIES)

    def test_no_families_configured_selects_nothing(self):
        """Spec: TR-306 — a persona configured with no substrate families still runs; its
        generated exercises come from the system prompt and never reach this module."""
        repo = FakeMemoryRepository()
        repo.concepts.append(_concept("hiking", 1))
        assert _strategy(repo, _persona(settings={"exercise_families": []})).select_items(PERSONA_ID) == []


class TestReadScope:
    """Spec: FR-303 — the declaration that makes this persona possible at all."""

    def test_reads_other_personas_concepts_when_declared(self):
        """The whole premise: it practises over concepts the user built up talking to
        *other* personas. Scoped to its own id it would only ever quiz on what it was
        told during its own sessions, which is circular."""
        repo = FakeMemoryRepository()
        repo.concepts.append(_concept("hiking", 1, persona_id=OTHER_PERSONA_ID))
        strategy = _strategy(repo, _persona(reads_across_personas=True), now=datetime(2026, 8, 2, 9, tzinfo=UTC))

        selected = strategy.select_items(PERSONA_ID)

        assert [s.item.name for s in selected] == ["hiking"]

    def test_sees_only_its_own_scope_when_not_declared(self):
        """Spec: FR-303 — the default is unchanged for every other persona: the same name
        under different personas is different knowledge."""
        repo = FakeMemoryRepository()
        repo.concepts.append(_concept("hiking", 1, persona_id=OTHER_PERSONA_ID))
        strategy = _strategy(repo, _persona(reads_across_personas=False), now=datetime(2026, 8, 2, 9, tzinfo=UTC))

        assert strategy.select_items(PERSONA_ID) == []


class TestRetrievalPrompts:
    """Spec: FR-303 — an item must arrive as something to recall, never as something to
    read out; the testing effect comes from the attempt, not the exposure."""

    def _context_for(self, family: str) -> str:
        repo = FakeMemoryRepository()
        if family == FAMILY_EPISODE:
            repo.episodes.append(Episode(id=1, summary="Hiked near Chamonix.", happened_at=_NOW, origin_conversation_id=1))
        elif family == FAMILY_PROCEDURE:
            repo.procedures.append(Procedure(
                id=1, persona_id=OTHER_PERSONA_ID, name="deploying", description="How to deploy.",
                language=Language("en"), steps=["build", "ship"],
            ))
        else:
            repo.concepts.append(_concept("hiking", 1))
        persona = _persona(settings={"exercise_families": [family]})
        [selected] = _strategy(repo, persona).select_items(PERSONA_ID)
        return selected.context or ""

    def test_concept_prompt_withholds_the_definition(self):
        assert "without you" in self._context_for(FAMILY_CONCEPT)

    def test_procedure_prompt_withholds_the_steps(self):
        assert "Do not list the steps yourself first" in self._context_for(FAMILY_PROCEDURE)

    def test_episode_prompt_carries_the_divergence_rule(self):
        """Spec: FR-303 — report both versions, adjudicate neither. The persona cannot
        tell a bad extraction from a drifted memory from a changed fact, so it must not
        pick a winner; this is the rule most likely to erode silently, so it is pinned."""
        context = self._context_for(FAMILY_EPISODE)
        assert "never decide" in context
        assert "both versions" in context


class TestDueOrdering:
    """Spec: FR-506 — forgetting-curve ranking, the opposite of novelty: target what is
    about to be lost."""

    def _ordered(self, *concepts) -> list[str]:
        repo = FakeMemoryRepository()
        repo.concepts.extend(concepts)
        persona = _persona(settings={"exercise_families": [FAMILY_CONCEPT]})
        return [s.item.name for s in _strategy(repo, persona).select_items(PERSONA_ID)]

    def test_never_practised_comes_first(self):
        """A first retrieval is worth more than a well-timed repeat."""
        practised = _concept("known", 1, persona_state={
            STATE_LAST_PRACTISED_AT: _NOW.date().isoformat(), STATE_HALF_LIFE_DAYS: 10.0,
        })
        assert self._ordered(practised, _concept("fresh", 2))[0] == "fresh"

    def test_the_staler_item_outranks_the_recently_practised_one(self):
        stale = _concept("stale", 1, persona_state={
            STATE_LAST_PRACTISED_AT: "2026-07-01", STATE_HALF_LIFE_DAYS: 2.0,
        })
        recent = _concept("recent", 2, persona_state={
            STATE_LAST_PRACTISED_AT: _NOW.date().isoformat(), STATE_HALF_LIFE_DAYS: 2.0,
        })
        assert self._ordered(recent, stale)[0] == "stale"

    def test_unusable_state_is_treated_as_never_practised(self):
        """Spec: FR-506 — a malformed or partial state must make the item *more* due, not
        crash and not silently sort last: the persona cannot tell when it last came up,
        so the safe reading is that it is overdue."""
        broken = _concept("broken", 1, persona_state={STATE_LAST_PRACTISED_AT: "not-a-date"})
        recent = _concept("recent", 2, persona_state={
            STATE_LAST_PRACTISED_AT: _NOW.date().isoformat(), STATE_HALF_LIFE_DAYS: 2.0,
        })
        assert self._ordered(recent, broken)[0] == "broken"


class TestEpisodeFamily:
    def test_episodes_come_back_most_recent_first(self):
        """Spec: INV-10 — episodes carry no persona scope, so they need no read
        permission; what they needed was a way to be listed at all (`list_episodes`,
        added 2026-08-02 — before it, episodes were reachable only by similarity search,
        which cannot answer "what happened lately")."""
        repo = FakeMemoryRepository()
        repo.episodes.append(Episode(id=1, summary="Older.", happened_at=datetime(2026, 7, 1, tzinfo=UTC), origin_conversation_id=1))
        repo.episodes.append(Episode(id=2, summary="Newer.", happened_at=datetime(2026, 7, 28, tzinfo=UTC), origin_conversation_id=2))
        persona = _persona(settings={"exercise_families": [FAMILY_EPISODE]})

        selected = _strategy(repo, persona).select_items(PERSONA_ID)

        assert [s.item.summary for s in selected] == ["Newer.", "Older."]


class TestPacing:
    def test_declares_both_non_default_flags(self):
        """Spec: TR-306 — this persona is why SelectionPacing exists. Its session opening
        IS its first exercise (so it must not spend the activation turn greeting), and its
        batches are small enough to exhaust routinely (so running dry means fetch more)."""
        assert CognitiveSelectionStrategy.pacing.include_activation_turn is True
        assert CognitiveSelectionStrategy.pacing.refetch_when_exhausted is True


class TestEmptySubstrate:
    def test_empty_store_degrades_rather_than_breaks(self):
        """Spec: FR-303 — the cold-start ladder. Generated families are the floor that
        keeps the persona usable against an empty store; substrate families are the
        differentiator, not the prerequisite."""
        assert _strategy(FakeMemoryRepository(), _persona()).select_items(PERSONA_ID) == []


class TestAuthoredContentIsNotThisPersonasMaterial:
    """Spec: FR-303, FR-310 — reading across personas reaches other personas' *authored*
    curriculum too, and that is not this persona's material.

    Found on the real database before the first live session: with 38 authored tutor
    concepts and 22 organic ones in the store, and no cognitive-coach state on any of
    them, every item ranked "never practised" and the authored ones sorted first on id —
    so the very first session would have opened by quizzing the user on another persona's
    vocabulary list instead of their own knowledge.
    """

    def test_authored_items_are_excluded(self):
        repo = FakeMemoryRepository()
        authored = _concept("vocabulary", 1)
        authored.origin = "authored"
        repo.concepts.append(authored)
        repo.concepts.append(_concept("hiking", 2))
        persona = _persona(settings={"exercise_families": [FAMILY_CONCEPT]})

        selected = _strategy(repo, persona).select_items(PERSONA_ID)

        assert [s.item.name for s in selected] == ["hiking"]

    def test_a_store_of_only_authored_content_selects_nothing(self):
        """Degrades to the generated families rather than reaching for someone else's
        curriculum — the honest answer when the user has produced nothing of their own
        yet."""
        repo = FakeMemoryRepository()
        authored = _concept("vocabulary", 1)
        authored.origin = "authored"
        repo.concepts.append(authored)
        persona = _persona(settings={"exercise_families": [FAMILY_CONCEPT]})

        assert _strategy(repo, persona).select_items(PERSONA_ID) == []
