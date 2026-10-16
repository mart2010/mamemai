# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Threshold Sweep arithmetic (PLAN S1.7). Pure — no I/O.

Two sweeps, both re-scoring stored similarities instead of re-embedding:
- the recall cut-off (FR-309, `MIN_RECALL_SIMILARITY`) over per-Probe results;
- the upsert merge / disambiguate thresholds (TR-602) over labelled pairs of memory texts."""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from .retrieval import GATED_K, ProbeResult, recall_at_k
from .model import ProbeKind


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def frange(start: float, stop: float, step: float) -> list[float]:
    n = round((stop - start) / step)
    return [round(start + i * step, 4) for i in range(n + 1)]


# --- recall cut-off ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CutoffPoint:
    cutoff: float
    gated_recall: float  # answerable Probes: share of expected facts surviving the cut-off in the top 5
    n_answerable: int
    abstention_correct: float  # abstention Probes with nothing at or above the cut-off
    n_abstention: int


def sweep_cutoffs(results: Sequence[ProbeResult], cutoffs: Sequence[float]) -> list[CutoffPoint]:
    answerable = [r for r in results if r.probe.kind is not ProbeKind.ABSTENTION]
    abstain = [r for r in results if r.probe.kind is ProbeKind.ABSTENTION]
    points = []
    for c in cutoffs:
        recalls = [recall_at_k([h.fact_id for h in r.hits[:GATED_K] if h.similarity >= c], r.probe.expected_fact_ids, GATED_K)
                   for r in answerable]
        ok = [not any(h.similarity >= c for h in r.hits[:GATED_K]) for r in abstain]
        points.append(CutoffPoint(
            c, sum(recalls) / len(recalls) if recalls else 0.0, len(answerable),
            sum(ok) / len(ok) if ok else 1.0, len(abstain)))
    return points


def lowest_safe_cutoff(results: Sequence[ProbeResult]) -> float | None:
    """The smallest cut-off at which every abstention Probe is still refused: just above the highest
    top-5 similarity any unanswerable question reached. None without abstention Probes."""
    tops = [h.similarity for r in results if r.probe.kind is ProbeKind.ABSTENTION for h in r.hits[:GATED_K]]
    return None if not tops else math.floor(max(tops) * 1000) / 1000 + 0.001


# --- merge / disambiguate thresholds ------------------------------------------------------------

class PairClass(StrEnum):
    SAME = "same"  # a Ledger Fact against its own paraphrase: should be merged
    REVISION = "revision"  # a Fact Revision against the fact it supersedes: related, but the value changed
    DISTINCT = "distinct"  # two different facts of the same memory type: must never be merged


@dataclass(frozen=True)
class PairSimilarity:
    pair_class: PairClass
    similarity: float
    same_domain: bool = False  # DISTINCT pairs: the hardest, a shared Life/Knowledge Domain


@dataclass(frozen=True)
class ThresholdPoint:
    merge: float
    disambiguate: float
    same_merged: float  # SAME ≥ merge: duplicates collapsed with no LLM call (good)
    same_missed: float  # SAME < disambiguate: duplicates stored twice (cost: bloat)
    distinct_merged: float  # DISTINCT ≥ merge: two facts silently fused (the dangerous error)
    distinct_to_llm: float  # DISTINCT in [disambiguate, merge): needs an LLM call, and may be fused
    revision_merged: float
    revision_to_llm: float


def _share(values: Sequence[float], predicate) -> float:
    return sum(1 for v in values if predicate(v)) / len(values) if values else 0.0


def sweep_thresholds(pairs: Sequence[PairSimilarity], merges: Sequence[float], disambiguates: Sequence[float],
                     ) -> list[ThresholdPoint]:
    by = {c: [p.similarity for p in pairs if p.pair_class is c] for c in PairClass}
    out = []
    for m in merges:
        for d in disambiguates:
            if d > m:
                continue
            out.append(ThresholdPoint(
                m, d,
                _share(by[PairClass.SAME], lambda s: s >= m), _share(by[PairClass.SAME], lambda s: s < d),
                _share(by[PairClass.DISTINCT], lambda s: s >= m), _share(by[PairClass.DISTINCT], lambda s: d <= s < m),
                _share(by[PairClass.REVISION], lambda s: s >= m), _share(by[PairClass.REVISION], lambda s: d <= s < m)))
    return out


def lowest_safe_merge(pairs: Sequence[PairSimilarity]) -> float | None:
    """Smallest merge threshold that auto-merges no DISTINCT pair."""
    d = [p.similarity for p in pairs if p.pair_class is PairClass.DISTINCT]
    return None if not d else math.floor(max(d) * 1000) / 1000 + 0.001


# --- ANN index vs exact scan ---------------------------------------------------------------------

def overlap_at_k(approx: Sequence[str], exact: Sequence[str], k: int) -> float:
    """Share of the exact top-k that the approximate index also returned (its recall)."""
    want = list(exact[:k])
    return len(set(approx[:k]) & set(want)) / len(want) if want else 1.0
