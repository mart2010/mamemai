# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, UTC
from uuid import UUID

from num2words import num2words

from ..domain.events import ConversationBoundaryDetected, ConversationBoundaryType, PersonaSwitched
from ..domain.model import (
    DEFAULT_VOICE_ROLE,
    GENERAL_ASSISTANT_ID,
    AssistantPersona,
    Directive,
    Language,
    MemoryBrief,
    MemoryType,
    Speaker,
    Turn,
    User,
    cosine_similarity,
)
from .directives import ACTION_END_SESSION, ACTION_SET_RESPONSE_LANGUAGE, matches_trigger
from .ports import (
    DirectiveRepository,
    EmbeddingService,
    LanguageDetector,
    LLMService,
    MemoryBriefRepository,
    MemoryItem,
    MemoryRepository,
    Message,
    PersonaRepository,
    PersonaSelectionPort,
    RecallGate,
    DEFAULT_SELECTION_PACING,
    SelectedItem,
    SelectionPacing,
    SessionLogReader,
    STTService,
    TTSService,
    TurnLogger,
    UserRepository,
)

# Opt-in, off by default: persona-switch/selection/cast-voice tracing for evaluating
# a candidate LLM's tag-emission reliability (see server/tests/e2e/, and docs/PLAN.md
# Phase 12's live smoke + gemma3:27b follow-up, which is what this was built for).
# Gated because some of these lines echo conversation content to stdout — unlike the
# existing unconditional [latency]/[offline]/[strategy] lines, which never do.
# Named for the generic mechanism, not the one persona it was first built to trace:
# every line below is persona-agnostic (selection batches, directive switches, cast
# voices). Renamed 2026-07-31 — the previous name was the last persona-specific
# *identifier* in services/ or domain/ (tests/unit/test_no_persona_leak.py,
# docs/BRIEF_persona_contract.md).
_PERSONA_DEBUG = os.environ.get("MAMEMAI_TEST_PERSONA_DEBUG") is not None


@dataclass
class WorkingMemory:
    session_id: UUID
    started_at: datetime
    user: User
    active_persona: AssistantPersona
    # Directives (FR-207) — first-class, `domain.model.Directive`, not a Concept —
    # persona-switch targets today. Small, stable set; fetched once at session start,
    # same posture as memory_brief below.
    directives: list[Directive]
    memory_brief: MemoryBrief | None
    needs_onboarding: bool = False
    recent_turns: list[Turn] = field(default_factory=list)
    rolling_summary: str | None = None
    total_turn_count: int = 0
    session_tail: list[Turn] = field(default_factory=list)
    # Persona-selected items, keyed by persona id and consumed one per turn. Fetched
    # lazily on the first turn a strategy-bearing persona is active (sessions start on
    # GA; they arrive via mid-session switch) — key presence means "already fetched".
    # Whether an exhausted batch is re-queried is the persona's own declared pacing
    # (SelectionPacing, TR-306), not a property of this struct. Nothing *replaces* a
    # batch mid-session: the [FOCUS: ...] re-fetch went with the tag channel (TR-304).
    selection_batches: dict[UUID, list[SelectedItem]] = field(default_factory=dict)
    # RecallGate cache (FR-309/TR-314), keyed by persona id: every (embedding, results)
    # pair from an utterance that actually triggered a real memory search for that
    # persona this session, oldest first — persona-keyed because memory search is
    # itself persona-scoped, so a GA-context embedding is not a meaningful comparison
    # for another persona's. The *whole* history is kept and compared against, not
    # just the last entry, because nothing new can enter long-term memory mid-session
    # (INV-1): the searchable set is frozen for the conversation's duration, so a
    # repeat of any earlier query — not only the immediately preceding one — would
    # deterministically return the same results again. Absent/empty key = no search
    # has happened yet for that persona.
    recall_history: dict[UUID, list[tuple[list[float], list[MemoryItem]]]] = field(default_factory=dict)


# Where a synthesised segment goes the moment it exists (TR-316). The live handler's sink
# is the WebSocket send; awaiting it inline is deliberate — it preserves segment order and
# lets the client's own backpressure throttle generation, which is harmless because the
# user is still listening to what has already been sent.
AudioSink = Callable[[bytes], Awaitable[None]]


@dataclass
class TurnResult:
    # The reply's audio, in order — but **only when no `emit_audio` sink was given to
    # `ProcessTurn.execute`**. With a sink (what the server does) segments are delivered
    # as they are produced and this stays empty, so a caller that supplies one must not
    # also send this. Empty-rather-than-duplicated is the safe direction: the failure mode
    # is silence, which is obvious, rather than every sentence played twice.
    audio_chunks: list[bytes]
    assistant_content: str
    persona_switched: PersonaSwitched | None = None
    conversation_boundary: ConversationBoundaryDetected | None = None
    session_ended: bool = False


ONBOARDING_SCRIPT = """\
When delivering your introduction, cover, in your own natural spoken words (not a verbatim recitation):
- For now, you go by "Vocal Assistant" — a plain placeholder name, not a proper name yet. \
Mention that the user will eventually be able to give you a proper name by voice.
- That you are a personal, fully local voice assistant — no cloud services involved.
- That you remember things about the user across conversations over time.
- That you can take on different specialized personas for particular topics or activities, created \
by talking to you.
- That you are configured entirely by voice, with no apps or settings screens. Today that covers \
choosing your spoken language (done once already) and creating, listing, switching, or removing \
personas. More voice-configurable options will be added over time.
- That the user can ask you to repeat this introduction at any time in the future.
Keep it to roughly 5-8 natural spoken sentences, then invite them to start talking about whatever's \
on their mind."""

_FIRST_LAUNCH_DIRECTIVE = (
    "This is the user's very first conversation with you. Before responding to anything else, "
    "deliver your introduction now, as described below."
)

_SENTENCE_ENDINGS = {".", "!", "?"}


