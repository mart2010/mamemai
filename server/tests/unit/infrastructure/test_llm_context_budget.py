# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""The context budget every local Ollama call is measured against (TR-951/TR-952/FR-307).

Ollama's default `num_ctx` is 4096 and it truncates longer prompts in silence: the model
answers about a prompt it never saw, extraction returns empty arrays, and every stage
downstream reads that as "nothing worth remembering". A 37-minute conversation was
discarded twice that way with no error in any log. These tests pin the two halves of the
fix — the value is sent explicitly, and an overflow is *loud* — because both are invisible
by construction: nothing in the pipeline fails when they regress.
"""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mamemai_server.domain.model import Conversation, Language, Speaker, Turn
from mamemai_server.infrastructure.llm._common import (
    OLLAMA_DEFAULT_NUM_CTX,
    context_overflow,
    estimate_tokens,
    usable_context,
)
from mamemai_server.infrastructure.llm.ollama import (
    OllamaConsolidationExtractor,
    OllamaLLMService,
    OllamaWorthinessEvaluator,
)
from mamemai_server.services.ports import Message


class _Response:
    """Shape of the ollama-python reply the adapters read: `.message.content`."""

    class _Message:
        def __init__(self, content: str) -> None:
            self.content = content

    def __init__(self, content: str) -> None:
        self.message = self._Message(content)


class FakeOllamaClient:
    """Records every call's kwargs and answers with canned content."""

    def __init__(self, content: str = "{}") -> None:
        self.calls: list[dict] = []
        self._content = content

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(self._content)


class FakeAsyncOllamaClient:
    def __init__(self, chunks: tuple[str, ...] = ("hi",)) -> None:
        self.calls: list[dict] = []
        self._chunks = chunks

    async def chat(self, **kwargs):
        self.calls.append(kwargs)

        async def _stream():
            for chunk in self._chunks:
                yield _Response(chunk)

        return _stream()


def _conversation(turns: int, words_per_turn: int = 5) -> Conversation:
    now = datetime.now(UTC)
    content = " ".join(["mot"] * words_per_turn)
    return Conversation(
        id=1,
        persona_id=uuid4(),
        started_at=now,
        ended_at=now,
        turns=[
            Turn(
                speaker=Speaker.USER if i % 2 == 0 else Speaker.ASSISTANT,
                content=content,
                timestamp=now,
                language=Language("fr"),
            )
            for i in range(turns)
        ],
    )


class TestEstimateTokens:
    def test_counts_every_part_of_the_prompt(self):
        """Spec: TR-952 — the budget is spent by the system prompt as well as the
        transcript, so both are counted."""
        assert estimate_tokens("a" * 300, "b" * 300) == 200

    def test_ignores_empty_parts(self):
        """Spec: TR-952"""
        assert estimate_tokens("a" * 300, "", None or "") == 100

    def test_over_counts_rather_than_under_counts(self):
        """Spec: TR-952 — characters÷3 is deliberate. English averages nearer 4 chars per
        token, so the estimate runs high; for a warning that is the safe direction, and it
        is why no tokenizer dependency was taken on."""
        english = "the quick brown fox jumps over the lazy dog " * 10
        assert estimate_tokens(english) > len(english.split())


class TestUsableContext:
    def test_reserves_room_for_the_answer(self):
        """Spec: TR-952 — num_ctx covers the prompt *and* the generation, so the prompt's
        budget is what is left after the answer."""
        assert usable_context(16384) < 16384

    def test_never_returns_a_non_positive_budget(self):
        """Spec: TR-952 — a num_ctx smaller than the reserve is a misconfiguration, not a
        reason to compute a negative budget and call every prompt an overflow silently."""
        assert usable_context(1) >= 1


class TestContextOverflow:
    def test_a_fitting_prompt_is_silent(self, capsys):
        """Spec: TR-952 — the normal case must not print; the line is an alarm."""
        assert context_overflow("extraction window", 16384, "short prompt") is False
        assert capsys.readouterr().out == ""

    def test_an_oversized_prompt_says_so_on_stdout(self, capsys):
        """Spec: TR-952 — the defect being fixed is the silence, not the overflow: before
        this line, a truncated call was indistinguishable from an empty conversation."""
        assert context_overflow("extraction window", OLLAMA_DEFAULT_NUM_CTX, "x" * 60_000) is True

        out = capsys.readouterr().out
        assert "[llm] CONTEXT OVERFLOW" in out
        assert "extraction window" in out
        assert f"num_ctx={OLLAMA_DEFAULT_NUM_CTX}" in out


