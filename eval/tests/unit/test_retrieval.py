# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-6/7 — the `retrieval` Suite scores a SUT through its port: ranking metrics, the
production gate (FR-309, TR-314), Scores that carry n, and a Run Fingerprint."""
import math

import pytest

from mamemai_eval.domain.model import EvalLanguage, ProbeKind
from mamemai_eval.domain.retrieval import Hit, ProbeResult, ndcg_at_k, recall_at_k, reciprocal_rank, score_retrieval
from mamemai_eval.usecases.retrieval import ExecuteRetrievalSuite, render_memory_records
from tests.fakes import FakeRetrievalSut
from tests.helpers import sample_content


def test_recall_mrr_ndcg_on_known_rankings():
    ranked = ["a", "b", "c", "d"]
    assert recall_at_k(ranked, ["c"], 1) == 0.0
    assert recall_at_k(ranked, ["c"], 3) == 1.0
    assert recall_at_k(ranked, ["a", "d"], 3) == 0.5  # aggregation: fraction of expected facts found
    assert reciprocal_rank(ranked, ["c", "d"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(ranked, ["zzz"]) == 0.0
    assert ndcg_at_k(ranked, ["a"], 10) == 1.0
    assert ndcg_at_k(ranked, ["b"], 10) == pytest.approx(1 / math.log2(3))


def test_hits_must_be_sorted_best_first():
    content = sample_content()
    with pytest.raises(ValueError):
        ProbeResult(content.probes[0], (Hit("f03", 0.5), Hit("f04", 0.9)))


def test_event_becomes_episode_and_others_concepts_named_by_first_key_term():
    c = sample_content()
    by_id = {r.fact_id: r for r in render_memory_records(c.profile, c.ledger)}
    assert by_id["f03"].memory_type == "episode" and by_id["f03"].name is None and by_id["f03"].happened_on
    assert by_id["f04"].memory_type == "concept" and by_id["f04"].name == "Jupiter"
    assert by_id["f01"].memory_type == "concept" and by_id["f01"].name == "Lyon"


def _results(c, hits_by_probe):
    return [ProbeResult(p, tuple(hits_by_probe[p.probe_id])) for p in c.probes]


def test_gate_scores_abstention_and_answerable_at_the_cutoff():
    c = sample_content()
    hits = {
        "p-single": [Hit("f03", 0.90)],                       # found, passes the gate
        "p-hard": [Hit("f03", 0.86), Hit("f04", 0.85)],       # confused: top-1 is the hard negative
        "p-update": [Hit("f01", 0.88), Hit("f02", 0.87)],     # stale ranks above current
        "p-agg": [Hit("f01", 0.9), Hit("f02", 0.8)],          # f02 below the cut-off
        "p-abst": [Hit("f04", 0.80)],                         # below cut-off → correct abstention
        "p-cross": [Hit("f03", 0.70)],                        # relevant but under the cut-off
    }
    s = {x.metric: x for x in score_retrieval(_results(c, hits), 0.84)}
    assert s["recall@1"].n == 5  # abstention excluded
    assert s["abstention_correct"].value == 1.0 and s["abstention_correct"].n == 1
    assert s["hard_negative_top1_confusion"].value == 1.0
    assert s["update_current_first"].value == 0.0
    assert s["recall@1/single_fact"].value == 1.0
    assert s["gated_recall@5"].value == pytest.approx((1 + 1 + 1 + 0.5 + 0) / 5)
    # looser cut-off flips the abstention verdict — the basis of the Threshold Sweep
    loose = {x.metric: x for x in score_retrieval(_results(c, hits), 0.75)}
    assert loose["abstention_correct"].value == 0.0


def test_run_pools_profiles_of_one_language_and_closes_the_sut():
    c = sample_content(EvalLanguage.FR)
    suts: list[FakeRetrievalSut] = []

    def factory():
        suts.append(FakeRetrievalSut())
        return suts[-1]

    run, outcomes = ExecuteRetrievalSuite(factory).run(
        language=EvalLanguage.FR, corpus_version="v1", profiles=[(c.profile, c.ledger, c.probes)], git_sha="abc")
    assert suts[0].closed and len(suts[0].records) == 4
    assert run.suite == "retrieval" and run.fingerprint.corpus_version == "v1"
    assert run.fingerprint.sut["embedding"] == "fake-jaccard" and run.fingerprint.extra["rendering"]
    assert len(outcomes[0].results) == len(c.probes)
    assert all(s.n > 0 for s in run.scores)  # EV-INV-7: every Score carries n


def test_a_run_refuses_a_profile_of_another_language():
    c = sample_content(EvalLanguage.EN)
    with pytest.raises(ValueError):
        ExecuteRetrievalSuite(FakeRetrievalSut).run(
            language=EvalLanguage.FR, corpus_version="v1", profiles=[(c.profile, c.ledger, c.probes)], git_sha="x")


def test_probe_citing_an_unknown_fact_is_rejected_before_any_indexing():
    from dataclasses import replace
    c = sample_content()
    bad = replace(c.probes[0], expected_fact_ids=("f99",))
    with pytest.raises(ValueError, match="f99"):
        ExecuteRetrievalSuite(FakeRetrievalSut).outcome_for_profile(c.profile, c.ledger, [bad])


# --- S1.7 sweeps --------------------------------------------------------------------------------

from mamemai_eval.domain.sweep import (  # noqa: E402
    PairClass, PairSimilarity, cosine, frange, lowest_safe_cutoff, lowest_safe_merge, quantile, sweep_cutoffs,
    sweep_thresholds,
)
from mamemai_eval.usecases.retrieval import Rendering  # noqa: E402
from mamemai_eval.usecases.sweep import MeasurePairSimilarities, plan_pairs  # noqa: E402


def test_cutoff_sweep_trades_gated_recall_against_abstention():
    c = sample_content()
    hits = {
        "p-single": [Hit("f03", 0.90)], "p-hard": [Hit("f04", 0.86)], "p-update": [Hit("f02", 0.80)],
        "p-agg": [Hit("f01", 0.9), Hit("f02", 0.8)], "p-abst": [Hit("f04", 0.82)], "p-cross": [Hit("f03", 0.70)],
    }
    results = _results(c, hits)
    pts = {p.cutoff: p for p in sweep_cutoffs(results, [0.7, 0.81, 0.84, 0.95])}
    assert pts[0.7].gated_recall == 1.0 and pts[0.7].abstention_correct == 0.0  # everything passes, even the unanswerable
    assert pts[0.84].abstention_correct == 1.0 and pts[0.84].gated_recall < pts[0.7].gated_recall
    assert pts[0.95].gated_recall == 0.0
    assert pts[0.7].n_answerable == 5 and pts[0.7].n_abstention == 1
    assert lowest_safe_cutoff(results) == pytest.approx(0.821)  # just above the unanswerable question's 0.82


def test_quantile_cosine_and_frange():
    assert quantile([1, 2, 3, 4, 5], 0.5) == 3 and quantile([0.0, 1.0], 0.25) == 0.25
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0) and cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert frange(0.7, 0.8, 0.05) == [0.7, 0.75, 0.8]


def test_threshold_sweep_counts_each_pair_class_separately():
    pairs = [PairSimilarity(PairClass.SAME, 0.97), PairSimilarity(PairClass.SAME, 0.80),
             PairSimilarity(PairClass.DISTINCT, 0.94), PairSimilarity(PairClass.DISTINCT, 0.78),
             PairSimilarity(PairClass.DISTINCT, 0.60), PairSimilarity(PairClass.REVISION, 0.92)]
    p = sweep_thresholds(pairs, [0.93], [0.75])[0]
    assert p.same_merged == 0.5 and p.same_missed == 0.0
    assert p.distinct_merged == pytest.approx(1 / 3) and p.distinct_to_llm == pytest.approx(1 / 3)
    assert p.revision_merged == 0.0 and p.revision_to_llm == 1.0
    assert sweep_thresholds(pairs, [0.70], [0.75]) == []  # disambiguate above merge is meaningless
    assert lowest_safe_merge(pairs) == pytest.approx(0.941)


def test_pair_plan_labels_same_revision_and_distinct_without_cross_type_or_revision_pairs():
    c = sample_content()
    plans = plan_pairs(c.profile, c.ledger, Rendering.FIRST_KEY_TERM)
    by = {k: [p for p in plans if p.pair_class is k] for k in PairClass}
    assert len(by[PairClass.SAME]) == 0  # sample ledger has no paraphrases
    assert [(p.left.fact_id, p.right.fact_id) for p in by[PairClass.REVISION]] == [("f01", "f02")]
    ids = {frozenset((p.left.fact_id, p.right.fact_id)) for p in by[PairClass.DISTINCT]}
    assert frozenset(("f01", "f02")) not in ids  # revision pair is not also "distinct"
    assert frozenset(("f03", "f04")) not in ids  # episode vs concept are never compared
    assert frozenset(("f01", "f04")) in ids


def test_bare_rendering_embeds_the_description_without_its_name():
    c = sample_content()
    named = {p.left.fact_id: p.left for p in plan_pairs(c.profile, c.ledger, Rendering.FIRST_KEY_TERM)}
    bare = {p.left.fact_id: p.left for p in plan_pairs(c.profile, c.ledger, Rendering.BARE)}
    assert named["f01"].name_in_embedding and not bare["f01"].name_in_embedding
    assert bare["f01"].name == "Lyon"  # still stored with a name; only the embedded string differs


def test_pair_similarities_use_one_embedding_per_distinct_text():
    from dataclasses import replace
    c = sample_content()
    facts = tuple(replace(f, paraphrases=(f.text + " vraiment", f.text + " aussi")) for f in c.ledger.facts)
    ledger = replace(c.ledger, facts=facts)
    pairs = MeasurePairSimilarities(FakeRetrievalSut).execute(c.profile, ledger, Rendering.FIRST_KEY_TERM)
    classes = [p.pair_class for p in pairs]
    assert classes.count(PairClass.SAME) == 8 and classes.count(PairClass.REVISION) == 1
    same = [p.similarity for p in pairs if p.pair_class is PairClass.SAME]
    distinct = [p.similarity for p in pairs if p.pair_class is PairClass.DISTINCT]
    assert min(same) > max(distinct)  # a paraphrase is closer than another fact


# --- S1.8 ANN vs exact --------------------------------------------------------------------------

from mamemai_eval.domain.sweep import overlap_at_k  # noqa: E402
from mamemai_eval.usecases.ann_check import MeasureAnnRecall, build_store  # noqa: E402


def test_overlap_is_the_share_of_the_exact_top_k_returned():
    assert overlap_at_k(["a", "b", "c"], ["a", "b", "c"], 3) == 1.0
    assert overlap_at_k(["a", "x", "c"], ["a", "b", "c"], 3) == pytest.approx(2 / 3)
    assert overlap_at_k(["b", "a"], ["a", "b", "c"], 2) == 1.0  # order inside the top-k does not matter


def test_store_holds_each_fact_and_its_two_paraphrases_with_unique_ids():
    from dataclasses import replace
    c = sample_content()
    ledger = replace(c.ledger, facts=tuple(replace(f, paraphrases=("p one " + f.text, "p two " + f.text)) for f in c.ledger.facts))
    store = build_store([(c.profile, ledger, c.probes)])
    assert len(store) == 12 and len({r.fact_id for r in store}) == 12
    assert any(r.fact_id.endswith(":p2") for r in store)


def test_ann_check_scores_agreement_and_refuses_when_exact_is_not_a_scan():
    from dataclasses import replace
    c = sample_content()
    ledger = replace(c.ledger, facts=tuple(replace(f, paraphrases=("p1 " + f.text, "p2 " + f.text)) for f in c.ledger.facts))
    run = MeasureAnnRecall(FakeRetrievalSut).execute([(c.profile, ledger, c.probes)], "v1", git_sha="x")
    s = {x.metric: x for x in run.scores}
    assert s["ann_overlap@10"].value == 1.0 and s["ann_top1_agrees"].value == 1.0  # the Fake's index is exact
    assert s["queries_with_any_miss"].value == 0.0 and s["store_items"].value == 12
    assert run.suite == "ann_vs_exact" and run.fingerprint.extra["exact_plan"] == "Seq Scan"

    class IndexedExact(FakeRetrievalSut):
        def plan(self, path):
            return "Index Scan using hnsw"

    class NoIndex(FakeRetrievalSut):
        def plan(self, path):
            return "Seq Scan"

    with pytest.raises(RuntimeError, match="vacuous"):
        MeasureAnnRecall(NoIndex).execute([(c.profile, ledger, c.probes)], "v1", git_sha="x")

    with pytest.raises(RuntimeError, match="proves nothing"):
        MeasureAnnRecall(IndexedExact).execute([(c.profile, ledger, c.probes)], "v1", git_sha="x")


def test_ann_check_refuses_a_mixed_language_store():
    en, fr = sample_content(EvalLanguage.EN), sample_content(EvalLanguage.FR)
    with pytest.raises(ValueError, match="one Eval Language"):
        MeasureAnnRecall(FakeRetrievalSut).execute(
            [(en.profile, en.ledger, en.probes), (fr.profile, fr.ledger, fr.probes)], "v1", git_sha="x")