def _split_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """Split `buffer` into (complete sentences, trailing incomplete remainder). A
    single call's `buffer` isn't guaranteed to hold at most one sentence — a single LLM
    token can carry more than one sentence ending, so this must find every boundary, not
    just check whether the buffer as a whole ends on one."""
    sentences: list[str] = []
    start = 0
    for i, ch in enumerate(buffer):
        if ch in _SENTENCE_ENDINGS:
            sentences.append(buffer[start:i + 1])
            start = i + 1
    return sentences, buffer[start:]


# The in-band response-tag channel ([FOCUS: wish], [TOPIC_CONTINUATION]/[TOPIC_BREAK])
# was retired here — TR-304, see docs/BRIEF_inband_tags.md. It signalled control inside
# the very stream being spoken, which cost first-audio latency for every persona (a
# lead-in buffer scanned on every turn, whether or not a tag was coming), leaked into
# audio whenever a multilingual model translated its own control token ([Focalizza: ...]),
# and needed per-model prompt scaffolding to emit at all. FR-207 already ran this
# experiment for persona switching and a deterministic pre-LLM rule won on every axis.
# What replaced each signal: a persona switch still forces a conversation break, decided
# before the LLM is called (step 7 below); focus detection on the user turn and offline
# topic segmentation are both deferred, deliberately, as recorded in that note.
# `_strip_markdown`'s generic marker strip (FR-106/TR-308) remains the backstop for a
# model that emits bracket markers unprompted.


def _pacing_for(strategy: PersonaSelectionPort | None) -> SelectionPacing:
    """A strategy that declares no pacing gets the default, so a persona happy with the
    pipeline's behaviour writes nothing — absence stays the default, rather than
    boilerplate every strategy has to repeat."""
    return getattr(strategy, "pacing", DEFAULT_SELECTION_PACING)


def _session_voice(persona: AssistantPersona, language: str, session_id: UUID) -> str:
    """Resolve a detected language code to one concrete voice for this session.
    `persona.voices` is keyed by IETF language code for any cast role beyond the
    fixed `DEFAULT_VOICE_ROLE` anchor — an unregistered code (including the user's
    own native language, which is never itself a key) falls back to the anchor.

    That anchor fallback briefly resolved through the machine's language→voice map first
    (2026-08-02), while detection ran for cast-less personas too. With detection scoped
    back to casts the extra hop is not merely unused but wrong: for a cast persona the
    anchor *is* the deliberate choice for every language the cast does not cover, and
    substituting the machine default would discard a bundle-chosen voice the moment the
    persona writes a segment in one of those languages.

    A '|'-separated value is a rotation pool: the session id picks one
    deterministically — stable within a session, varying across sessions. Why a
    persona would want that rotation is its own concern, not this function's.
    Stateless by design: the live path never writes."""
    raw = persona.voices.get(language, persona.default_voice)
    options = [v.strip() for v in raw.split("|") if v.strip()]
    if not options:
        return persona.default_voice
    return options[session_id.int % len(options)]


_MARKDOWN_EMPHASIS = re.compile(r"(\*\*\*|\*\*|\*|___|__|_|`)")
_MARKDOWN_HEADER = re.compile(r"^#{1,6}\s*", re.MULTILINE)
_MARKDOWN_HRULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$\n?", re.MULTILINE)
_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # pictographs, emoticons, transport, supplemental symbols
    "\U00002600-\U000027BF"  # misc symbols, dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicator symbols (flags)
    "\U0000FE0F"  # variation selector-16
    "\U0000200D"  # zero-width joiner (multi-codepoint emoji sequences)
    "]+"
)


# User turns are rendered to the LLM with a [lang:code] prefix (FR-114/TR-303), so a
# model may mimic the convention in its own output — strip any such tag before TTS,
# the same never-spoken rule as every other bracket marker.
_LANG_TAG = re.compile(r"\[lang:[^\]]{0,20}\]\s*", re.IGNORECASE)


# Backstop for bracket markers the model invents on its own, which no code consumes and
# TTS otherwise speaks literally ("bracket speaker colon guide bracket"). Two recorded
# cases: cast-role labels ([SPEAKER:default], [TARGETTEACHER], [GUIDE]) left over from
# the pre-TR-305 tag-based cast design, still emitted although the persona prompt asks
# for no markers at all and nothing reads them; and a *localized* control tag
# ([Focalizza: ...] for [FOCUS:], 2026-07-13) — a multilingual model translating its own
# control token, which the literal [FOCUS: matcher can never catch.
#
# Deliberately marker-SHAPED, not any bracketed text: one unspaced token, optionally
# followed by a ':' payload. "[1, 2, 3]" and "[see below]" are ordinary speech and are
# left alone; "[GUIDE]" and "[Focalizza: pratica orale]" are not. Prompt-side fixes stay
# the primary defence (this is what a model does *despite* being told not to).
# As of 2026-08-02 this is the ONLY thing between a bracket token and the speaker: the
# in-band tag channel was retired (TR-304), so nothing is resolved upstream any more and
# every bracket span reaching here is by definition unwanted. That makes this
# load-bearing rather than a backstop — which is also why it stays marker-shaped: with
# no upstream parser left, over-broad stripping would eat real words the user must hear.
_SPOKEN_MARKER = re.compile(r"\[\s*[A-Za-z][A-Za-z0-9_-]{1,30}\s*(?::[^\]\n]{0,60})?\s*\]\s*")


def _strip_markdown(text: str) -> str:
    """Drop markdown headers/rules/emphasis/code markers, emoji, mimicked [lang:] tags,
    and stray bracket markers — LLMs emit them despite instructions not to, and TTS
    either reads them aloud literally (e.g. "hash", "asterisk") or renders them as
    unpronounceable glyphs."""
    text = _LANG_TAG.sub("", text)
    text = _SPOKEN_MARKER.sub("", text)
    text = _MARKDOWN_HRULE.sub("", text)
    text = _MARKDOWN_HEADER.sub("", text)
    text = _MARKDOWN_EMPHASIS.sub("", text)
    text = _EMOJI.sub("", text)
    return text


