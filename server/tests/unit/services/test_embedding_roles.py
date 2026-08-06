# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: TR-509 — each caller must embed with the role its text actually plays.

e5 is asymmetric: `query: ` on the text being searched *with*, `passage: ` on the text
being searched *for*. Getting it backwards is invisible at runtime — no error, no
exception, just every threshold reading a scale it was not derived against and recall
quietly degrading. Nothing else in the suite would notice, because both methods return a
well-formed vector.

So these assert the *role*, not the value, using FakeEmbeddingService's separate
query_calls / passage_calls logs.
"""



from mamemai_server.domain.model import Concept, GENERAL_ASSISTANT_ID, Language, MemoryType
from mamemai_server.services.memory import TriggerRecall
from mamemai_server.services.upsert import MemoryUpserter, embeddable_text

from tests.fakes.fakes import (
    FakeDisambiguationEvaluator,
    FakeEmbeddingService,
    FakeMemoryRepository,
    FakeMemorySynthesizer,
)


def _concept(name: str = "Zodiacal light") -> Concept:
    return Concept(
        id=None, persona_id=GENERAL_ASSISTANT_ID, name=name,
        description=f"{name} is a faint glow along the ecliptic.",
        language=Language("en"),
    )


class TestStoredContentIsAPassage:
    def test_upserting_a_concept_embeds_it_as_a_passage(self):
        """Stored content is what a search runs *against*, so it takes the passage
        prefix — and the same vector is reused for the insert-vs-merge search, which is
        document-to-document and therefore wants both sides on one scale."""
        embedding = FakeEmbeddingService()
        upserter = MemoryUpserter(
            FakeMemoryRepository(), embedding,
            FakeDisambiguationEvaluator(), FakeMemorySynthesizer(),
        )
        concept = _concept()

        upserter.upsert_concept(concept, persona_id=GENERAL_ASSISTANT_ID,
                                user_turns=["tell me about zodiacal light"])

        assert embedding.passage_calls
        assert not embedding.query_calls

    def test_the_embedded_text_is_the_shared_format(self):
        """The backfill script reproduces this string from SQL; if upsert ever embedded
        something else, every re-embedded row would be subtly wrong and nothing would
        fail loudly. `embeddable_text` is the single definition both use."""
        embedding = FakeEmbeddingService()
        upserter = MemoryUpserter(
            FakeMemoryRepository(), embedding,
            FakeDisambiguationEvaluator(), FakeMemorySynthesizer(),
        )
        concept = _concept()

        upserter.upsert_concept(concept, persona_id=GENERAL_ASSISTANT_ID,
                                user_turns=["tell me about zodiacal light"])

        assert embeddable_text(concept.name, concept.description) in embedding.passage_calls


class TestSearchTextIsAQuery:
    def test_trigger_recall_embeds_its_question_as_a_query(self):
        embedding = FakeEmbeddingService()
        repo = FakeMemoryRepository()
        recall = TriggerRecall(embedding_service=embedding, memory_repo=repo)

        recall.execute("what did we say about zodiacal light?", (MemoryType.CONCEPT,))

        assert embedding.query_calls == ["what did we say about zodiacal light?"]
        assert not embedding.passage_calls


class TestTheTwoRolesAreDistinguishable:
    def test_the_fake_records_them_separately(self):
        """Guards the guard: if FakeEmbeddingService ever collapsed both methods into one
        log, every test above would keep passing while checking nothing."""
        embedding = FakeEmbeddingService()

        embedding.embed_query("a question")
        embedding.embed_passage("a stored fact")

        assert embedding.query_calls == ["a question"]
        assert embedding.passage_calls == ["a stored fact"]
        # The combined log stays ordered, so existing call-count assertions still hold.
        assert embedding.calls == ["a question", "a stored fact"]
