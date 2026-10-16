# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Retrieval Suite measuring: ranking metrics and the scoring of per-Probe results. Pure — no I/O.

A Probe's outcome is its ranked list of `Hit`s (fact id + cosine similarity, best first). Scoring
is a pure function of those hits, the Probes and a similarity cut-off, so a Threshold Sweep
(S1.7) re-scores stored outcomes without re-embedding anything."""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .model import Probe, ProbeKind, Score

RECALL_KS = (1, 3, 5, 10)
NDCG_K = 10
GATED_K = 5  # production recall asks the store for the top 5 (TR-314)


@dataclass(frozen=True)
class Hit:
    fact_id: str
    similarity: float  # 1 − cosine distance (TR-509)


@dataclass(frozen=True)
class ProbeResult:
    probe: Probe
    hits: tuple[Hit, ...]  # best first

    def __post_init__(self) -> None:
        sims = [h.similarity for h in self.hits]
        if sims != sorted(sims, reverse=True):
            raise ValueError(f"hits of {self.probe.probe_id} are not sorted best-first")

    @property
    def ranked_ids(self) -> list[str]:
        return [h.fact_id for h in self.hits]

    @property
    def top_similarity(self) -> float | None:
        return self.hits[0].similarity if self.hits else None


def recall_at_k(ranked: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Fraction of the relevant facts found in the top k."""
    return len(set(ranked[:k]) & set(relevant)) / len(set(relevant))


def reciprocal_rank(ranked: Sequence[str], relevant: Sequence[str]) -> float:
    rel = set(relevant)
    for i, fid in enumerate(ranked, start=1):
        if fid in rel:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Binary-relevance nDCG."""
    rel = set(relevant)
    dcg = sum(1.0 / math.log2(i + 1) for i, fid in enumerate(ranked[:k], start=1) if fid in rel)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(rel), k) + 1))
    return dcg / ideal


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _ranking_scores(results: Sequence[ProbeResult], suffix: str) -> list[Score]:
    n = len(results)
    scores = [
        Score(f"recall@{k}{suffix}", _mean([recall_at_k(r.ranked_ids, r.probe.expected_fact_ids, k) for r in results]), n)
        for k in RECALL_KS
    ]
    scores.append(Score(f"mrr{suffix}", _mean([reciprocal_rank(r.ranked_ids, r.probe.expected_fact_ids) for r in results]), n))
    scores.append(Score(
        f"ndcg@{NDCG_K}{suffix}", _mean([ndcg_at_k(r.ranked_ids, r.probe.expected_fact_ids, NDCG_K) for r in results]), n))
    return scores


def _passes_gate(result: ProbeResult, cutoff: float) -> list[Hit]:
    """What production recall would hand the assistant: the top GATED_K rows at or above the cut-off."""
    return [h for h in result.hits[:GATED_K] if h.similarity >= cutoff]


def update_prefers_current(result: ProbeResult) -> bool:
    """An update Probe expects the current fact; its `hard_negative_fact_ids` are the outdated ones.
    Success = the current fact is retrieved and ranks above every outdated one that was retrieved."""
    ranked = result.ranked_ids
    current = [ranked.index(f) for f in result.probe.expected_fact_ids if f in ranked]
    if not current:
        return False
    stale = [ranked.index(f) for f in result.probe.hard_negative_fact_ids if f in ranked]
    return not stale or min(current) < min(stale)


def score_retrieval(results: Sequence[ProbeResult], cutoff: float) -> tuple[Score, ...]:
    """All Scores of the `retrieval` Suite for one Eval Language.

    Ranking metrics are overall (answerable Probes) and per Probe kind. Gate metrics apply the
    production similarity cut-off: `gated_recall@5` over answerable Probes (a relevant fact survives
    the gate) and `abstention_correct` over abstention Probes (nothing survives — the failure the
    gate exists to prevent is a false recall, FR-309)."""
    answerable = [r for r in results if r.probe.kind is not ProbeKind.ABSTENTION]
    abstain = [r for r in results if r.probe.kind is ProbeKind.ABSTENTION]
    scores: list[Score] = []
    if answerable:
        scores += _ranking_scores(answerable, "")
        scores.append(Score(
            "gated_recall@5",
            _mean([recall_at_k([h.fact_id for h in _passes_gate(r, cutoff)], r.probe.expected_fact_ids, GATED_K)
                   for r in answerable]), len(answerable)))
    for kind in ProbeKind:
        if kind is ProbeKind.ABSTENTION:
            continue
        of_kind = [r for r in answerable if r.probe.kind is kind]
        if of_kind:
            scores += _ranking_scores(of_kind, f"/{kind.value}")
    hard = [r for r in answerable if r.probe.kind is ProbeKind.HARD_NEGATIVE]
    if hard:
        confused = [bool(r.hits) and r.hits[0].fact_id in r.probe.hard_negative_fact_ids for r in hard]
        scores.append(Score("hard_negative_top1_confusion", _mean([float(c) for c in confused]), len(hard)))
    upd = [r for r in answerable if r.probe.kind is ProbeKind.UPDATE]
    if upd:
        scores.append(Score("update_current_first", _mean([float(update_prefers_current(r)) for r in upd]), len(upd)))
    if abstain:
        scores.append(Score(
            "abstention_correct", _mean([float(not _passes_gate(r, cutoff)) for r in abstain]), len(abstain)))
        scores.append(Score(
            "abstention_top1_similarity", _mean([r.top_similarity or 0.0 for r in abstain]), len(abstain), unit="cosine"))
    return tuple(scores)
