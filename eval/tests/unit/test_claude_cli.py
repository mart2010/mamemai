# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-10 — pinned model, quota detection, no substitution."""
import json

import pytest

from mamemai_eval.adapters.claude_cli import ClaudeCliReferenceLLM, CompletedProcess
from mamemai_eval.adapters.ollama_smoke import OllamaSmokeReferenceLLM
from mamemai_eval.usecases.ports import QuotaExhausted, ReferenceLLMError, ReferenceRequest

REQ = ReferenceRequest(system="sys", prompt="hello", model="claude-test-1")


def ok(model="claude-test-1", result="hi"):
    return CompletedProcess(0, json.dumps({"is_error": False, "result": result, "modelUsage": {model: {}}}), "")


def runner_returning(proc, seen=None):
    def run(argv, stdin, cwd):
        if seen is not None:
            seen.update(argv=argv, stdin=stdin, cwd=cwd)
        return proc
    return run


def test_success_and_command_shape():
    seen = {}
    r = ClaudeCliReferenceLLM(runner_returning(ok(), seen)).complete(REQ)
    assert (r.text, r.model) == ("hi", "claude-test-1")
    argv = seen["argv"]
    assert argv[argv.index("--model") + 1] == "claude-test-1"
    assert "--bare" not in argv  # would skip keychain → subscription auth breaks
    assert argv[argv.index("--tools") + 1] == ""
    assert seen["stdin"] == "hello"
    assert "mamemai" in seen["cwd"]  # throwaway cwd, not the repo


@pytest.mark.parametrize("payload", [
    {"is_error": True, "api_error_status": 429, "result": "x"},
    {"is_error": True, "api_error_status": None, "result": "Claude AI usage limit reached|123"},
])
def test_quota_is_distinguished(payload):
    with pytest.raises(QuotaExhausted):
        ClaudeCliReferenceLLM(runner_returning(CompletedProcess(1, json.dumps(payload), ""))).complete(REQ)


def test_other_failures_are_not_quota():
    with pytest.raises(ReferenceLLMError) as e:
        ClaudeCliReferenceLLM(runner_returning(CompletedProcess(1, "boom", "segfault"))).complete(REQ)
    assert not isinstance(e.value, QuotaExhausted)


def test_answer_from_a_different_model_is_rejected():
    with pytest.raises(ReferenceLLMError, match="pinned"):
        ClaudeCliReferenceLLM(runner_returning(ok(model="claude-other"))).complete(REQ)


def test_temperature_is_refused_rather_than_silently_ignored():
    with pytest.raises(ReferenceLLMError):
        ClaudeCliReferenceLLM(runner_returning(ok())).complete(
            ReferenceRequest(system="s", prompt="p", model="claude-test-1", temperature=0.2))


def test_ollama_adapter_is_marked_non_comparable():
    assert ClaudeCliReferenceLLM().comparable is True
    assert OllamaSmokeReferenceLLM().comparable is False
