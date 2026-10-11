# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""In-memory Fakes of the eval ports (default over mocks, per the monorepo convention)."""
from __future__ import annotations

from uuid import UUID

from mamemai_eval.domain.model import Corpus, Ledger, Probe, Run, SyntheticUserProfile
from mamemai_eval.usecases.ports import FrozenCorpusError, QuotaExhausted, ReferenceRequest, ReferenceResponse


class FakeReferenceLLM:
    """Scripted Reference LLM. `quota_after=N` raises QuotaExhausted from call N+1 on."""

    def __init__(self, replies: list[str] | None = None, *, comparable: bool = True, quota_after: int | None = None):
        self.comparable = comparable
        self.calls: list[ReferenceRequest] = []
        self._replies = list(replies or [])
        self._quota_after = quota_after

    def complete(self, request: ReferenceRequest) -> ReferenceResponse:
        if self._quota_after is not None and len(self.calls) >= self._quota_after:
            raise QuotaExhausted("fake quota exhausted")
        self.calls.append(request)
        text = self._replies.pop(0) if self._replies else f"reply-{len(self.calls)}"
        return ReferenceResponse(text=text, model=request.model)


class InMemoryReferenceLLMCache:
    def __init__(self) -> None:
        self.store: dict[str, ReferenceResponse] = {}

    def get(self, key: str) -> ReferenceResponse | None:
        return self.store.get(key)

    def put(self, key: str, request: ReferenceRequest, response: ReferenceResponse) -> None:
        self.store.setdefault(key, response)


class FakeEvalRepository:
    def __init__(self) -> None:
        self._profiles: dict[UUID, SyntheticUserProfile] = {}
        self._ledgers: dict[UUID, Ledger] = {}
        self._runs: dict[UUID, Run] = {}
        self._probes: dict[UUID, list[Probe]] = {}
        self._corpora: dict[str, Corpus] = {}

    def _guard(self, profile_id: UUID) -> None:
        if self.is_frozen(profile_id):
            raise FrozenCorpusError(f"profile {profile_id} belongs to a frozen corpus")

    def save_profile_with_ledger(self, profile: SyntheticUserProfile, ledger: Ledger) -> None:
        self._guard(profile.profile_id)
        if ledger.profile_id != profile.profile_id:
            raise ValueError("ledger belongs to another profile")
        self._profiles[profile.profile_id] = profile
        self._ledgers[profile.profile_id] = ledger

    def get_profile(self, profile_id: UUID) -> SyntheticUserProfile:
        return self._profiles[profile_id]

    def get_ledger(self, profile_id: UUID) -> Ledger:
        return self._ledgers[profile_id]

    def profile_exists(self, profile_id: UUID) -> bool:
        return profile_id in self._profiles

    def delete_profile(self, profile_id: UUID) -> None:
        self._guard(profile_id)
        self._profiles.pop(profile_id, None)
        self._ledgers.pop(profile_id, None)

    def save_probes(self, profile_id: UUID, probes: list[Probe]) -> None:
        self._guard(profile_id)
        self._probes[profile_id] = sorted(probes, key=lambda p: p.probe_id)

    def get_probes(self, profile_id: UUID) -> list[Probe]:
        return list(self._probes.get(profile_id, []))

    def save_corpus(self, corpus: Corpus) -> None:
        if corpus.version in self._corpora:
            raise ValueError("corpus exists")
        self._corpora[corpus.version] = corpus

    def get_corpus(self, version: str) -> Corpus:
        return self._corpora[version]

    def list_corpora(self) -> list[Corpus]:
        return sorted(self._corpora.values(), key=lambda c: c.created_at)

    def is_frozen(self, profile_id: UUID) -> bool:
        return any(profile_id in c.profile_ids for c in self._corpora.values())

    def list_profile_ids(self) -> list[UUID]:
        return list(self._profiles)

    def save_run(self, run: Run) -> None:
        self._runs[run.run_id] = run

    def get_run(self, run_id: UUID) -> Run:
        return self._runs[run_id]

    def list_runs(self, suite: str) -> list[Run]:
        return sorted((r for r in self._runs.values() if r.suite == suite), key=lambda r: r.started_at, reverse=True)
