# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-3 — Probes are derived from the Ledger; answers never leak into queries;
abstention questions are independently verified unanswerable."""
import json
from datetime import date
from uuid import uuid4

import pytest

from mamemai_eval.domain.model import (
    EvalLanguage, FactKind, Ledger, LedgerFact, ProbeKind, SpeechArchetype, SyntheticUserProfile,
)
from mamemai_eval.usecases.generate_probes import (
    ABSTAIN_CANDIDATES, N_ABSTAIN, N_AGG, N_CROSS, N_HARD, N_SINGLE, GenerateProbes, plan,
)
from mamemai_eval.usecases.reference_json import ReferenceJsonError
from tests.fakes import FakeReferenceLLM

PID = uuid4()
DOMAINS = ("maison", "travail", "loisirs")


def build_ledger():
    """60 facts: attributes + events + knowledge, 5 revisions among attributes."""
    facts = []
    for i in range(60):
        scenario = i // 10
        n = f"{i:02d}"
        if i % 3 == 0:
            facts.append(LedgerFact(f"f{n}", FactKind.EVENT, f"Camille a visité le lieu{n} hier", scenario, DOMAINS[i % 3],
                                    date(2026, 6, 1), key_terms=(f"lieu{n}",)))
        elif i % 3 == 1:
            facts.append(LedgerFact(f"f{n}", FactKind.ATTRIBUTE, f"Camille possède l'objet{n}", scenario, DOMAINS[i % 2],
                                    key_terms=(f"objet{n}",)))
        else:
            facts.append(LedgerFact(f"f{n}", FactKind.KNOWLEDGE, f"Camille a appris la notion{n}", scenario, "astronomie",
                                    key_terms=(f"notion{n}",)))
    attrs = [f for f in facts if f.kind is FactKind.ATTRIBUTE]
    for k in range(5):  # attribute k (scenario <= 1) is replaced by a later attribute
        old, new = attrs[k], attrs[-1 - k]
        facts[facts.index(new)] = LedgerFact(new.fact_id, new.kind, new.text, new.revealed_in, new.domain,
                                             key_terms=new.key_terms, supersedes=old.fact_id)
    return Ledger(PID, tuple(facts), tuple(date(2026, 6 + s, 8) for s in range(6)))


LEDGER = build_ledger()
PROFILE = SyntheticUserProfile(PID, EvalLanguage.FR, "Camille", 1, "bio", SpeechArchetype.HESITANT, "style")


def scripted_llm(*, leak=False, answerable=()):
    """Replies in the order GenerateProbes asks: written queries, agg+abstention, verification, translations."""
    items, cross_ids = plan(LEDGER, "fr-hesitant", 1)
    queries = [{"id": it.probe_id, "query": f"Question {it.probe_id}" + (f" {it.target.key_terms[0]}" if leak else "")}
               for it in items]
    current = sorted(f.fact_id for f in LEDGER.current_facts())
    cands = [f"Que sais-tu de mon vol numéro {i} ?" for i in range(ABSTAIN_CANDIDATES)]
    b = {"aggregation": [{"query": f"Agrégation {i}", "fact_ids": current[i:i + 2]} for i in range(N_AGG)],
         "abstention": [{"query": q} for q in cands]}
    checks = {"checks": [{"id": str(i), "fact_ids": ["f01"] if i in answerable else []} for i in range(len(cands))]}
    trans = {"translations": [{"id": pid, "query": f"Translated {pid}"} for pid in cross_ids]}
    return FakeReferenceLLM([json.dumps(x) for x in ({"queries": queries}, b, checks, trans)])


def test_plan_is_deterministic_and_covers_every_kind():
    items, cross = plan(LEDGER, "p", 1)
    assert items == plan(LEDGER, "p", 1)[0] and items != plan(LEDGER, "p", 2)[0]
    kinds = [it.kind for it in items]
    assert kinds.count(ProbeKind.SINGLE_FACT) == N_SINGLE and kinds.count(ProbeKind.HARD_NEGATIVE) == N_HARD
    assert kinds.count(ProbeKind.UPDATE) == 5 and len(cross) == N_CROSS
    for it in items:
        if it.kind is ProbeKind.UPDATE:
            assert it.target.supersedes and it.distractors[0].fact_id == it.target.supersedes
        if it.kind is ProbeKind.HARD_NEGATIVE:
            d = it.distractors[0]
            assert (d.domain, d.kind) == (it.target.domain, it.target.kind) and d.fact_id != it.target.fact_id


def test_full_generation_yields_every_kind_with_expected_ids():
    probes = GenerateProbes(scripted_llm(), "m").execute(PROFILE, LEDGER)
    by_kind = {}
    for p in probes:
        by_kind.setdefault(p.kind, []).append(p)
    assert {k: len(v) for k, v in by_kind.items()} == {
        ProbeKind.SINGLE_FACT: N_SINGLE, ProbeKind.HARD_NEGATIVE: N_HARD, ProbeKind.UPDATE: 5, ProbeKind.AGGREGATION: N_AGG,
        ProbeKind.ABSTENTION: N_ABSTAIN, ProbeKind.CROSS_LINGUAL: N_CROSS}
    assert all(p.language is EvalLanguage.EN for p in by_kind[ProbeKind.CROSS_LINGUAL])  # profile is fr → cross is en
    assert all(not p.expected_fact_ids for p in by_kind[ProbeKind.ABSTENTION])
    assert len({p.probe_id for p in probes}) == len(probes)


def test_answers_leaking_into_queries_are_rejected_after_repair_attempts():
    first = scripted_llm(leak=True)._replies[0]
    llm = FakeReferenceLLM([first] * 3)  # the model keeps leaking on every repair round
    with pytest.raises(ReferenceJsonError, match="answer words"):
        GenerateProbes(llm, "m").execute(PROFILE, LEDGER)
    assert "REJECTED" in llm.calls[1].prompt and len(llm.calls) == 3


def test_answerable_abstention_candidates_are_dropped_and_missing_ones_requested_again():
    answerable = set(range(ABSTAIN_CANDIDATES - N_ABSTAIN + 3))  # leaves fewer than N_ABSTAIN survivors
    llm = scripted_llm(answerable=answerable)
    with pytest.raises(ReferenceJsonError):  # scripted replies run out: the round-2 request has no scripted answer
        GenerateProbes(llm, "m").execute(PROFILE, LEDGER)
    assert any("were answerable and rejected" in c.prompt for c in llm.calls)


def test_verified_abstention_survivors_exclude_the_answerable_ones():
    probes = GenerateProbes(scripted_llm(answerable={0, 1}), "m").execute(PROFILE, LEDGER)
    abst = [p.text for p in probes if p.kind is ProbeKind.ABSTENTION]
    assert len(abst) == N_ABSTAIN and "Que sais-tu de mon vol numéro 0 ?" not in abst


def test_hesitant_fillers_are_the_profiles_own_language():
    from mamemai_eval.usecases.generate_probes import _style
    en = SyntheticUserProfile(PID, EvalLanguage.EN, "Hannah", 1, "bio", SpeechArchetype.HESITANT, "style")
    assert "'euh'" in _style(PROFILE) and "'um'" in _style(en) and "'euh'" not in _style(en)
