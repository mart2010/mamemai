# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum, IntEnum
from uuid import UUID


# ---------------------------------------------------------------------------
# Value Objects & Enums
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Language:
    code: str  # IETF language tag, e.g. "en", "fr", "de", "zh-Hans"

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ValueError("Language code cannot be empty")


# Intersection of faster-whisper (~99 languages) and Kokoro (~9 languages).
# Kokoro is the limiting factor; this is the full set Mamemai supports.
SUPPORTED_LANGUAGES: list[Language] = [
    Language("en"), Language("fr"), Language("es"), Language("it"),
    Language("pt"), Language("ja"), Language("zh-cn"),
]


def resolve_installed_languages(installed_codes: tuple[str, ...] | list[str]) -> list[Language]:
    """The installed languages (FR-705): the wizard-selected codes intersected with
    SUPPORTED_LANGUAGES, in SUPPORTED_LANGUAGES order. Empty codes (config predates
    the [languages] key) → everything supported. Unsupported codes are silently
    dropped here — callers that want to warn or fail compare the result themselves."""
    if not installed_codes:
        return list(SUPPORTED_LANGUAGES)
    return [lang for lang in SUPPORTED_LANGUAGES if lang.code in installed_codes]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure Python cosine similarity in [-1, 1] between two embedding vectors —
    used by RecallGate.should_search (FR-309/TR-314) to compare a turn's embedding
    against the last one that actually triggered a memory search this session,
    entirely in-process (no DB round trip; pgvector's own `<=>` operator is a
    separate, unrelated comparison against stored items). Does not assume
    L2-normalised input, unlike a plain dot product, so it stays correct against
    any EmbeddingService implementation, real or fake. Returns 0.0 for a
    zero-magnitude vector rather than dividing by zero."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def retention(days_since: float, half_life_days: float) -> float:
    """Exponential-decay retention estimate in (0, 1] — the fraction of an item still
    expected to be recallable `days_since` days after it was last practiced, halving
    every `half_life_days`. Pure arithmetic: `2 ** (-days_since / half_life_days)`.

    Generic on purpose. Any spaced-retrieval persona needs this curve, so the *formula*
    lives here — but nothing about how a persona stores or moves its half-life does.
    Where the inputs come from (which `persona_state` keys hold them), what grows or
    shrinks the half-life, and what an item with no usable state should rank as are all
    persona-owned policy, and standardising them here would invite generic code to read
    inside `persona_state` and violate INV-6. Generalise the calculation, not the data.

    A negative `days_since` (clock skew — practiced "in the future") is read as 0, i.e.
    fully retained, the same degenerate-input posture as `cosine_similarity` returning
    0.0 for a zero-magnitude vector. A non-positive `half_life_days` has no meaningful
    curve at all and raises rather than inventing one: deciding what an unusable SRS
    state ranks as is exactly the persona policy this function must not make."""
    if half_life_days <= 0:
        raise ValueError(f"half_life_days must be positive, got {half_life_days!r}")
    return 2.0 ** (-max(days_since, 0.0) / half_life_days)


class EngagementLevel(IntEnum):
    UNSEEN = 0
    MENTIONED = 1
    EXPLORED = 2
    INTEGRATED = 3


class MemoryType(Enum):
    EPISODE = "episode"
    CONCEPT = "concept"
    PROCEDURE = "procedure"


class Speaker(Enum):
    USER = "user"
    ASSISTANT = "assistant"



# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

GENERAL_ASSISTANT_ID = UUID("00000000-0000-0000-0000-000000000001")

# Every persona's `voices` map must carry this key. Other keys (a multi-speaker cast)
# are IETF language codes, persona-defined; generic code only ever reads
# DEFAULT_VOICE_ROLE by name — any other key is resolved by the language actually
# detected in a synthesized segment (see `_session_voice` in services/session.py),
# never by generic code branching on the key itself.
# A non-default key's value may be a "|"-separated rotation pool ("ef_dora|em_alex"),
# resolved to ONE voice per session by the live path — deliberate multi-voice exposure
# whose purpose is the owning persona's concern, not this layer's.
# The default key is the fixed anchor and must always be a single voice.
DEFAULT_VOICE_ROLE = "default"


def _validate_voices(voices: dict[str, str]) -> None:
    if DEFAULT_VOICE_ROLE not in voices:
        raise ValueError(f"voices must include the '{DEFAULT_VOICE_ROLE}' role")
    if "|" in voices[DEFAULT_VOICE_ROLE]:
        raise ValueError(
            f"the '{DEFAULT_VOICE_ROLE}' voice must be a single voice (the fixed anchor) — "
            "'|' rotation pools are only for non-default (language-code) keys"
        )