# Languages num2words can spell out. Others (ja, ko, zh-cn) are left to Kokoro/espeak's
# own number handling — their number reading is complex enough that a digit-substitution
# approach would likely do more harm than good.
_NUM2WORDS_LANGUAGES = {"en", "fr", "es", "it", "pt"}

# Thousands/decimal conventions, per language. `.` and `,` swap roles between English and
# the rest, so the pattern has to be chosen by language rather than shared — the same
# "7,840" is 7840 in English and 7.84 in French. Spaces (incl. NBSP and the narrow NBSP
# a model may emit) are never a decimal separator in any of these, so they always group.
_GROUP_SPACES = "    "
_SEPARATORS = {
    "en": (",", "."),
    "fr": (_GROUP_SPACES, ","),
    "es": ("." + _GROUP_SPACES, ","),
    "it": ("." + _GROUP_SPACES, ","),
    "pt": ("." + _GROUP_SPACES, ","),
}


def _number_pattern(thousands: str, decimal: str) -> re.Pattern[str]:
    """`grouped` requires separators followed by *exactly* three digits — that grouping is
    the only thing distinguishing a thousands separator from a decimal one. Anything that
    isn't grouped falls to `plain`, which is the original pattern untouched, so only
    genuinely grouped numbers take the new path."""
    t = "[" + re.escape(thousands) + "]"
    d = re.escape(decimal)
    return re.compile(
        rf"(?P<grouped>\d{{1,3}}(?:{t}\d{{3}})+(?:{d}\d+)?)"
        rf"|(?P<plain>\d+(?:[.,]\d+)?)"
    )


_NUMBER_BY_LANGUAGE = {lang: _number_pattern(*seps) for lang, seps in _SEPARATORS.items()}


def _spell_out_numbers(text: str, language_code: str | None) -> str:
    """Convert digit sequences to their spoken form — TTS engines (Kokoro/espeak) read \
literal digits inconsistently, especially outside English.

    Thousands separators are load-bearing, not cosmetic (FR-106): before 2026-08-03 the
    pattern was a bare `\\d+`, so French "12 000" matched as two separate numbers and was
    spoken "douze zéro" — `num2words(0)` being "zéro" is what turned every round thousand
    into nonsense. Years escaped (no separator), which is why it survived so long.
    """
    if language_code not in _NUM2WORDS_LANGUAGES:
        return text
    thousands, decimal = _SEPARATORS[language_code]

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        start, end = match.span()
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if before.isalpha() or after.isalpha():
            # A digit run glued to a letter (Kepler-62f, F-16, 1st) is an identifier, not
            # a quantity: spelling out just the digits splices words against the letter
            # with no separator (e.g. "soixante-deuxf"). Leave the whole token to the TTS
            # engine, same as CJK's own digit handling.
            return raw
        if match.lastgroup == "grouped":
            for ch in thousands:
                raw = raw.replace(ch, "")
            raw = raw.replace(decimal, ".")
        else:
            raw = raw.replace(",", ".")
        try:
            value: float | int = float(raw) if "." in raw else int(raw)
            return num2words(value, lang=language_code)
        except (ValueError, NotImplementedError):
            return match.group(0)

    return _NUMBER_BY_LANGUAGE[language_code].sub(_replace, text)


def _render_turn_content(turn: Turn, sees_turn_language: bool) -> str:
    """User turns may carry their STT-detected language into the LLM context as a
    [lang:code] prefix (FR-114): for a persona whose domain is language-sensitive it
    tells the model which language the user actually produced — distinguishing an
    on-target utterance from one in their own language, or, on a third language tag,
    from a likely pronunciation stumble. Rendering-only: stored turn content and
    session logs stay clean (the log's "language" field already carries the code).

    Applied only for a persona that declares `sees_turn_language`, since 2026-08-02.
    It used to apply to every persona, which was a known leak
    (docs/BRIEF_persona_contract.md, Evidence §4) and turned out to be worse than
    prompt pollution: in a real session, an English sentence was misdetected by STT as
    French, and the model read the resulting `[lang:fr]` as an instruction — switching
    language mid-conversation and overriding its own standing response-language rule.
    A persona that cannot use the signal should not be shown it, and no amount of
    prompt wording makes an unusable tag safe (the FR-207/TR-304 lesson: prefer not
    emitting a signal over asking a model to ignore one)."""
    if sees_turn_language and turn.speaker == Speaker.USER and turn.language is not None:
        return f"[lang:{turn.language.code}] {turn.content}"
    return turn.content


def _format_memory_item(item: MemoryItem) -> str:
    from ..domain.model import Concept, Episode, Procedure
    if isinstance(item, Episode):
        return f"[Episode] {item.summary}"
    if isinstance(item, Concept):
        return f"[Concept] {item.name}: {item.description}"
    if isinstance(item, Procedure):
        return f"[Procedure] {item.name}: {item.description}"
    return str(item)


