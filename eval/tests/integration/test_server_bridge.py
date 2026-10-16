# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-6 — the bridge drives the real embedding + pgvector search in a Scratch Schema and
leaves nothing behind; TR-509 — hits come back as similarities, best first."""
from datetime import date

import pytest

from mamemai_eval.adapters import server_bridge
from mamemai_eval.domain.model import EvalLanguage
from mamemai_eval.usecases.retrieval import MemoryRecord, RetrievalQuery
from tests.integration.conftest import TEST_DSN

pytestmark = pytest.mark.skipif(not server_bridge.server_python().exists(), reason="server venv not installed")


def test_real_search_ranks_by_similarity_and_drops_its_scratch_schema(monkeypatch, conn):
    monkeypatch.setenv("MAMEMAI_EVAL_DATABASE_URL", TEST_DSN)
    records = [
        MemoryRecord("ep", "episode", "Camille a vu une éclipse solaire à Nice", None, date(2026, 6, 10), EvalLanguage.FR),
        MemoryRecord("co", "concept", "Camille habite à Lyon dans un appartement", "Lyon", None, EvalLanguage.FR),
        MemoryRecord("kn", "concept", "Camille a appris que Jupiter est la plus grande planète", "Jupiter", None, EvalLanguage.FR),
    ]
    with server_bridge.ServerBridgeRetrievalSut() as sut:
        sut.index(records)
        answers = sut.search([RetrievalQuery("q1", "Quelle est la plus grosse planète ?"),
                              RetrievalQuery("q2", "Where did I watch the eclipse?")], top_n=3)
        schema = sut._schema
        assert sut.describe().facts["vector_index"] == "hnsw"
    assert [h.fact_id for h in answers[0]][0] == "kn"
    assert [h.fact_id for h in answers[1]][0] == "ep"  # cross-lingual, across memory types
    for hits in answers:
        sims = [h.similarity for h in hits]
        assert sims == sorted(sims, reverse=True) and all(0.5 < s <= 1.0 for s in sims)
    assert conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)).fetchone() is None
