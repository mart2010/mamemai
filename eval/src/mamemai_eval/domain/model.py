# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Eval domain: ground truth (Profile, Ledger), dialogs (Scenario, Dialog), measuring
(Probe, Run, Score). Pure — no I/O. Vocabulary: docs/GLOSSARY.md."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class EvalLanguage(StrEnum):
    EN = "en"
    FR = "fr"


class SpeechArchetype(StrEnum):
    """How a Synthetic User Profile talks (targets in domain/style.py)."""
    HESITANT = "hesitant"
    FLUENT = "fluent"


class FactKind(StrEnum):
    """What a Ledger Fact IS in the user's life (not how the product stores it).

    The live extractor yields only episodes (dated personal events) and concepts (knowledge the
    user learned or discussed); it never yields procedures (FR-307) and the spec names no home
    for stable personal facts. So scoring maps EVENT → episode, KNOWLEDGE → concept, and ATTRIBUTE →
    "wherever the product puts it", measured and reported, not pass/fail (see PLAN findings)."""
    EVENT = "event"
    ATTRIBUTE = "attribute"
    KNOWLEDGE = "knowledge"

    @property
    def expected_memory_type(self) -> str | None:
        return {"event": "episode", "knowledge": "concept"}.get(self.value)


class ProbeKind(StrEnum):
    SINGLE_FACT = "single_fact"
    AGGREGATION = "aggregation"
    UPDATE = "update"
    ABSTENTION = "abstention"
    CROSS_LINGUAL = "cross_lingual"
    HARD_NEGATIVE = "hard_negative"


class ScenarioKind(StrEnum):
    """What a Dialog is *for*. Only DISCLOSURE Scenarios carry Ledger Facts; the other two are the
    negative cases of the worthiness gate (spec Worthiness: gates episodes, and explicitly excludes
    talk about the assistant's own operation)."""
    DISCLOSURE = "disclosure"  # the user tells the assistant about their life (facts from the Ledger)
    SMALL_TALK = "small_talk"  # phatic chat: greetings, banter; no personal event, nothing to remember
    ASSISTANT_OPERATION = "assistant_operation"  # settings, voice, "how do you remember things"


class Tier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SyntheticUserProfile:
    profile_id: UUID
    language: EvalLanguage
    display_name: str
    seed: int
    biography: str
    archetype: SpeechArchetype
    speech_style: str  # free-text colour on top of the archetype's measurable Style Target


@dataclass(frozen=True)
class LedgerFact:
    """One atomic, checkable statement. `supersedes` makes this fact a Fact Revision of an
    earlier one (the user moved, changed job, changed their mind). `domain` is a Life Domain for
    events/attributes and a Knowledge Domain for knowledge."""
    fact_id: str
    kind: FactKind
    text: str
    revealed_in: int  # Scenario index in which the user first states it
    domain: str
    happened_on: date | None = None  # required for EVENT, forbidden otherwise
    paraphrases: tuple[str, ...] = ()
    supersedes: str | None = None
    # 1–3 distinctive strings any faithful restatement must contain ("Quimper", "Pimprenelle",
    # "3,8 centimètres"): lets presence/staleness be checked deterministically, with a Judge only for
    # the ambiguous cases. Each must literally occur in `text`.
    key_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.fact_id or not self.text.strip():
            raise ValueError("LedgerFact needs a fact_id and non-empty text")
        if self.revealed_in < 0:
            raise ValueError(f"{self.fact_id}: revealed_in must be >= 0")
        if (self.kind is FactKind.EVENT) != (self.happened_on is not None):
            raise ValueError(f"{self.fact_id}: happened_on is required for events and only for events")
        if self.supersedes is not None and self.kind is not FactKind.ATTRIBUTE:
            raise ValueError(f"{self.fact_id}: only attributes can be revised")
        missing = [t for t in self.key_terms if not t.strip() or t.casefold() not in self.text.casefold()]
        if missing:
            raise ValueError(f"{self.fact_id}: key_terms not found in text: {missing}")


