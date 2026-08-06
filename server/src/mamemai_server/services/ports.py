# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Protocol
from uuid import UUID

from ..domain.events import ConversationBoundaryType
from ..domain.model import (
    AssistantPersona,
    Conversation,
    Concept,
    Directive,
    EngagementLevel,
    Episode,
    Language,
    MemoryBrief,
    MemoryType,
    Procedure,
    Speaker,
    Turn,
    User,
)

type MemoryItem = Episode | Concept | Procedure

# A draft is a not-yet-persisted Concept/Procedure (id=None) destined for the existing
# upsert/consolidation pipeline — the same shape PersonaEnrichmentPort.propose_items and
# the future InstallPersonaBundle (Phase 11) both emit. Episodes are never proposed.
type MemoryItemDraft = Concept | Procedure


@dataclass(frozen=True)
class SessionLine:
    """One parsed line from a JSONL session log file."""
    ts: datetime
    speaker: Speaker | None = None
    content: str | None = None
    language: Language | None = None
    marker: ConversationBoundaryType | None = None
    is_session_closed: bool = False
    clean_exit: bool = False
    persona_id: UUID | None = None


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class ExtractionResult:
    episodes: list[Episode]
    concepts: list[Concept]


@dataclass(frozen=True)
class SessionInfo:
    session_id: UUID
    ended_at: datetime
    clean_exit: bool


class STTService(Protocol):
    def transcribe(self, audio: bytes) -> tuple[str, Language]: ...


class LLMService(Protocol):
    def complete(self, messages: list[Message], system_prompt: str) -> AsyncIterator[str]: ...


class TTSService(Protocol):
    def synthesise(self, text: str, voice: str, speed: float = 1.0) -> bytes: ...


class EmbeddingService(Protocol):
    """Two methods, not one, because `intfloat/multilingual-e5-large` is asymmetric:
    it was trained with a `query: ` prefix on the text being searched *with* and a
    `passage: ` prefix on the text being searched *for*, and the two are not
    interchangeable (TR-509).

    Which to call is a property of the role the text plays, not of its content:
      - `embed_query`   — text being matched *against* stored memory: a user turn at the
                          recall gate, a `TriggerRecall` query, or a persona strategy
                          matching a topic it wants material for.
      - `embed_passage` — anything stored, or compared *against* stored content.
                          Upsert's insert-vs-merge search uses this on both sides: that
                          comparison is document-to-document, so a single prefix keeps
                          both halves on one scale, and it is the scale the merge and
                          disambiguate thresholds were derived against.

    Measured 2026-08-04: dropping the prefixes entirely (what shipped before) let an
    absent subject score 0.8536 against a present one at 0.8368 — no threshold separates
    those. With the prefixes the absent ceiling falls to 0.8341 and the floor clears it.
    """

    def embed_query(self, text: str) -> list[float]: ...

    def embed_passage(self, text: str) -> list[float]: ...


class LanguageDetector(Protocol):
    def detect(self, text: str, candidates: tuple[str, ...]) -> str | None:
        """Best-guess dominant language of `text`, restricted to `candidates` (IETF
        codes). Returns None when `text` is too short/ambiguous to trust a guess —
        callers should keep whatever language was already in effect rather than
        force a switch on a low-confidence call."""
        ...


class UserRepository(Protocol):
    def get(self) -> User | None: ...
    def save(self, user: User) -> None: ...


class SessionLogReader(Protocol):
    """Reads session metadata and turn tail from flat JSONL log files."""
    def get_previous(self) -> SessionInfo | None: ...
    def read_tail(self, session_id: UUID, max_turns: int) -> list[Turn]: ...


class ConversationRepository(Protocol):
    def save_new(self, conversation: Conversation, session_id: UUID) -> int: ...
    def save_consolidation(self, conversation: Conversation) -> None: ...
    def get_unconsolidated(self) -> list[Conversation]: ...
    def is_session_persisted(self, session_id: UUID) -> bool: ...
    def get_last_open_id(self) -> int | None: ...
    def extend_conversation(self, conversation_id: int, session_id: UUID, turns: list[Turn], ended_at: datetime | None) -> None: ...