class TestExtractorContextHandling:
    def test_sends_num_ctx_on_every_call(self):
        """Spec: TR-951, TR-952 — omitting it is not a neutral default: it is 4096, and at
        4096 this same conversation extracts nothing."""
        extractor = OllamaConsolidationExtractor(model="m", num_ctx=16384)
        extractor._client = FakeOllamaClient('{"episodes": [], "concepts": []}')

        extractor.extract(_conversation(4))

        assert extractor._client.calls[0]["options"] == {"num_ctx": 16384}

    def test_an_over_budget_window_is_skipped_loudly(self, capsys):
        """Spec: FR-307, TR-952 — measured 2026-08-03: a truncated extraction call returns
        valid JSON with empty arrays every time, which downstream cannot tell from "nothing
        here". Asking anyway buys a lie at 30s a call, so the window is skipped and the
        printed line is the result."""
        extractor = OllamaConsolidationExtractor(model="m", num_ctx=OLLAMA_DEFAULT_NUM_CTX)
        extractor._client = FakeOllamaClient('{"episodes": [], "concepts": []}')

        result = extractor.extract(_conversation(4, words_per_turn=2000))

        assert extractor._client.calls == []
        assert result.concepts == [] and result.episodes == []
        assert "[llm] CONTEXT OVERFLOW" in capsys.readouterr().out


class TestWorthinessContextHandling:
    def test_sends_num_ctx(self):
        """Spec: TR-951, TR-952"""
        evaluator = OllamaWorthinessEvaluator(model="m", num_ctx=16384)
        evaluator._client = FakeOllamaClient("NO")

        evaluator.evaluate(_conversation(4))

        assert evaluator._client.calls[0]["options"] == {"num_ctx": 16384}

    def test_an_over_budget_window_is_still_asked_but_warned_about(self, capsys):
        """Spec: FR-307, TR-952 — the opposite call to extraction's, deliberately: a
        YES/NO verdict over the surviving part of a transcript is degraded, not fabricated,
        and skipping would discard a long conversation outright — the very failure this
        machinery exists to stop."""
        evaluator = OllamaWorthinessEvaluator(model="m", num_ctx=OLLAMA_DEFAULT_NUM_CTX)
        evaluator._client = FakeOllamaClient("YES")

        assert evaluator.evaluate(_conversation(4, words_per_turn=2000)) is True
        assert evaluator._client.calls != []
        assert "[llm] CONTEXT OVERFLOW" in capsys.readouterr().out


class TestLiveServiceContextHandling:
    @pytest.mark.asyncio
    async def test_sends_num_ctx_and_reports_the_context_every_turn(self, capsys):
        """Spec: TR-311, TR-951 — unconditional, not warning-only: the live context grows
        all session while TR-309's rolling summary doesn't fire until turn 50, so the
        operator needs the trend. Measured on a real session: 2 526 tokens at turn 12,
        4 141 at 18."""
        service = OllamaLLMService(model="m", num_ctx=16384)
        service._client = FakeAsyncOllamaClient(("bon", "jour"))

        chunks = [chunk async for chunk in service.complete([Message(role="user", content="salut")], "sys")]

        assert chunks == ["bon", "jour"]
        assert service._client.calls[0]["options"] == {"num_ctx": 16384}
        out = capsys.readouterr().out
        assert "[llm] context:" in out
        assert "num_ctx=16384" in out
        assert "OVERFLOW" not in out

    @pytest.mark.asyncio
    async def test_flags_the_turn_where_the_live_context_overflows(self, capsys):
        """Spec: TR-311, TR-952 — llama.cpp keeps the system prompt and drops the oldest
        turns, so an overflowing session degrades into amnesia rather than erroring. The
        line is the only signal there is."""
        service = OllamaLLMService(model="m", num_ctx=OLLAMA_DEFAULT_NUM_CTX)
        service._client = FakeAsyncOllamaClient()

        [chunk async for chunk in service.complete([Message(role="user", content="x" * 60_000)], "sys")]

        assert "OVERFLOW" in capsys.readouterr().out
