# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""PlanScenarios (S2.1): the agenda of every Dialog of a Profile, derived from its Ledger. Deterministic
and seeded — no Reference LLM — so a plan is reproducible and reviewable before any Dialog is written.

Per Profile: one DISCLOSURE Scenario per Ledger scenario (what the user reveals, which Fact Revisions they
state, which later facts they deliberately do not tell yet) plus two fact-free Scenarios — small talk and
talk about the assistant itself — the negative cases for the worthiness gate (spec Worthiness)."""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from ..domain.model import Ledger, Scenario, ScenarioKind, SpeechArchetype, SyntheticUserProfile

# user turns per revealed fact: a hesitant speaker circles, restarts and trails off, so needs more of them
USER_TURNS_PER_FACT = {SpeechArchetype.FLUENT: 1.5, SpeechArchetype.HESITANT: 2.0}
MIN_USER_TURNS = 6
MAX_OMITS = 3
MOODS = ("neutral", "cheerful", "tired", "rushed", "reflective", "chatty", "a bit worried", "relaxed")

# Writer instructions for the fact-free kinds. Phrased so that no dated personal event, plan or
# self-description can creep in: that would make the Dialog worthy and the label wrong.
FACT_FREE_TOPICS: dict[ScenarioKind, str] = {
    ScenarioKind.SMALL_TALK: (
        "Light phatic chat only: greetings, how-are-you, banter, thanks, a joke. The user states no event that "
        "happened or will happen, no plan, no fact about themselves, their family, work or home, and asks for no "
        "knowledge."),
    ScenarioKind.ASSISTANT_OPERATION: (
        "The user talks about the assistant itself: asks it to speak slower or louder, which languages it knows, "
        "how it remembers things, whether it listens all the time. The user states no event, plan, or fact about "
        "their own life."),
}
FACT_FREE_TURNS = {ScenarioKind.SMALL_TALK: 8, ScenarioKind.ASSISTANT_OPERATION: 10}
FACT_FREE_GAP_DAYS = 7  # between the last disclosure and each fact-free Scenario


def scenario_id(profile: SyntheticUserProfile, index: int) -> str:
    return f"{profile.language.value}-{profile.archetype.value}-s{index}"


def target_turns(archetype: SpeechArchetype, n_facts: int) -> int:
    """Total Turns (user + assistant): the user's share scales with the facts to convey."""
    user_turns = max(MIN_USER_TURNS, math.ceil(USER_TURNS_PER_FACT[archetype] * n_facts))
    return 2 * user_turns


class PlanScenarios:
    def execute(self, profile: SyntheticUserProfile, ledger: Ledger) -> list[Scenario]:
        if not ledger.scenario_dates:
            raise ValueError("the Ledger has no scenario dates to plan from")
        rng = random.Random(f"{profile.seed}:{profile.profile_id}:scenarios")
        scenarios: list[Scenario] = []
        for i, on_date in enumerate(ledger.scenario_dates):
            reveals = tuple(f.fact_id for f in ledger.facts if f.revealed_in == i)
            if not reveals:
                raise ValueError(f"no fact is revealed in scenario {i}; the Ledger schedule has a hole")
            scenarios.append(Scenario(
                scenario_id(profile, i), profile.profile_id, i, profile.language, reveals,
                omits=self._omits(ledger, i, on_date, reveals, rng), target_turns=target_turns(profile.archetype, len(reveals)),
                mood=rng.choice(MOODS), kind=ScenarioKind.DISCLOSURE, on_date=on_date))
        last = ledger.scenario_dates[-1]
        for n, kind in enumerate((ScenarioKind.SMALL_TALK, ScenarioKind.ASSISTANT_OPERATION), start=1):
            index = len(ledger.scenario_dates) + n - 1
            scenarios.append(Scenario(
                scenario_id(profile, index), profile.profile_id, index, profile.language, (),
                target_turns=FACT_FREE_TURNS[kind], mood=rng.choice(MOODS), kind=kind,
                on_date=last + timedelta(days=FACT_FREE_GAP_DAYS * n), topic=FACT_FREE_TOPICS[kind]))
        for s in scenarios:
            s.check_against(ledger)
        return scenarios

    @staticmethod
    def _omits(ledger: Ledger, index: int, on_date: date, reveals: tuple[str, ...], rng: random.Random,
               ) -> tuple[str, ...]:
        """Later facts in a domain this Scenario touches that are already true by `on_date`, so the topic is
        open but the answer withheld. Future events and revisions cannot be withheld: they do not exist yet."""
        domains = {ledger.get(f).domain for f in reveals}
        later = [f.fact_id for f in ledger.facts
                 if f.revealed_in > index and f.domain in domains and f.supersedes is None
                 and (f.happened_on is None or f.happened_on <= on_date)]
        return tuple(sorted(rng.sample(later, min(MAX_OMITS, len(later)))))