# kw_only: field order here mirrors the `personas` table's column order (timestamps
# last), which a plain @dataclass forbids — `created_at`/`updated_at` are mandatory and
# would follow defaulted fields, so the generated __init__ would put a non-default
# parameter after default ones (TypeError at import). Keyword-only lifts that ordering
# constraint entirely; every construction site already passes keywords, and persistence
# never depends on field order (postgres.py maps via the explicit _PERSONA_COLUMNS list).
@dataclass(kw_only=True)
class AssistantPersona:
    id: UUID
    name: str
    system_prompt: str
    # The session language pair(s): input languages expected while this persona is
    # active (a bundle's target list + the primary language, appended at install).
    # Empty = no restriction (the GA accepts any installed language).
    languages: list[Language]
    response_language: Language  # language this persona responds in; drives TTS voice selection
    voices: dict[str, str]      # IETF language code (or DEFAULT_VOICE_ROLE) -> Kokoro voice identifier
    is_system: bool
    created_at: datetime
    updated_at: datetime
    speaking_rate: float = 1.0  # persona-scoped TTS rate, e.g. a persona may want it slower than GA
    is_active: bool = True
    # Author-namespaced bundle identity (e.g. "author/persona-name"), unique by convention;
    # None for GA and user-created personas. Set once at bundle install, never reassigned.
    persona_key: str | None = None
    # Names the strategy set (selection/assessment/enrichment) this persona binds to —
    # a bare identifier resolved against the composition root's registry at startup;
    # None (GA, user-created personas) binds nothing. Set at creation like persona_key.
    strategy: str | None = None
    # Opaque persona-owned tunables, copied verbatim from a bundle's [persona.settings].
    # Read only by the owning persona's own strategies; generic code never branches on
    # its contents (same leak-prevention contract as persona_state, one level up).
    settings: dict | None = None
    # Does this persona's conversation record real events? False for a persona whose
    # sessions are rehearsal — drills, role-play, exercises — where what the user says is
    # performance, not autobiography, so no episode may be extracted from it (FR-407).
    # Declared per persona, NOT inferred: a persona can want assessment AND episodes.
    # This is a first-class attribute precisely so generic code may read it — unlike
    # `settings`/`persona_state`, which INV-6 keeps opaque.
    extracts_episodes: bool = True
    # May this persona READ concept/procedure knowledge owned by other personas?
    # False (the default, and every persona before 2026-08-02) means it sees only its
    # own scope, per FR-303: the same name under two personas is two pieces of knowledge.
    # True is for a persona that works *from* the user's existing memory rather than
    # authoring its own — without it, such a persona can only ever quiz on what it was
    # told during its own sessions, which is circular.
    # Read-only and read-only by construction: authoring, upsert scoping and INV-9's
    # cascade are untouched, so nothing this persona sees can be rewritten or deleted by
    # it. Like extracts_episodes, a first-class attribute precisely so generic code may
    # read it — unlike `settings`, which INV-6 keeps opaque.
    reads_across_personas: bool = False
    # Does this persona need to know which language each user turn was SPOKEN in?
    # False (the default) means turns reach the model as plain text. True prefixes each
    # user turn with [lang:code] (FR-114) — worth it only for a persona whose job depends
    # on the distinction, e.g. telling an on-target utterance from one in the user's own
    # language. Everyone else pays prompt pollution for a signal they cannot use, and
    # worse: a real session on 2026-08-02 had an English sentence misdetected as French,
    # and the model read the resulting [lang:fr] as an instruction and switched language
    # mid-conversation, overriding its own standing response-language rule.
    sees_turn_language: bool = False

    def __post_init__(self) -> None:
        _validate_voices(self.voices)

    @property
    def default_voice(self) -> str:
        return self.voices[DEFAULT_VOICE_ROLE]

    def update(
        self,
        updated_at: datetime,
        name: str | None = None,
        system_prompt: str | None = None,
        voices: dict[str, str] | None = None,
        speaking_rate: float | None = None,
        response_language: "Language | None" = None,
    ) -> None:
        if name:
            self.name = name
        if system_prompt:
            self.system_prompt = system_prompt
        if voices:
            _validate_voices(voices)
            self.voices = voices
        if speaking_rate:
            self.speaking_rate = speaking_rate
        if response_language:
            self.response_language = response_language
        self.updated_at = updated_at

    def deactivate(self, updated_at: datetime) -> None:
        if self.is_system:
            raise ValueError("System personas cannot be deactivated")
        self.is_active = False
        self.updated_at = updated_at

    def reactivate(self, updated_at: datetime) -> None:
        self.is_active = True
        self.updated_at = updated_at

    @classmethod
    def general_assistant(
        cls,
        system_prompt: str,
        response_language: "Language" = Language("en"),
        tts_voice: str = "af_heart",
    ) -> "AssistantPersona":
        now = datetime.now(UTC)
        return cls(
            id=GENERAL_ASSISTANT_ID,
            name="Vocal Assistant",  # generic placeholder — Mamemai is the product, not the persona's name
            system_prompt=system_prompt,
            languages=[],
            response_language=response_language,
            voices={DEFAULT_VOICE_ROLE: tts_voice},
            is_system=True,
            created_at=now,
            updated_at=now,
        )


