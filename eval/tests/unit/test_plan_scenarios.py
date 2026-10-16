# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-3 — Scenarios are planned from the Ledger (never from a Dialog); the worthiness label
follows the facts a Scenario reveals (spec Worthiness: gates episodes only)."""
from dataclasses import replace
from datetime import date

import pytest

from mamemai_eval.domain.model import (
    EvalLanguage, FactKind, Ledger, LedgerFact, Scenario, ScenarioKind, SpeechArchetype,
)
from mamemai_eval.usecases.plan_scenarios import PlanScenarios, target_turns
from tests.fakes import FakeEvalRepository
from tests.helpers import sample_content


def plan(archetype=SpeechArchetype.HESITANT):
    c = sample_content(archetype=archetype)
    return c, PlanScenarios().execute(c.profile, c.ledger)


def test_one_disclosure_per_ledger_scenario_plus_two_fact_free_ones():
    c, scenarios = plan()
    kinds = [s.kind for s in scenarios]
    assert kinds == [ScenarioKind.DISCLOSURE] * 3 + [ScenarioKind.SMALL_TALK, ScenarioKind.ASSISTANT_OPERATION]
    assert [s.index for s in scenarios] == [0, 1, 2, 3, 4]
    assert len({s.scenario_id for s in scenarios}) == 5 and scenarios[0].scenario_id.endswith("-hesitant-s0")


def test_reveals_follow_the_ledger_schedule_and_every_fact_is_revealed_exactly_once():
    c, scenarios = plan()
    revealed = [f for s in scenarios for f in s.reveals]
    assert sorted(revealed) == sorted(f.fact_id for f in c.ledger.facts)
    assert scenarios[2].reveals == ("f02",) and scenarios[1].reveals == ("f03", "f04")
    for s in scenarios:
        s.check_against(c.ledger)  # the planner's own output satisfies the domain rules


def test_revisions_are_stated_where_the_new_value_is_revealed():
    c, scenarios = plan()
    assert scenarios[2].revisions(c.ledger) == (("f02", "f01"),)
    assert scenarios[0].revisions(c.ledger) == ()


def test_worthiness_label_follows_the_events_revealed():
    c, scenarios = plan()
    assert [s.expects_episodes(c.ledger) for s in scenarios] == [False, True, False, False, False]
    # scenario 1 tells an event (the eclipse); scenarios 0 and 2 tell only attributes → concepts, no episodes


def test_fact_free_scenarios_come_after_the_last_disclosure_with_their_own_dates_and_topics():
    c, scenarios = plan()
    last = c.ledger.scenario_dates[-1]
    assert [s.on_date for s in scenarios[3:]] == [date(2026, 7, 20), date(2026, 7, 27)]
    assert all(s.on_date > last for s in scenarios[3:])
    assert all(s.reveals == () and s.omits == () and s.topic for s in scenarios[3:])
    assert all(s.topic == "" for s in scenarios[:3])


def test_omits_are_later_facts_in_domains_the_scenario_touches():
    c = sample_content()
    ledger = c.ledger
    scenarios = PlanScenarios().execute(c.profile, ledger)
    # scenario 0 reveals f01 (domain "maison"); the only later "maison" fact, f02, is a revision of f01
    # (the old value is still true), so nothing can be withheld
    assert scenarios[0].omits == ()
    extra = LedgerFact("f05", FactKind.ATTRIBUTE, "Camille a un chat", 2, "maison", key_terms=("chat",))
    with_cat = Ledger(ledger.profile_id, ledger.facts + (extra,), ledger.scenario_dates)
    assert PlanScenarios().execute(c.profile, with_cat)[0].omits == ("f05",)
    assert scenarios[2].omits == ()  # nothing is revealed after the last disclosure
    for s in scenarios:
        assert all(ledger.get(f).revealed_in > s.index for f in s.omits)


def test_omits_never_include_future_events_or_revisions():
    c = sample_content()
    # f03 (event 2026-06-10) is revealed in scenario 1; scenario 0 is dated 2026-06-08, so f03 has not happened yet
    ledger = Ledger(c.profile.profile_id, c.ledger.facts[:1] + (
        LedgerFact("f05", FactKind.EVENT, "Camille a visité Rome", 1, "maison", date(2026, 6, 15), key_terms=("Rome",)),
        LedgerFact("f06", FactKind.ATTRIBUTE, "Camille a un chat", 1, "maison", key_terms=("chat",)),
        LedgerFact("f07", FactKind.ATTRIBUTE, "Camille a un chien", 2, "maison", supersedes="f06", key_terms=("chien",)),
        c.ledger.get("f03")), c.ledger.scenario_dates)
    s0 = PlanScenarios().execute(c.profile, ledger)[0]
    assert s0.omits == ("f06",)  # not f05 (still in the future), not f07 (a revision)
    with pytest.raises(ValueError, match="after this Scenario"):
        replace(s0, omits=("f05",)).check_against(ledger)
    with pytest.raises(ValueError, match="revision"):
        replace(s0, omits=("f07",)).check_against(ledger)


def test_the_plan_is_deterministic_and_seeded():
    from mamemai_eval.usecases.plan_scenarios import MOODS
    c = sample_content()
    a = PlanScenarios().execute(c.profile, c.ledger)
    assert a == PlanScenarios().execute(c.profile, c.ledger)
    assert all(s.mood in MOODS for s in a)
    # across many seeds the draw is not constant (a fixed mood would mean the seed is ignored)
    moods = {PlanScenarios().execute(replace(c.profile, seed=n), c.ledger)[0].mood for n in range(30)}
    assert len(moods) > 1


def test_hesitant_speakers_get_longer_dialogs_for_the_same_facts():
    assert target_turns(SpeechArchetype.HESITANT, 10) > target_turns(SpeechArchetype.FLUENT, 10)
    assert target_turns(SpeechArchetype.FLUENT, 10) == 30 and target_turns(SpeechArchetype.FLUENT, 1) == 12  # floor of 6 user turns


def test_domain_rejects_inconsistent_scenarios():
    c = sample_content()
    with pytest.raises(ValueError, match="reveals and omits nothing"):
        Scenario("x", c.profile.profile_id, 9, EvalLanguage.FR, ("f01",), kind=ScenarioKind.SMALL_TALK)
    with pytest.raises(ValueError, match="at least one"):
        Scenario("x", c.profile.profile_id, 0, EvalLanguage.FR, ())
    already_told = Scenario("x", c.profile.profile_id, 1, EvalLanguage.FR, ("f03",), omits=("f01",))
    with pytest.raises(ValueError, match="cannot be omitted"):
        already_told.check_against(c.ledger)


def test_a_ledger_with_a_hole_in_its_schedule_is_refused():
    c = sample_content()
    facts = tuple(f for f in c.ledger.facts if f.revealed_in != 1)
    with pytest.raises(ValueError, match="hole"):
        PlanScenarios().execute(c.profile, Ledger(c.profile.profile_id, facts, c.ledger.scenario_dates))


def test_scenarios_round_trip_through_the_repository_fake():
    c, scenarios = plan()
    repo = FakeEvalRepository()
    repo.save_profile_with_ledger(c.profile, c.ledger)
    repo.save_scenarios(c.profile.profile_id, scenarios)
    assert repo.get_scenarios(c.profile.profile_id) == scenarios
