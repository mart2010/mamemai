# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""S1.8: how much of the exact top-k does the HNSW index return? One shared store per Eval Language (both
Profiles, each fact stored as itself *and* as its two paraphrases — near-duplicates are the hard case for
an approximate index), queried with every Probe twice: through the index and by brute force.

`ef_search` is a positive control: at 1 the index must visibly miss, proving the check can see misses."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from ..domain.model import Ledger, Probe, Run, RunFingerprint, Score, SyntheticUserProfile, Tier
from ..domain.sweep import overlap_at_k
from .retrieval import (
    MemoryRecord, Rendering, RetrievalQuery, SutFactory, current_git_sha, render_fact)

SUITE = "ann_vs_exact"
DEPTH = 10


def build_store(profiles: Sequence[tuple[SyntheticUserProfile, Ledger, Sequence[Probe]]],
                rendering: Rendering = Rendering.FIRST_KEY_TERM) -> list[MemoryRecord]:
    """Fact ids are made unique across Profiles ("<profile>:<fact>[:p1|p2]"); only identity matters here."""
    records: list[MemoryRecord] = []
    for profile, ledger, _ in profiles:
        tag = profile.profile_id.hex[:6]
        for f in ledger.facts:
            for suffix, text in (("", f.text), (":p1", f.paraphrases[0]), (":p2", f.paraphrases[1])):
                r = render_fact(profile.language, f, text, rendering)
                records.append(MemoryRecord(f"{tag}:{f.fact_id}{suffix}", r.memory_type, r.text, r.name, r.happened_on,
                                           r.language, r.name_in_embedding))
    return records


class MeasureAnnRecall:
    def __init__(self, sut_factory: SutFactory, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._sut_factory = sut_factory
        self._clock = clock

    def execute(self, profiles: Sequence[tuple[SyntheticUserProfile, Ledger, Sequence[Probe]]], corpus_version: str,
                git_sha: str | None = None, ef_search: int | None = None) -> Run:
        languages = {p.language for p, _, _ in profiles}
        if len(languages) != 1:
            raise ValueError(f"one Eval Language per Run (EV-INV-7), got {sorted(languages)}")
        started = self._clock()
        records = build_store(profiles)
        queries = [RetrievalQuery(p.probe_id, p.text) for _, _, probes in profiles for p in probes]
        with self._sut_factory() as sut:
            sut.index(records)
            approx = sut.search(queries, DEPTH, path="index", ef_search=ef_search)
            exact = sut.search(queries, DEPTH, path="exact")
            description, ann_plan, exact_plan = sut.describe(), sut.plan("index"), sut.plan("exact")
        if "seq scan" not in exact_plan.lower():
            raise RuntimeError(f"the 'exact' path was not a sequential scan, so it proves nothing: {exact_plan}")
        if "index scan" not in ann_plan.lower() and "hnsw" not in ann_plan.lower():
            raise RuntimeError(f"the 'approximate' path did not use the vector index, so the comparison is vacuous: {ann_plan}")
        n = len(queries)
        ids = lambda hits: [h.fact_id for h in hits]  # noqa: E731
        scores = (
            Score(f"ann_overlap@{DEPTH}", sum(overlap_at_k(ids(a), ids(e), DEPTH) for a, e in zip(approx, exact)) / n, n),
            Score("ann_overlap@5", sum(overlap_at_k(ids(a), ids(e), 5) for a, e in zip(approx, exact)) / n, n),
            Score("ann_top1_agrees", sum(ids(a)[:1] == ids(e)[:1] for a, e in zip(approx, exact)) / n, n),
            Score("queries_with_any_miss", sum(overlap_at_k(ids(a), ids(e), DEPTH) < 1 for a, e in zip(approx, exact)) / n, n),
            Score("store_items", float(len(records)), len(records), unit="items"),
        )
        fp = RunFingerprint(
            git_sha=git_sha or current_git_sha(), sut=dict(description.facts), corpus_version=corpus_version,
            language=profiles[0][0].language,
            extra={"ef_search": str(ef_search or "default"), "ann_plan": ann_plan, "exact_plan": exact_plan,
                   "store": "facts+2 paraphrases", "rendering": Rendering.FIRST_KEY_TERM.value})
        return Run(uuid4(), SUITE, Tier.T0, fp, scores, started, self._clock())