@dataclass(frozen=True)
class Ledger:
    """The authoritative fact list of one Profile (EV-INV-3). Consistency is checked at
    construction, so a generated Ledger that is inconsistent cannot be frozen.
    `scenario_dates[i]` is the fictional "today" of Scenario i."""
    profile_id: UUID
    facts: tuple[LedgerFact, ...]
    scenario_dates: tuple[date, ...] = ()

    def __post_init__(self) -> None:
        if list(self.scenario_dates) != sorted(set(self.scenario_dates)):
            raise ValueError("scenario_dates must be strictly ascending")
        by_id: dict[str, LedgerFact] = {}
        for f in self.facts:
            if f.fact_id in by_id:
                raise ValueError(f"duplicate fact_id {f.fact_id!r}")
            by_id[f.fact_id] = f
            if self.scenario_dates:
                if f.revealed_in >= len(self.scenario_dates):
                    raise ValueError(f"{f.fact_id}: revealed_in {f.revealed_in} has no scenario date")
                if f.happened_on and f.happened_on > self.scenario_dates[f.revealed_in]:
                    raise ValueError(f"{f.fact_id}: event happens after the scenario that reports it")
        superseded: set[str] = set()
        for f in self.facts:
            if f.supersedes is None:
                continue
            old = by_id.get(f.supersedes)
            if old is None:
                raise ValueError(f"{f.fact_id} supersedes unknown fact {f.supersedes!r}")
            if old.revealed_in >= f.revealed_in:
                raise ValueError(f"{f.fact_id} must be revealed after the fact it supersedes ({old.fact_id})")
            if f.supersedes in superseded:
                raise ValueError(f"{f.supersedes!r} is superseded twice")
            superseded.add(f.supersedes)

    def get(self, fact_id: str) -> LedgerFact:
        for f in self.facts:
            if f.fact_id == fact_id:
                return f
        raise KeyError(fact_id)

    def current_facts(self) -> tuple[LedgerFact, ...]:
        """Facts not superseded by a later revision — what the assistant *should* hold now."""
        superseded = {f.supersedes for f in self.facts if f.supersedes}
        return tuple(f for f in self.facts if f.fact_id not in superseded)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Probe:
    probe_id: str
    kind: ProbeKind
    language: EvalLanguage
    text: str
    expected_fact_ids: tuple[str, ...] = ()
    hard_negative_fact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind is ProbeKind.ABSTENTION:
            if self.expected_fact_ids:
                raise ValueError("an abstention Probe has no expected facts (the user never said it)")
        elif not self.expected_fact_ids:
            raise ValueError(f"{self.kind} Probe {self.probe_id!r} needs expected_fact_ids")


@dataclass(frozen=True)
class Scenario:
    """The plan for one Dialog. `index` is the Ledger's scenario index for a DISCLOSURE (its facts have
    `revealed_in == index`); the fact-free kinds are numbered after the last disclosure and dated after it.

    - `omits`: facts from LATER Scenarios in domains this one touches that are ALREADY true at `on_date`
      (an attribute, knowledge, or an event that has happened) — the user just does not tell them yet (a
      trap for an extractor or assistant that fills in what was never said). Events still to come and
      revisions (the old value is still true) are not omittable.
    - `target_turns`: total Turns (user + assistant, spec term), a writing target not a constraint.
    - `topic`: English instruction for the writer of a fact-free Dialog; empty for a DISCLOSURE."""
    scenario_id: str
    profile_id: UUID
    index: int
    language: EvalLanguage
    reveals: tuple[str, ...]
    omits: tuple[str, ...] = ()
    probes: tuple[Probe, ...] = ()
    target_turns: int = 12
    mood: str = "neutral"
    kind: ScenarioKind = ScenarioKind.DISCLOSURE
    on_date: date | None = None  # the fictional "today"; Dialog timestamps derive from it
    topic: str = ""

    def __post_init__(self) -> None:
        if self.kind is not ScenarioKind.DISCLOSURE and (self.reveals or self.omits):
            raise ValueError(f"a {self.kind} Scenario reveals and omits nothing")
        if self.kind is ScenarioKind.DISCLOSURE and not self.reveals:
            raise ValueError("a disclosure Scenario must reveal at least one fact")
        if self.target_turns < 2:
            raise ValueError("target_turns must be >= 2")

    def check_against(self, ledger: Ledger) -> None:
        """Raises if the agenda contradicts the Ledger (revealing a fact before/after its
        scheduled Scenario, both revealing and omitting one, or omitting a fact already told)."""
        for fid in self.reveals:
            if ledger.get(fid).revealed_in != self.index:
                raise ValueError(f"{fid} is scheduled for scenario {ledger.get(fid).revealed_in}, not {self.index}")
        both = set(self.reveals) & set(self.omits)
        if both:
            raise ValueError(f"facts both revealed and omitted: {sorted(both)}")
        for fid in self.omits:
            if ledger.get(fid).revealed_in <= self.index:
                raise ValueError(f"{fid} cannot be omitted: it is revealed in scenario {ledger.get(fid).revealed_in}, "
                                 f"not after {self.index}")
        for fid in self.omits:
            f = ledger.get(fid)
            if f.supersedes is not None:
                raise ValueError(f"{fid} cannot be omitted: it is a revision, and the old value is still true now")
            if f.happened_on is not None and self.on_date is not None and f.happened_on > self.on_date:
                raise ValueError(f"{fid} cannot be omitted: it happens on {f.happened_on}, after this Scenario")
        if self.on_date is not None and self.kind is ScenarioKind.DISCLOSURE and ledger.scenario_dates:
            if ledger.scenario_dates[self.index] != self.on_date:
                raise ValueError(f"on_date {self.on_date} differs from the Ledger's {ledger.scenario_dates[self.index]}")

    def revisions(self, ledger: Ledger) -> tuple[tuple[str, str], ...]:
        """(new_fact_id, superseded_fact_id) for each Fact Revision the user states in this Scenario."""
        return tuple((f, ledger.get(f).supersedes) for f in self.reveals if ledger.get(f).supersedes)  # type: ignore[misc]

    def expects_episodes(self, ledger: Ledger) -> bool:
        """The worthiness label: the gate guards episodes, so a Dialog is worthy iff the user tells at least
        one dated event. (Attributes and knowledge may still yield concepts — worthiness does not gate those.)"""
        return any(ledger.get(f).kind is FactKind.EVENT for f in self.reveals)


