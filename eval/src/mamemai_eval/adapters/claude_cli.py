# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Reference LLM over headless `claude -p` — draws on the Claude Code subscription (D1).

Runs in a throwaway cwd with no tools, no settings sources, no slash commands and no
session persistence, so the repo's CLAUDE.md/memory/hooks cannot leak into a generation
or a judgement. `--bare` is deliberately not used: it skips keychain reads, which is where
subscription (OAuth) credentials live."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

from ..usecases.ports import QuotaExhausted, ReferenceLLMError, ReferenceRequest, ReferenceResponse


@dataclass(frozen=True)
class CompletedProcess:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], str, str], CompletedProcess]  # (argv, stdin, cwd)

_QUOTA = re.compile(r"usage limit|rate.?limit|limit reached|quota|overloaded|too many requests", re.I)


def _subprocess_runner(argv: list[str], stdin: str, cwd: str) -> CompletedProcess:
    p = subprocess.run(argv, input=stdin, capture_output=True, text=True, cwd=cwd, timeout=900)
    return CompletedProcess(p.returncode, p.stdout, p.stderr)


class ClaudeCliReferenceLLM:
    comparable = True

    def __init__(self, runner: Runner = _subprocess_runner, executable: str = "claude") -> None:
        self._runner = runner
        self._exe = executable

    def build_argv(self, request: ReferenceRequest) -> list[str]:
        return [
            self._exe, "-p", "--model", request.model, "--output-format", "json",
            "--system-prompt", request.system, "--tools", "", "--no-session-persistence",
            "--setting-sources", "", "--disable-slash-commands",
        ]

    def complete(self, request: ReferenceRequest) -> ReferenceResponse:
        if request.temperature is not None:
            raise ReferenceLLMError("`claude -p` cannot set temperature; pass temperature=None")
        with tempfile.TemporaryDirectory(prefix="mamemai-eval-claude-") as cwd:
            proc = self._runner(self.build_argv(request), request.prompt, cwd)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            self._raise(proc.stdout + proc.stderr, proc.returncode)
        if data.get("is_error") or proc.returncode != 0:
            self._raise(f"{data.get('api_error_status')} {data.get('result')} {proc.stderr}", proc.returncode,
                        status=data.get("api_error_status"))
        usage = data.get("modelUsage") or {}
        answered = next(iter(usage), request.model)
        if answered != request.model:
            # EV-INV-10: the provider answered with something other than the pinned model.
            raise ReferenceLLMError(f"pinned {request.model!r} but {answered!r} answered")
        return ReferenceResponse(text=str(data["result"]), model=answered)

    @staticmethod
    def _raise(detail: str, returncode: int, status: object = None) -> None:
        if status == 429 or _QUOTA.search(detail):
            raise QuotaExhausted(detail.strip()[:500])
        raise ReferenceLLMError(f"claude -p failed (exit {returncode}): {detail.strip()[:500]}")
