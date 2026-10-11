# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-3 (Ledger consistency), EV-INV-7 (Score), EV-INV-10 (comparability)."""
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from mamemai_eval.domain.model import (
    EvalLanguage, FactKind, Ledger, LedgerFact, Probe, ProbeKind, Run, RunFingerprint, Scenario, Score, Tier,
    comparability_problem,
)

PID = uuid4()


def fact(fid, revealed_in=0, supersedes=None, text="Lives in Lyon", kind=FactKind.ATTRIBUTE, happened_on=None):
    return LedgerFact(fid, kind, text, revealed_in, "maison", happened_on, supersedes=supersedes)


def test_ledger_current_facts_excludes_superseded():
    ledger = Ledger(PID, (fact("a", 0), fact("b", 2, supersedes="a", text="Lives in Paris"), fact("c", 1)))
    assert {f.fact_id for f in ledger.current_facts()} == {"b", "c"}


@pytest.mark.parametrize("facts, msg", [
    ((fact("a"), fact("a")), "duplicate"),
    ((fact("a", 1, supersedes="zzz"),), "unknown"),
    ((fact("a", 2), fact("b", 2, supersedes="a")), "after"),
    ((fact("a", 0), fact("b", 1, supersedes="a"), fact("c", 2, supersedes="a")), "twice"),
])
def test_ledger_rejects_inconsistency(facts, msg):
    with pytest.raises(ValueError, match=msg):
        Ledger(PID, facts)


def test_event_needs_a_date_and_only_events_have_one():
    with pytest.raises(ValueError, match="happened_on"):
        fact("e", kind=FactKind.EVENT)
    with pytest.raises(ValueError, match="happened_on"):
        fact("a", happened_on=date(2026, 6, 1))
    assert FactKind.EVENT.expected_memory_type == "episode"
    assert FactKind.KNOWLEDGE.expected_memory_type == "concept"
    assert FactKind.ATTRIBUTE.expected_memory_type is None  # spec names no home — measured, not asserted


def test_only_attributes_can_be_revised():
    with pytest.raises(ValueError, match="only attributes"):
        fact("k2", 2, supersedes="k1", kind=FactKind.KNOWLEDGE)


def test_scenario_dates_constrain_events():
    dates = (date(2026, 6, 8), date(2026, 6, 22))
    ok = fact("e", 1, kind=FactKind.EVENT, happened_on=date(2026, 6, 15), text="Went hiking")
    Ledger(PID, (ok,), dates)
    with pytest.raises(ValueError, match="after the scenario"):
        Ledger(PID, (fact("e", 0, kind=FactKind.EVENT, happened_on=date(2026, 6, 15), text="Went hiking"),), dates)
    with pytest.raises(ValueError, match="no scenario date"):
        Ledger(PID, (fact("a", 2),), dates)
    with pytest.raises(ValueError, match="ascending"):
        Ledger(PID, (), (date(2026, 6, 22), date(2026, 6, 8)))


def test_probe_invariants():
    with pytest.raises(ValueError):
        Probe("p", ProbeKind.ABSTENTION, EvalLanguage.EN, "q?", expected_fact_ids=("a",))
    with pytest.raises(ValueError):
        Probe("p", ProbeKind.SINGLE_FACT, EvalLanguage.EN, "q?")
    Probe("p", ProbeKind.ABSTENTION, EvalLanguage.FR, "Quel est mon chat ?")


def test_scenario_must_follow_ledger_schedule():
    ledger = Ledger(PID, (fact("a", 0), fact("b", 1)))
    Scenario("s0", PID, 0, EvalLanguage.EN, reveals=("a",)).check_against(ledger)
    with pytest.raises(ValueError, match="scheduled"):
        Scenario("s0", PID, 0, EvalLanguage.EN, reveals=("b",)).check_against(ledger)
    with pytest.raises(ValueError, match="both"):
        Scenario("s0", PID, 0, EvalLanguage.EN, reveals=("a",), omits=("a",)).check_against(ledger)


def test_score_carries_sample_size():
    with pytest.raises(ValueError):
        Score("recall@5", 0.8, n=-1)


def _run(*, ref="claude-x", rubric=None, corpus="v1", lang=EvalLanguage.EN, suite="retrieval", comparable=True, sut=None):
    fp = RunFingerprint("abc", sut or {"embedding": "e5"}, corpus, lang, ref, rubric)
    now = datetime.now(UTC)
    return Run(uuid4(), suite, Tier.T1, fp, (Score("mrr", 0.5, 10),), now, now, comparable)


def test_sut_difference_is_comparable():
    assert comparability_problem(_run(sut={"embedding": "e5"}), _run(sut={"embedding": "bge"})) is None


@pytest.mark.parametrize("other", [
    dict(ref="claude-y"), dict(rubric="r2"), dict(corpus="v2"), dict(lang=EvalLanguage.FR), dict(suite="consolidation"),
    dict(comparable=False),
])
def test_instrument_difference_is_refused(other):
    assert comparability_problem(_run(), _run(**other)) is not None


def test_fingerprint_digest_is_stable_and_sensitive():
    a, b = _run().fingerprint, _run().fingerprint
    assert a.digest() == b.digest()
    assert a.digest() != _run(sut={"embedding": "bge"}).fingerprint.digest()


def test_key_terms_must_occur_in_the_fact_text():
    LedgerFact("a", FactKind.ATTRIBUTE, "Habite à Quimper", 0, "maison", key_terms=("quimper",))  # case-insensitive
    with pytest.raises(ValueError, match="key_terms"):
        LedgerFact("a", FactKind.ATTRIBUTE, "Habite à Quimper", 0, "maison", key_terms=("Brest",))