@dataclass(frozen=True)
class DialogTurn:
    speaker: str  # "user" | "assistant"
    text: str

    def __post_init__(self) -> None:
        if self.speaker not in ("user", "assistant"):
            raise ValueError(f"unknown speaker {self.speaker!r}")


@dataclass(frozen=True)
class Dialog:
    scenario_id: str
    turns: tuple[DialogTurn, ...]
    frozen: bool = True


@dataclass(frozen=True)
class Corpus:
    """An immutable, versioned snapshot of Profiles + Ledgers + Probes (EV-INV-5). `digest` is the
    sha256 of its canonical JSONL content: a Run cites the version, and the digest proves it is unchanged."""
    version: str
    digest: str
    created_at: datetime
    generated_with: str  # pinned Reference LLM model id that wrote the content
    profile_ids: tuple[UUID, ...]
    counts: Mapping[str, int]
    notes: str = ""


# ---------------------------------------------------------------------------
# Measuring
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Score:
    """Never a bare pass/fail (EV-INV-7): value, unit and sample size travel together."""
    metric: str
    value: float
    n: int
    unit: str = "ratio"

    def __post_init__(self) -> None:
        if self.n < 0:
            raise ValueError("Score.n must be >= 0")


@dataclass(frozen=True)
class RunFingerprint:
    """Everything needed to reproduce and compare a Run."""
    git_sha: str
    sut: Mapping[str, str]  # e.g. {"llm": "llama3.1:8b@sha256:…", "embedding": "e5-large"}
    corpus_version: str
    language: EvalLanguage
    reference_llm: str | None = None  # pinned model id; None if the Suite never called it
    rubric_hash: str | None = None  # set iff the Suite uses a Judge
    seeds: tuple[int, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        payload = {
            "git_sha": self.git_sha, "sut": dict(sorted(self.sut.items())), "corpus_version": self.corpus_version,
            "language": self.language.value, "reference_llm": self.reference_llm, "rubric_hash": self.rubric_hash,
            "seeds": list(self.seeds), "extra": dict(sorted(self.extra.items())),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Run:
    run_id: UUID
    suite: str
    tier: Tier
    fingerprint: RunFingerprint
    scores: tuple[Score, ...]
    started_at: datetime
    finished_at: datetime
    comparable: bool = True  # False for smoke/fallback configurations (EV-INV-10)


def comparability_problem(a: Run, b: Run) -> str | None:
    """Domain service for EV-INV-10: why `compare` must refuse these two Runs, or None.

    Differences in the SUT, thresholds or git SHA are the *point* of a comparison and are
    allowed; differences in the instrument (Reference LLM, Rubric, Corpus, language, Suite)
    make the Scores incomparable."""
    if not (a.comparable and b.comparable):
        return "a Run is tagged non-comparable"
    if a.suite != b.suite:
        return f"different Suites ({a.suite} vs {b.suite})"
    fa, fb = a.fingerprint, b.fingerprint
    if fa.language != fb.language:
        return "different Eval Languages (results are never averaged across languages)"
    if fa.corpus_version != fb.corpus_version:
        return f"different Corpus versions ({fa.corpus_version} vs {fb.corpus_version})"
    if fa.reference_llm != fb.reference_llm:
        return f"different Reference LLMs ({fa.reference_llm} vs {fb.reference_llm})"
    if fa.rubric_hash != fb.rubric_hash:
        return "different Rubrics"
    return None