def _compose_working_context(
    wm: WorkingMemory,
    recalled_memories: list[MemoryItem],
    selected_item: SelectedItem | None = None,
    blocked_switch_persona_name: str | None = None,
    recall_attempted: bool = False,
    language_changed_to: Language | None = None,
    language_unavailable: str | None = None,
    switched_to_persona_name: str | None = None,
    session_ending: bool = False,
) -> tuple[str, list[Message]]:
    # No persona-listing/switch-instruction block in the persona's own system prompt
    # (FR-202/FR-203 retired, FR-207): switching is decided deterministically before
    # this function ever runs (ProcessTurn.execute step 3b), so `system_prompt` below
    # never names another persona — the fix for the language-drift this scaffolding
    # used to cause. `blocked_switch_persona_name` below is a different, narrower
    # thing: a turn-scoped `messages` injection (same tier as recalled memories/
    # selected item, not a standing fixture), appearing only on the rare turn where
    # the user's own utterance already named this one persona and the switch to it
    # was blocked — reactive, never proactive naming.
    sees_lang = wm.active_persona.sees_turn_language
    prompt_parts: list[str] = [wm.active_persona.system_prompt]
    if wm.needs_onboarding:
        prompt_parts.insert(0, _FIRST_LAUNCH_DIRECTIVE)
        prompt_parts.append(ONBOARDING_SCRIPT)
    # Response-language instruction (FR-105): the active persona's fixed response
    # language, GA included (its response_language is set to User.primary_language at
    # onboarding and only changes on explicit request, INV-14).
    if wm.active_persona.response_language and not any(
        k != DEFAULT_VOICE_ROLE for k in wm.active_persona.voices
    ):
        # Suppressed for cast personas (non-default voices keys): a two-teacher cast
        # deliberately speaks two languages per reply, and a "respond only in X"
        # directive would fight the persona's own prompt (which owns language use).
        lang = wm.active_persona.response_language
        prompt_parts.append(f"Always respond in the language with IETF code '{lang.code}'. Never switch language unless explicitly asked.")
    # Personalization: every persona may address the user by name (User.name,
    # wizard-seeded at bootstrap; voice-editable only via a future directive, not
    # this generic composition step — see INV-6, this isn't persona-specific).
    prompt_parts.append(f"The user's name is '{wm.user.name}'. You may address them by it naturally.")
    if wm.memory_brief:
        prompt_parts.append(wm.memory_brief.content)
    # Honesty about memory, both directions (FR-309/TR-314) — only when the recall
    # gate actually decided this turn was worth checking memory for (`recall_attempted`);
    # an ordinary turn recall never ran on gets neither line, same as today.
    if recall_attempted:
        if recalled_memories:
            lines = "\n".join(f"- {_format_memory_item(m)}" for m in recalled_memories)
            prompt_parts.append(
                f"Relevant memories:\n{lines}\n"
                "Briefly acknowledge you're drawing on a past conversation, naturally "
                "and in your own words — don't recite this list."
            )
        else:
            prompt_parts.append(
                "You checked your memory for this and found nothing related to what "
                "the user just asked — say so plainly rather than guessing or making "
                "something up."
            )
    system_prompt = "\n\n".join(prompt_parts)

    messages: list[Message] = []

    if wm.session_tail:
        tail_text = "\n".join(
            f"{t.speaker.value}: {_render_turn_content(t, sees_lang)}" for t in wm.session_tail
        )
        messages.append(Message(role="system", content=f"Tail of previous session:\n{tail_text}"))

    if wm.rolling_summary:
        messages.append(Message(
            role="system",
            content=f"Earlier in this conversation: {wm.rolling_summary}",
        ))
    for turn in wm.recent_turns:
        role = "user" if turn.speaker == Speaker.USER else "assistant"
        messages.append(Message(role=role, content=_render_turn_content(turn, sees_lang)))

    if selected_item is not None:
        # Same per-turn injection mechanism as RAG recall: a role-tagged context message,
        # inserted just before the current user turn. Item and context are injected
        # verbatim — the strategy composed them; generic code never interprets them.
        lines = [f"Work this item into the conversation naturally this turn:\n{_format_memory_item(selected_item.item)}"]
        if selected_item.context:
            lines.append(f"Context: {selected_item.context}")
        injection = Message(role="system", content="\n".join(lines))
        insert_at = len(messages) - 1 if messages else 0
        messages.insert(insert_at, injection)

    if language_changed_to is not None:
        # The switch already happened (step 3b) and this persona's own response-language
        # instruction above is already the NEW language, so the reply confirming it is
        # itself in that language — same posture as a persona switch, whose confirmation
        # is spoken by the persona just switched to (FR-207).
        injection = Message(
            role="system",
            content=(
                "The user just asked you to answer in this language, and that has now been "
                "done and saved — it will hold for future sessions too, until they ask for "
                "another. Confirm briefly, in that language, and carry on with the "
                "conversation."
            ),
        )
        messages.insert(len(messages) - 1 if messages else 0, injection)

    if language_unavailable is not None:
        injection = Message(
            role="system",
            content=(
                f"The user just asked you to answer in '{language_unavailable}', but that "
                "language is not installed on this machine, so you cannot speak it. Say so "
                "briefly in your current language, mention that adding a language is done by "
                "re-running the mamemai setup wizard, and continue normally. Do not switch."
            ),
        )
        messages.insert(len(messages) - 1 if messages else 0, injection)

    if switched_to_persona_name is not None:
        # Symmetric to the blocked-switch injection below, for the opposite outcome
        # (2026-08-07, live-tested): without this, the switch happens deterministically
        # in step 3b but the reply has no signal that it did, so a small local model —
        # still holding `recent_turns` from the old persona, plus the unscoped
        # `memory_brief` above — keeps the old topic and can even claim it "can't
        # switch personas", despite the switch having already succeeded and the system
        # prompt above already being the new persona's.
        injection = Message(
            role="system",
            content=(
                f'You just switched to "{switched_to_persona_name}" — this already happened, '
                "deterministically, before this reply; it is not something you do or decide. "
                "This is your first reply as this persona: speak entirely as it now, per the "
                "system prompt above, and do not continue whatever the previous persona was "
                "discussing unless the user brings it up again. Never say you can't switch — "
                "you just did."
            ),
        )
        insert_at = len(messages) - 1 if messages else 0
        messages.insert(insert_at, injection)

    if blocked_switch_persona_name is not None:
        injection = Message(
            role="system",
            content=(
                f'The user just asked to switch to "{blocked_switch_persona_name}", but that '
                "persona is currently deactivated and unavailable. Briefly let them know, in "
                "your own words, and continue the conversation normally — do not attempt to switch."
            ),
        )
        insert_at = len(messages) - 1 if messages else 0
        messages.insert(insert_at, injection)

    if session_ending:
        # FR-115, 2026-08-07: the session is ending deterministically (`ACTION_END_SESSION`
        # already matched in step 3b) — this is why `selected_item`/recall were both
        # skipped for this turn (`is_directive_turn`). Say so explicitly, or a persona that
        # always has an exercise queued keeps trying to work it into the reply anyway.
        injection = Message(
            role="system",
            content=(
                "The user is ending the session now. Give a brief, natural goodbye in your "
                "own words and nothing else — no question, no new topic, no exercise or "
                "other content."
            ),
        )
        insert_at = len(messages) - 1 if messages else 0
        messages.insert(insert_at, injection)

    return system_prompt, messages


