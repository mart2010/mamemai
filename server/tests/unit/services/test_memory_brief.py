# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""GenerateMemoryBrief: what the brief is actually built from.

Every assertion here is on the prompt the LLM receives, not on its answer. The bug these
tests exist for — a GenerateMemoryBrief with no memory repository at all, live from the
first commit to 2026-08-02 — was invisible precisely because the only existing test
looked at a fake's canned output instead.
"""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mamemai_server.domain.model import Concept, Episode, Language
from mamemai_server.services.memory import GenerateMemoryBrief

from tests.fakes.fakes import FakeLLMService, FakeMemoryBriefRepository, FakeMemoryRepository

_NOW = datetime.now(UTC)
_PERSONA = uuid4()
_OTHER_PERSONA = uuid4()


def _episode(summary: str) -> Episode:
    return Episode(id=None, summary=summary, happened_at=_NOW, origin_conversation_id=1)


def _concept(name: str, origin: str = "organic", persona_id=None) -> Concept:
    return Concept(
        id=None, persona_id=persona_id or _PERSONA, name=name,
        description=f"about {name}", language=Language("en"), origin=origin,
    )


def _make(memory_repo: FakeMemoryRepository, **kwargs):
    llm = FakeLLMService(response="A brief.")
    brief_repo = FakeMemoryBriefRepository()
    return GenerateMemoryBrief(
        llm=llm, memory_brief_repo=brief_repo, memory_repo=memory_repo, **kwargs,
    ), llm, brief_repo


def _prompt(llm: FakeLLMService) -> str:
    (messages, _system), = llm.calls
    return messages[0].content


class TestGenerateMemoryBrief:
    @pytest.mark.asyncio
    async def test_brief_is_built_from_stored_memory(self):
        """Spec: FR-308 — the whole point: real memory reaches the prompt. Asserted on
        the prompt rather than the answer, since a fake LLM's answer is the same either
        way — which is how the missing read went unnoticed for months."""
        memory_repo = FakeMemoryRepository()
        memory_repo.upsert_episode(_episode("Hiked in the Alps last weekend."))
        memory_repo.upsert_concept(_concept("zodiacal light"))
        use_case, llm, brief_repo = _make(memory_repo)

        brief = await use_case.execute(_NOW)

        prompt = _prompt(llm)
        assert "Hiked in the Alps last weekend." in prompt
        assert "zodiacal light" in prompt
        assert brief is not None
        assert brief_repo.get() is not None

    @pytest.mark.asyncio
    async def test_no_memory_writes_no_brief(self):
        """Spec: FR-308 — with nothing to summarise, the LLM can only report that it
        knows nothing about the user, and saving that would overwrite a good brief with
        a worse one. Observed live 2026-08-02: "Based on our conversation (which is
        non-existent), I don't have any information about the user yet.\""""
        use_case, llm, brief_repo = _make(FakeMemoryRepository())

        brief = await use_case.execute(_NOW)

        assert brief is None
        assert llm.calls == []          # not even asked
        assert brief_repo.get() is None  # and nothing overwritten

    @pytest.mark.asyncio
    async def test_authored_concepts_are_excluded(self):
        """Spec: FR-303, FR-310 — a bundle's curriculum is the owning persona's material,
        not a fact about the user. The brief goes into every session's system prompt,
        including personas that never declared `reads_across_personas`, so authored
        content must not travel in it."""
        memory_repo = FakeMemoryRepository()
        memory_repo.upsert_concept(_concept("passato prossimo", origin="authored"))
        memory_repo.upsert_concept(_concept("noctilucent clouds", origin="organic"))
        use_case, llm, _ = _make(memory_repo)

        await use_case.execute(_NOW)

        prompt = _prompt(llm)
        assert "noctilucent clouds" in prompt
        assert "passato prossimo" not in prompt

    @pytest.mark.asyncio
    async def test_organic_concepts_from_any_persona_are_included(self):
        """Spec: FR-303 — organic concepts are things the user themselves raised
        (FR-310's provenance rule), whichever persona happened to be active at the time;
        the brief is about the user, so persona scope is not the axis that matters here.
        `origin` is, and it is a generic field (INV-6)."""
        memory_repo = FakeMemoryRepository()
        memory_repo.upsert_concept(_concept("halo rings", persona_id=_OTHER_PERSONA))
        use_case, llm, _ = _make(memory_repo)

        await use_case.execute(_NOW)

        assert "halo rings" in _prompt(llm)

    @pytest.mark.asyncio
    async def test_material_is_bounded_and_takes_the_most_recent(self):
        """Spec: FR-308 — the prompt must stay bounded as memory grows, and the tail of
        an insertion-ordered listing is the recent end."""
        memory_repo = FakeMemoryRepository()
        for i in range(10):
            memory_repo.upsert_concept(_concept(f"concept {i}"))
        use_case, llm, _ = _make(memory_repo, max_concepts=3)

        await use_case.execute(_NOW)

        prompt = _prompt(llm)
        assert "concept 9" in prompt
        assert "concept 7" in prompt
        assert "concept 6" not in prompt
