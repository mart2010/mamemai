# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Threshold Sweep use cases (S1.7): the recall cut-off over retrieval outcomes, and the upsert
merge / disambiguate thresholds over labelled pairs. Both only read similarities — they re-score,
they never change a product constant (a Verdict goes through the spec loop, DESIGN §Spec relationship)."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ..domain.model import EvalLanguage, Ledger, SyntheticUserProfile
from ..domain.sweep import PairClass, PairSimilarity, cosine
from .retrieval import MemoryRecord, Rendering, SutFactory, render_fact


@dataclass(frozen=True)
class PairPlan:
    pair_class: PairClass
    left: MemoryRecord
    right: MemoryRecord
    same_domain: bool = False


def plan_pairs(profile: SyntheticUserProfile, ledger: Ledger, rendering: Rendering) -> list[PairPlan]:
    """Labelled pairs of texts as the upsert path would compare them (TR-601: candidate vs stored item).

    SAME: a fact against each of its 2 paraphrases (same name, as an extractor naming the same thing would).
    REVISION: a fact against the fact it supersedes.
    DISTINCT: every other pair of facts that share a memory type (episodes are compared with episodes,
    concepts with concepts, as the search is scoped), excluding revision pairs."""
    lang: EvalLanguage = profile.language
    base = {f.fact_id: render_fact(lang, f, f.text, rendering) for f in ledger.facts}
    plans: list[PairPlan] = []
    for f in ledger.facts:
        for para in f.paraphrases:
            plans.append(PairPlan(PairClass.SAME, base[f.fact_id], render_fact(lang, f, para, rendering)))
    revision = {frozenset((f.fact_id, f.supersedes)) for f in ledger.facts if f.supersedes}
    for f in ledger.facts:
        if f.supersedes:
            plans.append(PairPlan(PairClass.REVISION, base[f.supersedes], base[f.fact_id]))
    for a, b in combinations(ledger.facts, 2):
        if base[a.fact_id].memory_type != base[b.fact_id].memory_type or frozenset((a.fact_id, b.fact_id)) in revision:
            continue
        plans.append(PairPlan(PairClass.DISTINCT, base[a.fact_id], base[b.fact_id], a.domain == b.domain))
    return plans


class MeasurePairSimilarities:
    """Embeds every distinct text once and returns the labelled cosine similarities."""

    def __init__(self, sut_factory: SutFactory) -> None:
        self._sut_factory = sut_factory

    def execute(self, profile: SyntheticUserProfile, ledger: Ledger, rendering: Rendering) -> list[PairSimilarity]:
        plans = plan_pairs(profile, ledger, rendering)
        unique: dict[tuple, MemoryRecord] = {}
        for p in plans:
            for r in (p.left, p.right):
                unique.setdefault(_key(r), r)
        keys = list(unique)
        with self._sut_factory() as sut:
            vectors = dict(zip(keys, sut.embed_passages([unique[k] for k in keys])))
        return [PairSimilarity(p.pair_class, cosine(vectors[_key(p.left)], vectors[_key(p.right)]), p.same_domain)
                for p in plans]


def _key(r: MemoryRecord) -> tuple:
    return (r.memory_type, r.name if r.name_in_embedding else None, r.text)