class StartSession:
    def __init__(
        self,
        user_repo: UserRepository,
        persona_repo: PersonaRepository,
        memory_brief_repo: MemoryBriefRepository,
        session_log_reader: SessionLogReader,
        directive_repo: DirectiveRepository,
        session_tail_turns: int = 10,
        session_continuation_threshold_hours: float = 24.0,
    ) -> None:
        self._user_repo = user_repo
        self._persona_repo = persona_repo
        self._memory_brief_repo = memory_brief_repo
        self._session_log_reader = session_log_reader
        self._directive_repo = directive_repo
        self._tail_turns = session_tail_turns
        self._threshold_hours = session_continuation_threshold_hours

    def execute(self, session_id: UUID, started_at: datetime) -> WorkingMemory:
        user = self._user_repo.get()
        if user is None:
            raise RuntimeError("No user record found — database not initialised")
        persona = self._persona_repo.get(GENERAL_ASSISTANT_ID)
        if persona is None:
            raise RuntimeError("GeneralAssistant not found — database not initialised")
        needs_onboarding = user.primary_language is None

        session_tail: list[Turn] = []
        if not needs_onboarding:
            previous = self._session_log_reader.get_previous()
            if previous:
                delta_hours = (started_at - previous.ended_at).total_seconds() / 3600
                if delta_hours <= self._threshold_hours:
                    session_tail = self._session_log_reader.read_tail(
                        previous.session_id, self._tail_turns
                    )

        # Selection batches are NOT fetched here: sessions always start on GA, which has
        # no strategy — ProcessTurn fetches lazily on the first turn a strategy-bearing
        # persona (switched to mid-session) is active.
        return WorkingMemory(
            session_id=session_id,
            started_at=started_at,
            user=user,
            active_persona=persona,
            directives=self._directive_repo.list_all(),
            memory_brief=None if needs_onboarding else self._memory_brief_repo.get(),
            needs_onboarding=needs_onboarding,
            session_tail=session_tail,
        )