@dataclass
class Directive:
    """FR-207: a user utterance that changes mamemai's own operating state rather than
    being answered in conversation. Two kinds today: `switch_persona`, and (2026-08-02)
    `set_response_language`, which changes the language the active persona answers in —
    the first voice-configurable setting under FR-701, and the reason INV-1 now
    enumerates configuration writes as its one live-path exception. First-class, not memory content: no `engagement_level`,
    no embedding, not persona-scoped knowledge — matched deterministically
    (`services.directives.matches_trigger`) against a turn's raw text, never surfaced
    as a "relevant memories" injection. One row per language the target is reachable
    in (`trigger` = {"keywords": [...], "max_words": N}, built from a translated action
    verb + `target_persona_id`'s own name). `target_persona_id` is an FK with
    `ON DELETE CASCADE` (INV-9) — removing a persona removes its own switch-to
    directives with no explicit cleanup code needed."""
    id: int | None
    # A directive names a persona OR a language, never both. target_persona_id is an FK
    # with ON DELETE CASCADE (INV-9); a language directive has no persona to cascade from
    # and is therefore global, surviving every persona.
    target_persona_id: UUID | None
    action: str
    language: Language  # the language this trigger's WORDING is in
    trigger: dict
    description: str
    target_language: Language | None = None  # what set_response_language switches TO
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class User:
    id: UUID
    primary_language: Language | None = None  # None until onboarding is complete
    secondary_languages: list[Language] = field(default_factory=list)
    idle_consolidation_minutes: float = 5.0  # how long to wait after disconnect before running offline consolidation

    def update_primary_language(self, new_language: Language) -> None:
        self.primary_language = new_language

    def update_idle_consolidation_minutes(self, minutes: float) -> None:
        self.idle_consolidation_minutes = minutes


@dataclass
class Turn:
    timestamp: datetime
    speaker: Speaker
    content: str
    language: Language | None = None  # set from STT output


@dataclass
class Conversation:
    id: int | None
    started_at: datetime
    persona_id: UUID
    turns: list[Turn] = field(default_factory=list)
    ended_at: datetime | None = None
    worthiness: bool | None = None
    summary: str | None = None
    consolidated: bool = False

    def add_turn(self, turn: Turn) -> None:
        if self.ended_at:
            raise ValueError("Cannot add a turn to an ended Conversation")
        self.turns.append(turn)

    def end(self, ended_at: datetime) -> None:
        self.ended_at = ended_at

    def mark_consolidated(self, worthiness: bool, summary: str | None) -> None:
        if self.consolidated:
            raise ValueError("Conversation is already consolidated")
        if not self.ended_at:
            raise ValueError("Cannot consolidate an active Conversation")
        if not self.turns:
            raise ValueError("Cannot consolidate a Conversation with no turns")
        self.worthiness = worthiness
        self.summary = summary
        self.consolidated = True

    @property
    def is_eligible_for_consolidation(self) -> bool:
        return bool(self.ended_at and self.turns and not self.consolidated)


@dataclass
class Episode:
    id: int | None
    summary: str
    happened_at: datetime
    origin_conversation_id: int  # provenance only — where this episode was first extracted
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding: list[float] | None = None


@dataclass
class Concept:
    id: int | None
    persona_id: UUID
    name: str
    description: str
    language: Language  # first introduced; stays fixed on upsert
    category: str | None = None  # free text, interpreted in the owning persona's own vocabulary
    # "authored" (bundle install, persona enrichment drafts) vs "organic" (live-conversation
    # extraction). Set once at creation, fixed forever — same contract as `language`. Governs
    # MemoryUpserter.upsert_concept's protection of curated content: a live-extraction candidate
    # that lands close enough to an authored concept is treated as a touch on it, never a rewrite.
    origin: str = "organic"
    # Opaque, unkeyed slot (persona_id already scopes ownership). Single-writer contract:
    # written only by the owning persona's assessment strategy, read only by that persona's
    # selection strategy — generic code never branches on its contents.
    persona_state: dict | None = None
    engagement_level: EngagementLevel = EngagementLevel.MENTIONED
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding: list[float] | None = None


@dataclass
class Procedure:
    id: int | None
    persona_id: UUID
    name: str
    description: str  # primary carrier of knowledge; always populated (~300 words)
    language: Language  # first introduced; stays fixed on upsert
    steps: list[str] = field(default_factory=list)  # empty when not decomposable into discrete steps
    category: str | None = None  # free text, interpreted in the owning persona's own vocabulary
    persona_state: dict | None = None  # same single-writer contract as Concept.persona_state
    engagement_level: EngagementLevel = EngagementLevel.MENTIONED
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding: list[float] | None = None


@dataclass
class MemoryBrief:
    content: str
    created_at: datetime
    updated_at: datetime