class SessionReplayReader(Protocol):
    """Walks session log files newest-first, stops at the first already-persisted
    session (monotonic invariant), and returns unprocessed sessions oldest-first."""
    def get_unprocessed(
        self,
        is_persisted: Callable[[UUID], bool],
    ) -> list[tuple[UUID, list[SessionLine]]]: ...


class MemoryRepository(Protocol):
    def upsert_episode(self, episode: Episode) -> int: ...
    def upsert_concept(self, concept: Concept) -> int: ...
    def upsert_procedure(self, procedure: Procedure) -> int: ...
    def update_persona_state(self, memory_type: MemoryType, item_id: int, persona_state: dict) -> None:
        """Persists an assessment strategy's opaque state byte-for-byte — the only write
        path to `persona_state` (single-writer contract; upserts never touch the column)."""
        ...
    def list_items(
        self,
        persona_id: UUID | None,
        memory_types: tuple[MemoryType, ...],
        category: str | None = None,
        engagement_levels: tuple[EngagementLevel, ...] | None = None,
        limit: int | None = None,
    ) -> list[MemoryItem]:
        """Non-similarity listing for selection strategies, ordered by ascending id within
        each memory type — insertion order is the curriculum-order contract for bundle
        content (UNSEEN tiebreak). Concepts/procedures only: episodes carry no persona
        scope, category, or engagement (implementations raise ValueError) — see
        `list_episodes`.

        `persona_id=None` lists across every persona, for a persona that declares
        `reads_across_personas` (FR-303). Read-only: authoring, upsert scoping and INV-9's
        cascade are untouched, so nothing reached this way can be rewritten or deleted by
        the reader. Same None-means-unscoped convention `search` already uses."""
        ...

    def list_episodes(self, limit: int | None = None) -> list[Episode]:
        """Episodes most recent first (`happened_at` descending, `created_at` breaking
        ties). Unscoped by nature rather than by permission — episodes carry no persona
        (INV-10), which is what makes them the one memory type every persona could
        already reach.

        Separate from `list_items` because the shape genuinely differs: no persona scope,
        no category, no engagement level, and recency rather than insertion order is the
        meaningful sort. Added 2026-08-02 — until then episodes could only be reached by
        similarity `search`, which cannot answer "what happened lately"."""
        ...
    def search(
        self,
        embedding: list[float],
        memory_types: tuple[MemoryType, ...],
        top_n: int,
        persona_id: UUID | None = None,
    ) -> list[tuple[float, MemoryItem]]: ...  # float is cosine similarity in [0, 1]


class DirectiveRepository(Protocol):
    """Directives (FR-207) are first-class, not memory content — a dedicated port/table,
    not a `MemoryRepository` concern, so there is no `WHERE ... IS NULL`-style filter
    anywhere else in the codebase to remember."""
    def list_all(self) -> list[Directive]:
        """Every Directive, any target — small, stable set; fetched once per session
        (`WorkingMemory.directives`, same fetch-once posture as `memory_brief`)."""
        ...
    def upsert(self, directive: Directive) -> int:
        """`id=None` inserts; an id updates in place. `PersonaDirectiveSync` only ever
        inserts today (directives aren't revised in place yet) — update support exists
        for a future editable-trigger feature to reuse without another migration."""
        ...
    def delete(self, directive_id: int) -> None:
        """Remove one Directive. Needed because a row outlives the configuration that
        generated it: `ensure_language_directives` prunes phrasings a later rule no
        longer generates, which cannot be done by simply not writing them. Persona-owned
        directives never come through here — INV-9's cascade removes those."""
        ...


# ---------------------------------------------------------------------------
# Persona extension ports — persona-agnostic contracts; each persona's strategy
# implementation (Infrastructure layer) owns all persona-specific vocabulary.
# GA registers no selection/enrichment/assessment strategies — those three are
# optional per persona. RecallGate (below) is the exception: every persona has
# one, GA included, falling back to a default rather than a no-op.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SelectedItem:
    """`context` is free text composed by the selection strategy (e.g. an episode anchor
    or elicitation hint) — injected verbatim, never interpreted by generic code."""
    item: MemoryItem
    context: str | None = None