class ProcessTurn:
    def __init__(
        self,
        stt: STTService,
        llm: LLMService,
        tts: TTSService,
        embedding_service: EmbeddingService,
        memory_repo: MemoryRepository,
        default_recall_gate: RecallGate,
        persona_repo: PersonaRepository,
        turn_logger: TurnLogger,
        language_detector: LanguageDetector,
        selection_strategies: dict[UUID, PersonaSelectionPort] | None = None,
        recall_gates: dict[UUID, RecallGate] | None = None,
        installed_languages: list[Language] | None = None,
        default_voice_for: Callable[[Language], str] | None = None,
        rolling_window_size: int = 50,
    ) -> None:
        # Local import: .persona also imports WorkingMemory from this module, so a
        # top-level import here would be circular (same pattern as _format_memory_item's
        # local domain.model import above).
        from .persona import SwitchPersona

        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._embedding_service = embedding_service
        self._memory_repo = memory_repo
        self._persona_repo = persona_repo
        self._turn_logger = turn_logger
        self._language_detector = language_detector
        self._switch_persona = SwitchPersona(persona_repo)
        # persona_id -> selection strategy; personas without one (e.g. GA) get no batch.
        self._selection_strategies = selection_strategies or {}
        # persona_id -> RecallGate override; every other persona,
        # GA included, falls back to default_recall_gate (FR-309/TR-314) — unlike
        # selection_strategies above, recall gating is never a no-op.
        self._recall_gates = recall_gates or {}
        self._default_recall_gate = default_recall_gate
        self._rolling_window_size = rolling_window_size
        # FR-701 language directives: which languages this machine can actually speak,
        # and how to pick a voice for one. Injected rather than imported — the Kokoro
        # map lives in infrastructure and services must not reach into it (INV-2).
        # Empty/None means "no language directive can execute", which is the safe
        # default for any caller that doesn't wire this.
        self._installed_languages = list(installed_languages or [])
        self._default_voice_for = default_voice_for
        self._last_turn_end: float | None = None

    def _apply_response_language(
        self, wm: WorkingMemory, directive: Directive,
    ) -> tuple[Language | None, str | None]:
        """Execute a `set_response_language` directive (FR-701): change the language the
        ACTIVE persona answers in, and persist it.

        Returns (changed_to, unavailable_name) — at most one is set.

        This is the one live-path DB write INV-1 permits, and it is deliberately narrow:
        a single persona row, configuration columns only, no model call and nothing that
        can grow the memory store.

        Scope is the active persona, not the user. `User.primary_language` is untouched
        on purpose — INV-10 pins episode summaries to it precisely so that months of
        conversing in another language never rewrite the user's life story into that
        language, and INV-14 wants a primary-language change to be its own explicit act.
        "Answer me in French" is about what you hear, not about relabelling your past.

        Availability is re-checked here even though these directives are only generated
        for installed languages (FR-705): a directive row outlives the config that
        produced it, so a language uninstalled since would otherwise switch the persona
        to a voice this machine cannot synthesise — silence instead of speech.
        """
        target = directive.target_language
        if target is None:
            return None, None
        if target == wm.active_persona.response_language:
            return None, None  # already answering in it — silent no-op, same as an already-active persona
        if self._default_voice_for is None or target not in self._installed_languages:
            return None, target.code

        persona = wm.active_persona
        persona.response_language = target
        persona.voices = {**persona.voices, DEFAULT_VOICE_ROLE: self._default_voice_for(target)}
        persona.updated_at = datetime.now(UTC)
        self._persona_repo.save(persona)
        if _PERSONA_DEBUG:
            print(f"[persona-debug] response language -> {target.code} (voice {persona.default_voice})")
        return target, None

    async def _next_selected_item(self, wm: WorkingMemory) -> SelectedItem | None:
        """Lazily fetch the active persona's selection batch on its first active turn
        (a live DB read, same standing as RAG recall), then consume one item per turn.
        Key presence in selection_batches means "already fetched".

        Whether an exhausted batch is re-queried is the persona's declared pacing
        (TR-306), not a rule of this pipeline — it was the latter until 2026-08-02, which
        made one persona's planned-lesson shape binding on every other."""
        if wm.needs_onboarding:
            return None
        persona_id = wm.active_persona.id
        strategy = self._selection_strategies.get(persona_id)
        if strategy is None:
            return None
        pacing = _pacing_for(strategy)
        exhausted = persona_id in wm.selection_batches and not wm.selection_batches[persona_id]
        if persona_id not in wm.selection_batches or (exhausted and pacing.refetch_when_exhausted):
            wm.selection_batches[persona_id] = list(await asyncio.to_thread(strategy.select_items, persona_id))
            if _PERSONA_DEBUG:
                names = [getattr(i.item, "name", getattr(i.item, "summary", "?"))
                         for i in wm.selection_batches[persona_id]]
                print(f"[persona-debug] fetched batch for {persona_id}: {names}")
        batch = wm.selection_batches[persona_id]
        item = batch.pop(0) if batch else None
        if _PERSONA_DEBUG:
            name = getattr(item.item, "name", getattr(item.item, "summary", "?")) if item else None
            print(f"[persona-debug] selected item this turn: {name}")
        return item

    async def execute(
        self,
        wm: WorkingMemory,
        audio: bytes,
        now: datetime,
        emit_audio: AudioSink | None = None,
    ) -> TurnResult | None:
        t_start = time.monotonic()
        if self._last_turn_end is not None:
            print(f"[latency] Gap since last turn: {t_start - self._last_turn_end:.1f}s")

        # 1. STT
        text, detected_language = await asyncio.to_thread(self._stt.transcribe, audio)
        if not text.strip():
            return None
        t_stt_done = time.monotonic()
        print(f"[latency] STT: {t_stt_done - t_start:.2f}s")

        # 2. User turn
        user_turn = Turn(timestamp=now, speaker=Speaker.USER, content=text)
        user_turn.language = detected_language

        # 3. Log to file (primary write) + update working memory
        self._turn_logger.append(wm.session_id, user_turn)
        wm.recent_turns.append(user_turn)
        wm.total_turn_count += 1

        # 3b. Directive matching (FR-207) — replaces the retired [PERSONA:] tag scheme
        # (FR-202/FR-203). A small, fixed set of first-class Directives (persona-switch
        # targets, today) checked every turn, unconditionally — as a plain deterministic
        # keyword/word-count rule (services/directives.matches_trigger), not an
        # embedding comparison: a directive phrase is a short, closed-vocabulary
        # command, exactly the case where a rule outperforms a similarity search, and
        # it means this step needs no embedding call at all (previously a second,
        # separate embed() on every turn — the dominant avoidable per-turn CPU cost on
        # CPU-only hardware). A clearing match executes deterministically, before this
        # turn's system prompt is composed — no LLM decision or system-prompt
        # scaffolding involved, which is the actual fix for the GA language drift that
        # scaffolding used to cause. A target that's been deactivated (`is_active`)
        # is unreachable — its Directive row isn't cleaned up on deactivate (only on
        # removal, INV-9's cascade), so the trigger still matches, but the switch
        # itself is blocked here; `blocked_switch_persona_name` carries the target's
        # name through to step 6 so the reply can explain it, deterministically decided
        # same as the switch itself, never left to LLM judgment.
        persona_switched: PersonaSwitched | None = None
        blocked_switch_persona_name: str | None = None
        language_changed_to: Language | None = None
        language_unavailable: str | None = None
        session_ending = False
        # Most-keywords-wins, not first-in-list-wins: one persona's name can be a
        # strict word subset of another's (e.g. "Coach" vs "Old Coach") — an utterance
        # naming the more specific one satisfies BOTH triggers, so picking the first
        # match in id order could silently resolve to the wrong persona (caught live,
        # 2026-07-30). The candidate requiring the most keywords is the more specific,
        # better-evidenced match; ties fall back to list order (ascending id).
        matching_directives = [d for d in wm.directives if matches_trigger(d.trigger, text)]
        matched_directive = (
            max(matching_directives, key=lambda d: len(d.trigger.get("keywords", [])))
            if matching_directives else None
        )
        if matched_directive is not None and matched_directive.action == ACTION_SET_RESPONSE_LANGUAGE:
            language_changed_to, language_unavailable = self._apply_response_language(wm, matched_directive)
        elif matched_directive is not None and matched_directive.action == "switch_persona":
            target_id = matched_directive.target_persona_id
            if target_id != wm.active_persona.id:  # already-active target: silent no-op
                target_persona = self._persona_repo.get(target_id)
                if target_persona is not None and not target_persona.is_active:
                    blocked_switch_persona_name = target_persona.name
                elif target_persona is not None:
                    persona_switched = self._switch_persona.execute(wm, target_id)
                    if _PERSONA_DEBUG:
                        print(f"[persona-debug] directive matched -> switched to {wm.active_persona.name!r}")
                # target_persona is None: a dangling reference (shouldn't happen —
                # target_persona_id is FK-backed, INV-9) — nothing to switch to or
                # explain, silent no-op like an already-active target.
        elif matched_directive is not None and matched_directive.action == ACTION_END_SESSION:
            session_ending = True

        # A directive turn — a real switch, a blocked one, a language change, or a
        # session end — means this turn's own utterance was a directive attempt, not a
        # content question or something to search memory for.
        is_directive_turn = (
            persona_switched is not None
            or blocked_switch_persona_name is not None
            or language_changed_to is not None
            or language_unavailable is not None
            or session_ending
        )

        # 4. Recall gate → enrich working context from LTM (FR-309/TR-314). Replaces the
        # old per-turn LLM classification call with a persona-scoped, local threshold
        # policy: should_embed() short-circuits trivial utterances before any embedding
        # is computed; should_search() then skips the DB round trip when this turn's
        # embedding is nearly identical to ANY prior search this session (not just the
        # last one — nothing new can enter memory mid-session, INV-1, so a repeat of
        # any earlier query would return the same thing again), reusing that prior
        # search's cached results instead. Skipped entirely on a directive turn (3b) —
        # recall/selection resume normally next turn.
        recalled_memories: list[MemoryItem] = []
        # True once the gate actually decides this turn is worth checking memory for
        # (should_embed cleared) — distinct from `recalled_memories` being empty,
        # which is also true when the gate never even considered it (an ordinary
        # short reply). Lets the reply be honest either way (TR-303): reference
        # actually recalling something, or say plainly it found nothing, rather than
        # silently saying neither on both a real miss and a turn recall never ran on.
        recall_attempted = False
        persona_id = wm.active_persona.id
        if not is_directive_turn:
            gate = self._recall_gates.get(persona_id, self._default_recall_gate)
            if gate.should_embed(text):
                recall_attempted = True
                embedding = await asyncio.to_thread(self._embedding_service.embed_query, text)
                history = wm.recall_history.get(persona_id, [])
                if history:
                    similarities = (
                        (cosine_similarity(embedding, past_embedding), past_results)
                        for past_embedding, past_results in history
                    )
                    max_similarity, best_match = max(similarities, key=lambda pair: pair[0])
                else:
                    max_similarity, best_match = None, []
                if gate.should_search(max_similarity):
                    search_results = await asyncio.to_thread(
                        self._memory_repo.search,
                        embedding,
                        tuple(MemoryType),
                        top_n=5,
                        persona_id=persona_id,
                    )
                    # The gate's relevance floor is what makes "found nothing" reachable:
                    # search itself is ORDER BY distance LIMIT top_n with no floor, so a
                    # non-empty DB always answers with top_n rows however unrelated, and
                    # the honesty branch below could never fire (FR-309/TR-303).
                    recalled_memories = [
                        item for similarity, item in search_results if gate.is_relevant(similarity)
                    ]
                    # Cached under the query embedding including the empty case: a repeat
                    # of a question that found nothing must not pay for a second lookup
                    # that cannot find anything either (INV-1 freezes the searchable set).
                    wm.recall_history.setdefault(persona_id, []).append((embedding, recalled_memories))
                else:
                    recalled_memories = best_match

        # 5. Persona-selected item — batch fetched lazily on the active persona's first
        # turn, then one item per turn, injected via the same context-message mechanism
        # as recall above. Skipped on a directive turn for the same reason as step 4.
        # Whether a persona brings content on the turn that activates it is its own
        # declared pacing (TR-306), not this pipeline's call. A *blocked* switch is
        # excluded on purpose and regardless of the declaration: nobody was activated —
        # the active persona is explaining that another one is unavailable, which is no
        # moment to also hand it an exercise.
        activation_turn = persona_switched is not None
        serves_content = not is_directive_turn or (
            activation_turn and _pacing_for(self._selection_strategies.get(wm.active_persona.id)).include_activation_turn
        )
        selected_item = await self._next_selected_item(wm) if serves_content else None

        # 6. Stream the LLM response, synthesising each sentence via TTS as it completes
        # — instead of waiting for the entire reply before speaking a word. No control-tag
        # scan sits in front of this any more (TR-304 retired).
        system_prompt, messages = _compose_working_context(
            wm, recalled_memories, selected_item,
            blocked_switch_persona_name=blocked_switch_persona_name, recall_attempted=recall_attempted,
            language_changed_to=language_changed_to, language_unavailable=language_unavailable,
            switched_to_persona_name=wm.active_persona.name if persona_switched is not None else None,
            session_ending=session_ending,
        )
        wm.needs_onboarding = False
        response_language = wm.active_persona.response_language
        lang_code = response_language.code if response_language else None
        speaking_rate = wm.active_persona.speaking_rate
        # Multi-voice cast: each complete segment's OWN dominant language picks the
        # Kokoro voice — no LLM tag cooperation needed (see _session_voice — rotation
        # pools). Whole-segment granularity is deliberate: a segment that's
        # mostly the native language but quotes a target-language word stays in the
        # native voice (accented, as a real bilingual guide would sound), never split
        # mid-sentence — this is a design choice, not a detector limitation. A
        # low-confidence call (see infrastructure/language_detection.py) keeps whatever
        # voice was already active rather than force a switch.
        current_voice = wm.active_persona.default_voice
        # Detection is a *cast* feature and runs only for a persona that has one — a
        # persona with a single voice has nothing to choose between, so classifying its
        # text buys nothing. It ran for every persona over every installed language for
        # one day (2026-08-02), as a safety net for the model still answering in the old
        # language for a turn after a language switch. That belongs where it originates:
        # the switch must now be asked for *in* the language being switched to (FR-708),
        # which leaves the model's last user turn agreeing with its own instruction,
        # instead of second-guessing every segment of every ordinary reply against a
        # seven-way choice.
        cast_languages = tuple(k for k in wm.active_persona.voices if k != DEFAULT_VOICE_ROLE)
        native_lang = wm.user.primary_language.code if wm.user.primary_language else None
        # Exactly the languages actually in play for this persona: its cast's, its own,
        # and the user's. A single candidate makes detection meaningless — there is
        # nothing to tell apart.
        candidates = set(cast_languages)
        if candidates:
            candidates.add(wm.active_persona.response_language.code)
            if native_lang:
                candidates.add(native_lang)
        detection_candidates = tuple(sorted(candidates)) if len(candidates) > 1 else ()

        audio_chunks: list[bytes] = []
        content_parts: list[str] = []
        sentence_buffer = ""
        boundary_marker: ConversationBoundaryType | None = None
        t_first_token: float | None = None
        t_first_audio: float | None = None

        async def _collect(chunk: bytes) -> None:
            audio_chunks.append(chunk)

        # No sink = accumulate into TurnResult, the pre-2026-08-04 behaviour. The live
        # server always supplies one; see TurnResult.audio_chunks.
        sink: AudioSink = emit_audio or _collect

        async def _synthesise_segment(text: str) -> None:
            nonlocal t_first_audio, current_voice
            processed = _spell_out_numbers(_strip_markdown(text), lang_code)
            if detection_candidates:
                detected = self._language_detector.detect(processed, detection_candidates)
                if detected is not None:
                    current_voice = _session_voice(wm.active_persona, detected, wm.session_id)
            if _PERSONA_DEBUG:
                print(f"[persona-debug] segment voice={current_voice!r} text={processed[:60]!r}")
            content_parts.append(processed)
            t_segment_ready = time.monotonic()
            chunk = await asyncio.to_thread(self._tts.synthesise, processed, current_voice, speaking_rate)
            t_synthesised = time.monotonic()
            # Dispatched here, inside the token loop — not gathered and returned. This is
            # what TR-316 requires and what makes the metric below mean what it says.
            await sink(chunk)
            if t_first_audio is None:
                t_first_audio = time.monotonic()
                print(f"[latency] TTS first chunk: {t_synthesised - t_segment_ready:.2f}s")
                # Named for what it now measures. It used to read "Total to first audio"
                # while measuring when the server *synthesised* its first chunk — every
                # chunk then waited for the whole reply to finish before being sent, so
                # the figure understated the user's actual wait by the entire remaining
                # generation (~16s of a 22.7s turn, measured 2026-08-04).
                print(f"[latency] First audio sent: {t_first_audio - t_start:.2f}s")

        # No control-tag scan stands between the first token and the first sentence
        # (TR-304 retired): a sentence is synthesised as soon as it completes, so first
        # audio no longer waits on a lead-in buffer that most turns never needed.
        async for token in self._llm.complete(messages, system_prompt):
            if t_first_token is None:
                t_first_token = time.monotonic()
                print(f"[latency] LLM first token: {t_first_token - t_stt_done:.2f}s")

            sentence_buffer += token
            complete, sentence_buffer = _split_complete_sentences(sentence_buffer)
            for sentence in complete:
                await _synthesise_segment(sentence)

        if sentence_buffer.strip():
            await _synthesise_segment(sentence_buffer)

        assistant_content = "".join(content_parts).strip()
        print(f"[latency] Total turn: {time.monotonic() - t_start:.2f}s")

        # 7. Conversation boundary event — marker embedded in assistant turn below.
        # A persona switch (3b) is now the ONLY source of a mid-session break. The LLM's
        # own [TOPIC_CONTINUATION]/[TOPIC_BREAK] self-tag was retired with the rest of
        # the in-band channel (TR-304): it had a parser but no producer — no prompt
        # anywhere ever asked for it — so it only ever fired by accident.
        # This one stays because it is deterministic and load-bearing: consolidation
        # groups turns into a single Conversation with one frozen persona_id per group
        # (replay.py's _group_into_conversations only re-evaluates it on a BREAK), so
        # without this a switch mid-session lets the new persona's turns ride along under
        # the old persona's scope — silently breaking FR-303 persona-scoped knowledge,
        # TR-704 assessment dispatch (a persona's own PersonaAssessmentPort never firing
        # for its own content), and FR-504's episode exemption.
        # Topic-based splitting within one persona's stretch has no replacement yet and
        # is a known gap, deliberately deferred — see docs/BRIEF_inband_tags.md.
        if persona_switched is not None:
            boundary_marker = ConversationBoundaryType.BREAK
        boundary = ConversationBoundaryDetected(boundary_type=boundary_marker) if boundary_marker else None

        # 8. (was: focus-marker batch re-fetch) A session wish no longer reaches
        # selection. `PersonaSelectionPort.select_items` still takes `focus`, and the
        # strategy side that interprets it is intact — what was removed is the in-band
        # channel that fed it, which charged every persona a lead-in buffer and leaked
        # into audio when the model translated its own control token ([Focalizza: ...]).
        # Detecting the wish on the USER turn instead is the intended replacement,
        # deliberately deferred until it can be designed against a second persona and
        # tested live — see docs/BRIEF_inband_tags.md. Until then focus is always None
        # and every persona gets its default batch.

        # 9. Log assistant turn + update working memory
        # Fresh timestamp captures when the LLM finished — gap from `now` reflects response time.
        assistant_turn = Turn(timestamp=datetime.now(UTC), speaker=Speaker.ASSISTANT, content=assistant_content)
        self._turn_logger.append(wm.session_id, assistant_turn, marker=boundary_marker, persona_id=wm.active_persona.id)
        wm.recent_turns.append(assistant_turn)
        wm.total_turn_count += 1

        # 10. Rolling window check
        if (self._rolling_window_size > 0
                and wm.total_turn_count % self._rolling_window_size == 0):
            await self._summarise_window(wm)

        self._last_turn_end = time.monotonic()
        return TurnResult(
            audio_chunks=audio_chunks,
            assistant_content=assistant_content,
            persona_switched=persona_switched,
            conversation_boundary=boundary,
            session_ended=session_ending,
        )

    async def _summarise_window(self, wm: WorkingMemory) -> None:
        n = self._rolling_window_size // 2
        turns = wm.recent_turns[:n]
        excerpt = "\n".join(f"{t.speaker.value}: {t.content}" for t in turns)
        content = excerpt
        if wm.rolling_summary:
            content = f"Previous summary:\n{wm.rolling_summary}\n\nNew turns:\n{excerpt}"
        tokens: list[str] = []
        async for token in self._llm.complete(
            messages=[Message(role="user", content=f"Summarise concisely:\n{content}")],
            system_prompt="You are a conversation summariser. Be brief.",
        ):
            tokens.append(token)
        wm.rolling_summary = "".join(tokens).strip()
        wm.recent_turns = wm.recent_turns[n:]


class EndSession:
    def __init__(self, turn_logger: TurnLogger) -> None:
        self._turn_logger = turn_logger

    def execute(self, wm: WorkingMemory, ended_at: datetime) -> None:
        self._turn_logger.close(wm.session_id, ended_at, clean_exit=True)
