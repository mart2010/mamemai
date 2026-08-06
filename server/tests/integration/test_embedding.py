# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Integration test + calibration harness for the real embedding model. Skips gracefully
when the model can't be loaded (e.g. the dev laptop, or not yet downloaded).

This is also the "print similarity scores to help determine a good threshold value"
calibration test called for in PLAN.md Phase 3 — run with `pytest -s` to see the printed
numbers. The two-tier threshold (0.93 auto-merge / 0.75 disambiguate, per CLAUDE.md) is
derived from real data as of 2026-08-04 (see infrastructure/config.py); this test
asserts *relative* ordering (similar > dissimilar), not the absolute cutoff values.

Both sides use `embed_passage`: these are document-to-document comparisons, which is
what upsert.py's merge decision performs, so they read the same scale the merge and
disambiguate thresholds were derived against. The asymmetric query->passage pairing
that recall uses is exercised in test_recall_prefix_pairing below.
"""
import math

import pytest

from mamemai_server.infrastructure.embedding import SentenceTransformerEmbeddingService

_EXPECTED_DIM = 1024


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b)


@pytest.fixture(scope="module")
def embedding_service() -> SentenceTransformerEmbeddingService:
    try:
        return SentenceTransformerEmbeddingService()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"embedding model unavailable: {e}")


class TestSentenceTransformerEmbeddingService:
    def test_embedding_dimension_matches_schema(self, embedding_service: SentenceTransformerEmbeddingService) -> None:
        """Spec: TR-501, TR-952"""
        vec = embedding_service.embed_passage("hello")
        assert len(vec) == _EXPECTED_DIM

    def test_embedding_is_normalized(self, embedding_service: SentenceTransformerEmbeddingService) -> None:
        """Spec: TR-952"""
        vec = embedding_service.embed_passage("hello")
        norm = math.sqrt(sum(x * x for x in vec))
        assert norm == pytest.approx(1.0, abs=1e-3)

    def test_identical_text_has_similarity_one(self, embedding_service: SentenceTransformerEmbeddingService) -> None:
        """Spec: TR-509"""
        vec = embedding_service.embed_passage("The quick brown fox.")
        assert _cosine(vec, vec) == pytest.approx(1.0, abs=1e-4)

    @pytest.mark.parametrize(
        "text_a,text_b,relation",
        [
            ("The dog ran in the park.", "A dog was running in the park.", "similar"),
            ("The dog ran in the park.", "Quarterly tax filings are due in April.", "dissimilar"),
            ("golden retriever", "dog", "similar"),
            ("big bang theory of the universe", "Big Bang the TV sitcom", "dissimilar"),
            ("J'aime le café le matin.", "I like coffee in the morning.", "similar"),  # cross-lingual
        ],
    )
    def test_calibration_similarity_scores(
        self, embedding_service: SentenceTransformerEmbeddingService, text_a: str, text_b: str, relation: str
    ) -> None:
        """Spec: TR-602"""
        similarity = _cosine(embedding_service.embed_passage(text_a), embedding_service.embed_passage(text_b))
        print(f"\n[calibration:{relation}] {similarity:.4f}  '{text_a}'  vs  '{text_b}'")

    def test_similar_pairs_score_higher_than_dissimilar_pairs(
        self, embedding_service: SentenceTransformerEmbeddingService
    ) -> None:
        """Spec: TR-602"""
        similar = _cosine(
            embedding_service.embed_passage("The dog ran in the park."),
            embedding_service.embed_passage("A dog was running in the park."),
        )
        dissimilar = _cosine(
            embedding_service.embed_passage("The dog ran in the park."),
            embedding_service.embed_passage("Quarterly tax filings are due in April."),
        )
        print(f"\n[calibration] similar={similar:.4f} dissimilar={dissimilar:.4f}")
        assert similar > dissimilar

    def test_query_and_passage_prefixes_are_actually_different(
        self, embedding_service: SentenceTransformerEmbeddingService
    ) -> None:
        """Spec: TR-509 — the two methods must not be aliases.

        The whole point of the port having two methods is that e5 encodes the same string
        differently depending on the role it plays. If a refactor ever made one delegate
        to the other, every threshold would silently revert to the compressed scale that
        this change exists to escape, and nothing else in the suite would notice.
        """
        text = "les techniques mnémotechniques"
        as_query = embedding_service.embed_query(text)
        as_passage = embedding_service.embed_passage(text)

        assert _cosine(as_query, as_passage) < 0.999

    def test_recall_pairing_separates_a_real_hit_from_an_unrelated_one(
        self, embedding_service: SentenceTransformerEmbeddingService
    ) -> None:
        """Spec: TR-509, FR-309 — the asymmetric pairing recall actually performs.

        A question embedded as a query must sit closer to the passage that answers it
        than to an unrelated one. This is the property MIN_RECALL_SIMILARITY depends on;
        asserting only relative ordering keeps it robust to the model being swapped.
        """
        question = embedding_service.embed_query("What did we discuss about memory techniques?")
        relevant = embedding_service.embed_passage(
            "Techniques mnémotechniques: strategies for memorising information more effectively."
        )
        unrelated = embedding_service.embed_passage(
            "Boiler maintenance: how often to service a domestic gas boiler."
        )

        assert _cosine(question, relevant) > _cosine(question, unrelated)