@dataclass(frozen=True)
class ItemAssessment:
    """`persona_state` is persisted byte-for-byte to the item's opaque slot.
    `memory_type` disambiguates the target table (concepts and procedures have
    independent id sequences)."""
    item_id: int
    memory_type: MemoryType
    persona_state: dict


@dataclass(frozen=True)
class SelectionPacing:
    """How generic code serves a persona's selected items — declared by the persona,
    applied by the pipeline.

    Until 2026-08-02 these two rules were hard-coded in `ProcessTurn`, which meant one
    persona's sensible UX choices were every persona's law. Both defaults below are that
    original behaviour, so a strategy declaring nothing is unchanged.

    Generic code still owns the *mechanics* — when to call, where the batch lives, one
    item per turn, how it is rendered into the prompt. This is deliberately a small
    declared policy rather than handing the persona the turn: the wider inversion is
    docs/BRIEF_persona_contract.md's job, and designing it from a single not-yet-built
    persona is exactly the failure mode that note warns against.
    """

    # Re-query when the batch runs dry. False suits a persona whose batch is a planned
    # set, where running out signals the planned work is done rather than a need for
    # more; True suits one whose sessions are short and frequent enough to exhaust a
    # batch routinely.
    refetch_when_exhausted: bool = False

    # Bring content on the very turn a directive switches TO this persona. False leaves
    # that turn free to greet, which is the gentler reading of "the user just asked to
    # switch"; True suits a persona whose session opening *is* its first piece of
    # content, and which would otherwise burn a turn arriving empty-handed. Never
    # applies to a *blocked* switch: that turn activates nobody, it explains that a
    # persona is unavailable.
    include_activation_turn: bool = False


DEFAULT_SELECTION_PACING = SelectionPacing()


class PersonaSelectionPort(Protocol):
    """Live hook — proactive, persona-driven selection (e.g. spaced repetition), distinct
    from utterance-triggered RAG recall. Fetched lazily on the first turn the persona is
    active (sessions start on GA; strategy-bearing personas arrive via mid-session
    switch). `focus` is the user's expressed wish for the session ("just review old
    vocabulary today"), passed verbatim and interpreted only by the strategy; None means
    the default learning path. **Always None today** — the [FOCUS: ...] response marker
    that fed it was retired (TR-304) and user-turn detection is deferred; the parameter
    and every strategy that reads it are kept intact for that replacement.

    `pacing` is optional: a strategy that omits it gets DEFAULT_SELECTION_PACING, so
    absence stays the default behaviour rather than boilerplate every strategy must
    repeat."""

    pacing: SelectionPacing

    def select_items(
        self,
        persona_id: UUID,
        focus: str | None = None,
        limit: int = 10,
    ) -> Sequence[SelectedItem]: ...


class PersonaEnrichmentPort(Protocol):
    """Offline hook — proposes new memory item drafts (fed into the existing upsert
    pipeline). Exclusions are computed internally by the strategy via the repositories,
    not passed in — which items matter for exclusion is persona-specific knowledge."""
    def propose_items(self, persona_id: UUID) -> Sequence[MemoryItemDraft]: ...


class PersonaAssessmentPort(Protocol):
    """Offline hook — updates existing items' persona_state from conversational evidence.
    Dispatched by the consolidation pipeline AFTER upsert, so newly inserted items have
    ids and their first exposure is assessable."""
    def assess_items(
        self,
        persona_id: UUID,
        conversation: Conversation,
        touched_items: Sequence[MemoryItem],
    ) -> Sequence[ItemAssessment]: ...


