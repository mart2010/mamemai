# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Ports of the eval use-case layer. Concrete implementations live in adapters/."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from ..domain.model import Corpus, Ledger, Probe, Run, Scenario, SyntheticUserProfile


class QuotaExhausted(Exception):
    """The Reference LLM refused for lack of quota/rate. The caller stops at its checkpoint
    and resumes later from the cache — it must NEVER switch to another model (EV-INV-10)."""


class FrozenCorpusError(Exception):
    """A change to a Profile that belongs to a frozen Corpus (EV-INV-5)."""


class ReferenceLLMError(Exception):
    """Any other Reference LLM failure (bad exit status, unparsable output)."""


@dataclass(frozen=True)
class ReferenceRequest:
    system: str
    prompt: str
    model: str  # always explicit and pinned (EV-INV-10)
    temperature: float | None = None  # None = provider default; the `claude -p` CLI cannot set it


@dataclass(frozen=True)
class ReferenceResponse:
    text: str
    model: str  # the model the provider says actually answered


class ReferenceLLM(Protocol):
    comparable: bool  # False for smoke-only adapters (EV-INV-10)

    def complete(self, request: ReferenceRequest) -> ReferenceResponse: ...


class ReferenceLLMCache(Protocol):
    """Content-addressed request → response store (EV-INV-8)."""

    def get(self, key: str) -> ReferenceResponse | None: ...

    def put(self, key: str, request: ReferenceRequest, response: ReferenceResponse) -> None: ...


class EvalRepository(Protocol):
    """Durable `eval` schema. The SUT never uses this (EV-INV-6)."""

    def save_profile_with_ledger(self, profile: SyntheticUserProfile, ledger: Ledger) -> None: ...

    def get_profile(self, profile_id: UUID) -> SyntheticUserProfile: ...

    def get_ledger(self, profile_id: UUID) -> Ledger: ...

    def profile_exists(self, profile_id: UUID) -> bool: ...

    def delete_profile(self, profile_id: UUID) -> None: ...

    def save_probes(self, profile_id: UUID, probes: list[Probe]) -> None:
        """Replaces the Profile's probes (drafts only, like delete_profile)."""
        ...

    def get_probes(self, profile_id: UUID) -> list[Probe]: ...

    def save_scenarios(self, profile_id: UUID, scenarios: list[Scenario]) -> None:
        """Replaces the Profile's Scenarios (drafts only)."""
        ...

    def get_scenarios(self, profile_id: UUID) -> list[Scenario]: ...

    def save_corpus(self, corpus: Corpus) -> None: ...

    def get_corpus(self, version: str) -> Corpus: ...

    def list_corpora(self) -> list[Corpus]: ...

    def is_frozen(self, profile_id: UUID) -> bool: ...

    def list_profile_ids(self) -> list[UUID]: ...

    def save_run(self, run: Run) -> None: ...

    def get_run(self, run_id: UUID) -> Run: ...

    def list_runs(self, suite: str) -> list[Run]: ...
