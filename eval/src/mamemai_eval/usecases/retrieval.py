# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""`retrieval` Suite (DESIGN §Suites): a frozen Ledger rendered as memory items, indexed in the
System Under Test, then queried with the Corpus' Probes. No Simulated User, no SUT LLM.

The SUT is reached through the `RetrievalSut` port, so this layer never sees an embedding model,
a database or `mamemai_server` (EV-INV-1)."""
from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from datetime import UTC, datetime, date
from pathlib import Path
from typing import ContextManager, Protocol
from uuid import UUID, uuid4

from ..domain.model import (
    EvalLanguage, FactKind, Ledger, LedgerFact, Probe, Run, RunFingerprint, Score, SyntheticUserProfile, Tier,
)
from ..domain.retrieval import Hit, ProbeResult, score_retrieval

SUITE = "retrieval"
SEARCH_DEPTH = 10  # deepest metric asked for (recall@10, nDCG@10)

# How a Ledger Fact becomes a stored memory item. The Ledger has no concept *names* (the live
# extractor writes them), so the name is a measurement choice recorded in the Run Fingerprint.
class Rendering(StrEnum):
    """Concept-naming rule (a measurement choice; each value is its own comparability line)."""
    FIRST_KEY_TERM = "concept-name=first-key-term"  # product format "name: description", name = key_terms[0]
    BARE = "concept-name=none-in-embedding"  # sentence alone is embedded; the name is stored but not embedded


RENDERING = Rendering.FIRST_KEY_TERM.value


@dataclass(frozen=True)
class MemoryRecord:
    """A Ledger Fact in the form the product stores it: an Episode (a dated event, embedded from its
    bare summary) or a Concept (embedded from "name: description", TR-601)."""
    fact_id: str
    memory_type: str  # "episode" | "concept"
    text: str  # episode summary / concept description
    name: str | None  # concepts only
    happened_on: date | None  # episodes only
    language: EvalLanguage
    name_in_embedding: bool = True  # False: embed the description alone (Rendering.BARE)


def render_memory_records(profile: SyntheticUserProfile, ledger: Ledger,
                          rendering: Rendering = Rendering.FIRST_KEY_TERM) -> list[MemoryRecord]:
    """Event → Episode; knowledge and attribute → Concept (PLAN: attributes have no defined home yet)."""
    return [render_fact(profile.language, f, f.text, rendering) for f in ledger.facts]


def render_fact(language: EvalLanguage, fact: LedgerFact, text: str, rendering: Rendering = Rendering.FIRST_KEY_TERM,
                ) -> MemoryRecord:
    """`text` is the fact's sentence or one of its paraphrases (the merge sweep embeds both)."""
    if fact.kind is FactKind.EVENT:
        assert fact.happened_on is not None
        return MemoryRecord(fact.fact_id, "episode", text, None, fact.happened_on, language)
    name = fact.key_terms[0] if fact.key_terms else fact.domain
    return MemoryRecord(fact.fact_id, "concept", text, name, None, language, rendering is Rendering.FIRST_KEY_TERM)


@dataclass(frozen=True)
class RetrievalQuery:
    query_id: str
    text: str


@dataclass(frozen=True)
class SutDescription:
    """What the SUT says about itself — goes into the Run Fingerprint."""
    facts: Mapping[str, str]  # e.g. embedding model, device, index type
    min_recall_similarity: float  # the production recall cut-off (FR-309)


class RetrievalSut(Protocol):
    """A freshly provisioned, empty memory store plus the product's real embedding + search."""

    def describe(self) -> SutDescription: ...

    def index(self, records: Sequence[MemoryRecord]) -> None: ...

    def embed_passages(self, records: Sequence[MemoryRecord]) -> list[list[float]]:
        """The vectors the upsert path would store for these records (TR-601); nothing is written."""
        ...

    def search(self, queries: Sequence[RetrievalQuery], top_n: int, path: str = "planner",
               ef_search: int | None = None) -> list[list[Hit]]:
        """Per query, the `top_n` nearest stored items (best first) as fact ids. `path`: "planner" (the store
        decides, as in production), "index" (force the approximate index) or "exact" (brute-force scan, the
        reference the index is judged against, S1.8)."""
        ...

    def plan(self, path: str) -> str:
        """Which access path the store takes for a search under `path` (evidence for the Run Fingerprint)."""
        ...


SutFactory = Callable[[], ContextManager[RetrievalSut]]


@dataclass(frozen=True)
class RetrievalOutcome:
    """Per-Probe results of one pass over one Profile; kept so a sweep can re-score without re-embedding."""
    profile_id: UUID
    results: tuple[ProbeResult, ...]


class ExecuteRetrievalSuite:
    def __init__(self, sut_factory: SutFactory, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._sut_factory = sut_factory
        self._clock = clock

    def outcome_for_profile(self, profile: SyntheticUserProfile, ledger: Ledger, probes: Sequence[Probe],
                            rendering: Rendering = Rendering.FIRST_KEY_TERM) -> tuple[RetrievalOutcome, SutDescription]:
        """One Scratch Schema per Profile: each person's memories are searched alone, as in production."""
        records = render_memory_records(profile, ledger, rendering)
        known = {r.fact_id for r in records}
        for p in probes:
            unknown = (set(p.expected_fact_ids) | set(p.hard_negative_fact_ids)) - known
            if unknown:
                raise ValueError(f"probe {p.probe_id} cites facts missing from the Ledger: {sorted(unknown)}")
        with self._sut_factory() as sut:
            sut.index(records)
            hits = sut.search([RetrievalQuery(p.probe_id, p.text) for p in probes], SEARCH_DEPTH)
            description = sut.describe()
        if len(hits) != len(probes):
            raise RuntimeError(f"SUT answered {len(hits)} queries for {len(probes)} probes")
        return (RetrievalOutcome(profile.profile_id, tuple(ProbeResult(p, tuple(h)) for p, h in zip(probes, hits))),
                description)

    def run(
        self, *, language: EvalLanguage, corpus_version: str, profiles: Sequence[tuple[SyntheticUserProfile, Ledger, Sequence[Probe]]],
        git_sha: str | None = None, extra: Mapping[str, str] | None = None, rendering: Rendering = Rendering.FIRST_KEY_TERM,
    ) -> tuple[Run, list[RetrievalOutcome]]:
        """One Run = one Eval Language: both Archetypes' Profiles pooled (results are never averaged across languages)."""
        started = self._clock()
        outcomes: list[RetrievalOutcome] = []
        description: SutDescription | None = None
        for profile, ledger, probes in profiles:
            if profile.language is not language:
                raise ValueError(f"{profile.display_name} is {profile.language}, run is {language}")
            outcome, description = self.outcome_for_profile(profile, ledger, probes, rendering)
            outcomes.append(outcome)
        if description is None:
            raise ValueError("no profiles to run")
        scores = score_retrieval([r for o in outcomes for r in o.results], description.min_recall_similarity)
        fingerprint = RunFingerprint(
            git_sha=git_sha or current_git_sha(),
            sut=dict(description.facts),
            corpus_version=corpus_version,
            language=language,
            extra={"rendering": rendering.value, "cutoff": f"{description.min_recall_similarity}", **(extra or {})},
        )
        run = Run(uuid4(), SUITE, Tier.T0, fingerprint, scores, started, self._clock())
        return run, outcomes


def current_git_sha() -> str:
    try:
        here = Path(__file__).resolve().parent
        sha = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=here, capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=here, capture_output=True, text=True,
                               check=True).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def score_lookup(scores: Sequence[Score]) -> dict[str, Score]:
    return {s.metric: s for s in scores}