class RecallGate(Protocol):
    """Live hook (FR-309/TR-314) — persona-scoped policy on whether a turn's recall
    search is worth running at all, replacing a per-turn LLM classification call
    (the old RecallIntentDetector). Unlike the three ports above, every persona gets
    one: an unregistered persona (e.g. GA) falls back to a DefaultRecallGate rather
    than a no-op, since recall applies to ordinary conversation, not just advanced
    personas opting in to extra behaviour."""

    def should_embed(self, text: str) -> bool:
        """Called first, before any embedding is computed — a cheap, text-only
        short-circuit for utterances unlikely to need memory context at all (e.g. a
        one-word reply to the GA). A persona for which short replies are meaningful
        content in their own right overrides this to always return True."""
        ...

    def is_relevant(self, similarity: float) -> bool:
        """Called once per row the search returned, with that row's cosine similarity
        to the turn's embedding. False drops the row.

        A floor is required, not optional: `MemoryRepository.search` orders by distance
        and limits, with no floor of its own, so against a non-empty database it always
        returns `top_n` rows however unrelated they are. Without this, "recall found
        nothing" is not a state the system can be in, and the honesty branch that depends
        on it (TR-303) is unreachable code. A persona whose memories are dense and
        near-synonymous may want a stricter floor than the default."""
        ...

    def should_search(self, max_similarity_to_prior_searches: float | None) -> bool:
        """Called only when should_embed() returned True.
        `max_similarity_to_prior_searches` is the highest cosine similarity
        (domain.model.cosine_similarity) between this turn's embedding and the
        embedding of *any* utterance that actually triggered a real DB search this
        session for the active persona — not just the immediately preceding one.
        None if no search has happened yet (always search then). Comparing against
        the *whole* session's search history, not only the last search, is correct
        specifically because nothing new can enter long-term memory mid-session
        (INV-1): the set of insertable content is frozen for the whole conversation,
        so a repeat of any earlier query would deterministically return the same
        results again. A high similarity means the best-matching prior search's
        cached results are still the best available answer, so a fresh DB round trip
        is skipped in favour of reusing them."""
        ...


# ---------------------------------------------------------------------------
# Persona bundle port — Phase 11. The bundle FILE FORMAT is the contract between
# mamemai and any external authoring tool (see docs/BRIEF_phase11_bundle_format.md):
# mamemai owns this envelope schema, authors own all content vocabulary. These value
# objects are the parsed in-memory form of that format.
# ---------------------------------------------------------------------------

# The bundle-format contract version this mamemai release can read.
BUNDLE_FORMAT_VERSION = 1


class BundleFormatError(ValueError):
    """A bundle failed parse-and-reject validation (missing manifest, unsupported
    format_version, malformed lesson/item). Phase 11's only validation layer — the
    standalone `mamemai-bundle validate` CLI is a deferred non-goal."""


@dataclass(frozen=True)
class BundleItemSpec:
    """One [[items]] entry from a lesson file. Deliberately NOT a domain entity yet:
    persona_id is resolved by the installer (the persona may not exist until install),
    engagement_level is always forced to UNSEEN (a bundle cannot claim the user knows
    things), and the embedding is computed at install time."""
    memory_type: MemoryType  # CONCEPT or PROCEDURE only — bundles never contain episodes
    name: str
    description: str
    language: Language  # language of first introduction (existing invariant)
    category: str | None = None  # persona-interpreted free text
    steps: tuple[str, ...] = ()  # procedures only


@dataclass(frozen=True)
class BundleLesson:
    """A lesson is ordering only: filename sort order defines curriculum order, which
    survives import as ascending SERIAL id (insertion order is the contract). No other
    lesson structure is persisted; `title` inside the file is authoring metadata."""
    filename: str
    items: tuple[BundleItemSpec, ...]


@dataclass(frozen=True)
class BundlePersonaDefinition:
    """The optional [persona] table — required only when the persona may not exist yet;
    content-only bundles (e.g. cognate accelerators) omit it. `voices` MAY omit the
    "default" role: the installer derives it from User.primary_language (the native-
    teacher anchor is pair-dependent, so the bundle stays pair-independent)."""
    name: str
    system_prompt: str
    # Empty = no language restriction, as on AssistantPersona itself.
    languages: tuple[Language, ...]
    # None = derive from User.primary_language at install, the same pair-independence
    # rule that already governs voices["default"]. A bundle that is not about a
    # particular language cannot know which one its user speaks.
    response_language: Language | None
    voices: dict[str, str]
    settings: dict | None = None  # copied VERBATIM to AssistantPersona.settings
    # Strategy-set name (a bare identifier) resolved against the composition root's
    # registry; an unknown name degrades gracefully (warning, no strategies bound).
    strategy: str | None = None
    # Declares whether this persona's conversation records real events (FR-407). A
    # bundle whose sessions are rehearsal — drills, role-play, exercises — sets it
    # False. Defaults True: absent means the ordinary case, conversation is life.
    extracts_episodes: bool = True
    # Declares that this persona practises over the user's whole store rather than only
    # its own content (FR-303). Read-only; authoring and INV-9's cascade are unaffected.
    # Defaults False: a persona sees its own scope unless it says otherwise.
    reads_across_personas: bool = False
    # Declares that this persona needs each user turn tagged with the language it was
    # spoken in (FR-114). Defaults False: a persona whose job does not turn on that
    # distinction should not see it.
    sees_turn_language: bool = False


