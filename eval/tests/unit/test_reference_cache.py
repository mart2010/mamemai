# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-8 (every call cached), EV-INV-10 (quota → stop and resume, never substitute)."""
import pytest

from mamemai_eval.usecases.ports import QuotaExhausted, ReferenceRequest
from mamemai_eval.usecases.reference import CachingReferenceLLM, cache_key
from tests.fakes import FakeReferenceLLM, InMemoryReferenceLLMCache


def req(prompt="p", model="m1"):
    return ReferenceRequest(system="s", prompt=prompt, model=model)


def test_second_identical_call_is_served_from_cache():
    inner = FakeReferenceLLM(["one", "two"])
    llm = CachingReferenceLLM(inner, InMemoryReferenceLLMCache())
    assert llm.complete(req()).text == "one"
    assert llm.complete(req()).text == "one"
    assert len(inner.calls) == 1


def test_model_is_part_of_the_key():
    assert cache_key(req(model="a")) != cache_key(req(model="b"))
    inner = FakeReferenceLLM(["from-a", "from-b"])
    llm = CachingReferenceLLM(inner, InMemoryReferenceLLMCache())
    assert llm.complete(req(model="a")).text == "from-a"
    assert llm.complete(req(model="b")).text == "from-b"


def test_quota_exhaustion_stops_then_resumes_without_repeating_work():
    cache = InMemoryReferenceLLMCache()
    first = CachingReferenceLLM(FakeReferenceLLM(["r1", "r2"], quota_after=2), cache)
    prompts = ["a", "b", "c"]
    done = []
    with pytest.raises(QuotaExhausted):
        for p in prompts:
            done.append(first.complete(req(p)).text)
    assert done == ["r1", "r2"]
    # later (quota reset): same model, fresh process, same cache — only the failed call is made
    resumed_inner = FakeReferenceLLM(["r3"])
    resumed = CachingReferenceLLM(resumed_inner, cache)
    assert [resumed.complete(req(p)).text for p in prompts] == ["r1", "r2", "r3"]
    assert len(resumed_inner.calls) == 1


def test_comparable_flag_is_propagated():
    assert not CachingReferenceLLM(FakeReferenceLLM(comparable=False), InMemoryReferenceLLMCache()).comparable
