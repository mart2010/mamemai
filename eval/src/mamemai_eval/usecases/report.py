# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Plain-text rendering of Runs. Presentation only; the numbers come from the Suite."""
from __future__ import annotations

import statistics
from collections.abc import Sequence

from ..domain.model import Run
from ..domain.sweep import CutoffPoint, PairClass, PairSimilarity, ThresholdPoint, quantile


def format_run(run: Run, repeat: str = "") -> str:
    fp = run.fingerprint
    head = (f"== {run.suite} · {fp.language.value} · corpus {fp.corpus_version} · git {fp.git_sha} {repeat}\n"
            f"   sut: {dict(fp.sut)}  {dict(fp.extra)}\n   run {run.run_id}")
    width = max(len(s.metric) for s in run.scores)
    rows = [f"   {s.metric:<{width}}  {s.value:7.3f}  n={s.n}" for s in run.scores]
    return "\n".join([head, *rows])


def format_noise_floor(runs: Sequence[Run]) -> str:
    """Spread of each Score over repeated Runs of the same configuration (EV-INV-7): the smallest
    difference a later comparison may call real."""
    metrics = [s.metric for s in runs[0].scores]
    lines = [f"-- noise floor over {len(runs)} repeats"]
    worst = 0.0
    for m in metrics:
        vals = [next(s.value for s in r.scores if s.metric == m) for r in runs]
        spread = max(vals) - min(vals)
        worst = max(worst, spread)
        if spread:
            lines.append(f"   {m}: min {min(vals):.4f} max {max(vals):.4f} sd {statistics.pstdev(vals):.4f}")
    if worst == 0.0:
        lines.append("   every Score identical across repeats (deterministic)")
    return "\n".join(lines)


def format_cutoff_sweep(points: Sequence[CutoffPoint], safe: float | None, production: float) -> str:
    n_a, n_x = points[0].n_answerable, points[0].n_abstention
    lines = [f"   recall cut-off   gated_recall@5 (n={n_a})   abstention_correct (n={n_x})"]
    for p in points:
        tag = "  ← production" if abs(p.cutoff - production) < 1e-9 else ""
        lines.append(f"   {p.cutoff:>13.2f}   {p.gated_recall:>20.3f}   {p.abstention_correct:>24.3f}{tag}")
    if safe is not None:
        at = min(points, key=lambda p: abs(p.cutoff - safe))
        lines.append(f"   lowest cut-off refusing every abstention probe: {safe:.3f} "
                     f"(gated recall at {at.cutoff:.2f}: {at.gated_recall:.3f})")
    return "\n".join(lines)


def format_pair_distributions(pairs: Sequence[PairSimilarity]) -> str:
    lines = ["   pair class                n     min    p10    p50    p90    max"]
    groups = [("same (paraphrase)", [p for p in pairs if p.pair_class is PairClass.SAME]),
              ("revision (old↔new)", [p for p in pairs if p.pair_class is PairClass.REVISION]),
              ("distinct, same domain", [p for p in pairs if p.pair_class is PairClass.DISTINCT and p.same_domain]),
              ("distinct, any", [p for p in pairs if p.pair_class is PairClass.DISTINCT])]
    for label, members in groups:
        if not members:
            continue
        v = [p.similarity for p in members]
        lines.append(f"   {label:<22}{len(v):>6}  {min(v):.3f}  {quantile(v, .1):.3f}  {quantile(v, .5):.3f}  "
                     f"{quantile(v, .9):.3f}  {max(v):.3f}")
    return "\n".join(lines)


def format_threshold_sweep(points: Sequence[ThresholdPoint], safe_merge: float | None, production: tuple[float, float]) -> str:
    lines = ["   merge  disamb | same merged  same missed | distinct MERGED  distinct→LLM | revision merged  revision→LLM"]
    for p in points:
        tag = "  ← production" if (p.merge, p.disambiguate) == production else ""
        lines.append(f"   {p.merge:.2f}   {p.disambiguate:.2f}  | {p.same_merged:>11.3f}  {p.same_missed:>11.3f} | "
                     f"{p.distinct_merged:>15.3f}  {p.distinct_to_llm:>12.3f} | {p.revision_merged:>15.3f}  {p.revision_to_llm:>12.3f}{tag}")
    if safe_merge is not None:
        lines.append(f"   lowest merge threshold fusing no distinct pair: {safe_merge:.3f}")
    return "\n".join(lines)