@dataclass(frozen=True)
class PersonaBundle:
    persona_key: str  # author-namespaced, e.g. "author/persona-name"
    name: str
    version: str
    author: str
    description: str
    # The manifest's [bundle] + [provenance] tables verbatim (JSON-safe) — persisted
    # as-is to the bundle_installs provenance log, never read by any code path.
    manifest: dict
    persona: BundlePersonaDefinition | None
    lessons: tuple[BundleLesson, ...]  # already in lesson-filename sort order


class PersonaBundleSource(Protocol):
    def load(self, path: Path) -> PersonaBundle:
        """Parses and validates a bundle directory; raises BundleFormatError on any
        malformation. Read-only — mamemai never writes bundles."""
        ...


@dataclass(frozen=True)
class BundleInstallRecord:
    """One append-only provenance row per install run. Nothing reads it in any code
    path (same rationale as episodes.origin_conversation_id); deliberately NOT a
    reinstall guard — reinstalls are idempotent by merge."""
    persona_key: str
    bundle_name: str
    bundle_version: str
    bundle_author: str
    installed_at: datetime
    items_inserted: int
    items_merged: int
    manifest: dict  # the bundle's [bundle] + [provenance] tables, verbatim


class BundleInstallLog(Protocol):
    def append(self, record: BundleInstallRecord) -> None: ...


class DisambiguationEvaluator(Protocol):
    def is_same(self, existing: MemoryItem, candidate: MemoryItem) -> bool: ...


class MemorySynthesizer(Protocol):
    def synthesize_episode(self, existing_summary: str, new_summary: str) -> str: ...
    def synthesize_concept(self, existing: Concept, new_description: str) -> str: ...
    def synthesize_procedure(self, existing: Procedure, new_description: str, new_steps: list[str]) -> tuple[str, list[str]]: ...


class PersonaRepository(Protocol):
    def get(self, persona_id: UUID) -> AssistantPersona | None: ...
    def get_by_key(self, persona_key: str) -> AssistantPersona | None: ...
    def list_all(self) -> list[AssistantPersona]: ...
    def save(self, persona: AssistantPersona) -> None: ...
    def delete(self, persona_id: UUID) -> None: ...


class MemoryBriefRepository(Protocol):
    def get(self) -> MemoryBrief | None: ...
    def save(self, brief: MemoryBrief) -> None: ...


class TurnLogger(Protocol):
    def append(self, session_id: UUID, turn: Turn, marker: ConversationBoundaryType | None = None, persona_id: UUID | None = None) -> None: ...
    def close(self, session_id: UUID, ended_at: datetime, clean_exit: bool) -> None: ...


class ConsolidationExtractor(Protocol):
    def extract(
        self,
        conversation: Conversation,
        primary_language: Language | None = None,
        extract_episodes: bool = True,
    ) -> ExtractionResult:
        """`primary_language` drives the episode-summary language rule: Episode summaries
        are always written in the user's primary language regardless of conversation
        language (Episodes are persona-independent; months of conversing with a persona
        in another language must not rewrite the user's life story into that language).

        `extract_episodes=False` (set by ConsolidateMemory from the persona's own
        `extracts_episodes` declaration) skips asking for episodes at all: rehearsed material
        (role-play, drills) is not a record of real events, and no reliable after-the-fact
        judgment call was found for a small local model to separate a genuine story from a
        practised one — so the request is simply never made.

        Procedures are never requested from conversation at all (any persona): how-to
        knowledge belongs to authoring expertise (bundles), not something a live chat
        organically produces — FR-307."""
        ...


class UnitOfWork(Protocol):
    """Demarcates a transaction boundary around a group of repository writes, so a use
    case can commit or roll them back atomically without depending on infrastructure."""
    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None: ...


