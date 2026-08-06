# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
import asyncio
import json
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import truststore
import websockets

from .domain.model import (
    DEFAULT_VOICE_ROLE,
    SUPPORTED_LANGUAGES,
    Language,
    User,
    resolve_installed_languages,
)
from .infrastructure import postgres
from .infrastructure.config import load_config
from .infrastructure.embedding import SentenceTransformerEmbeddingService
from .infrastructure.language_detection import Py3LangidLanguageDetector
from .infrastructure.json_file import JSONLSessionLogReader, JSONLSessionReplayReader, JSONLTurnLogger
from .infrastructure.cognitive import (
    CognitiveAssessmentStrategy,
    CognitiveSelectionStrategy,
    STRATEGY_NAME as COGNITIVE_COACH,
)
from .infrastructure.language_tutor import (
    LanguageTutorAssessmentStrategy,
    LanguageTutorEnrichmentStrategy,
    LanguageTutorSelectionStrategy,
    OllamaClusterProposer,
    OllamaFocusInterpreter,
    OllamaPracticeJudge,
    STRATEGY_NAME as LANGUAGE_TUTOR,
)
from .infrastructure.llm import (
    OllamaConsolidationExtractor,
    OllamaDisambiguationEvaluator,
    OllamaLLMService,
    OllamaMemorySynthesizer,
    OllamaWorthinessEvaluator,
)
from .infrastructure.postgres import (
    PSConversationRepository,
    PSDirectiveRepository,
    PSMemoryBriefRepository,
    PSMemoryRepository,
    PSPersonaRepository,
    PSUnitOfWork,
    PSUserRepository,
)
from .infrastructure.recall_gate import DefaultRecallGate
from .infrastructure.stt import FasterWhisperSTTService
from .infrastructure.tts import KOKORO_DEFAULT_VOICES, KokoroTTSService
from .services.directives import PersonaDirectiveSync
from .services.memory import ConsolidateMemory, consolidation_is_due, EnrichMemory, GenerateMemoryBrief
from .services.persona import EditPersona
from .services.ports import LLMService, PersonaAssessmentPort, PersonaEnrichmentPort, PersonaSelectionPort, RecallGate
from .services.replay import TurnLogReplayer
from .services.session import EndSession, ProcessTurn, StartSession
from .services.user import CompleteOnboarding

# ---------------------------------------------------------------------------
# Long-lived server context — one DB connection and one set of real adapters
# shared across connections (single-user, no concurrency model; see CLAUDE.md)
# ---------------------------------------------------------------------------


@dataclass
class ServerContext:
    conn: psycopg.Connection
    stt: FasterWhisperSTTService
    # Live conversation only (ProcessTurn) — OllamaLLMService by default, or
    # OpenAICompatibleLLMService when [llm].provider = "openai_compatible"
    # (FR-707/TR-955). Never used for the offline pipeline — see offline_llm.
    llm: LLMService
    tts: KokoroTTSService
    embedding_service: SentenceTransformerEmbeddingService
    language_detector: Py3LangidLanguageDetector
    log_dir: Path
    memory_merge_threshold: float
    memory_disambiguate_threshold: float
    # The local Ollama model/host — always used for the offline pipeline (extraction,
    # worthiness, disambiguation, synthesis, GenerateMemoryBrief via offline_llm) and
    # every Ollama-backed strategy helper (e.g. the tutor's focus interpreter),
    # regardless of what `llm` above is live-configured to. A GPU-less/CPU-only
    # offline run is fine, per design decision, just slower.
    llm_model: str
    llm_ollama_host: str | None
    # [llm].num_ctx (TR-951) — the context window every local Ollama call sends
    # explicitly, rather than inheriting Ollama's silently-truncating 4096 default.
    llm_num_ctx: int
    # Dedicated instance for GenerateMemoryBrief (offline) — deliberately never `llm`
    # above: offline stays on local Ollama regardless of the live provider choice, so a
    # remote-LLM install doesn't silently start sending brief-generation prompts to a
    # third party too. Always Ollama, so always constructible even when `llm` isn't.
    offline_llm: OllamaLLMService
    # Wizard-installed languages (FR-705): the subset of SUPPORTED_LANGUAGES whose TTS
    # voices this installation actually pulled. Bounds onboarding selection (TR-103).
    installed_languages: list[Language]
    # [server].session_tail_turns (FR-109) — 0 disables tail injection entirely.
    session_tail_turns: int
    # [stt].min_utterance_ms (TR-958) — the floor below which end_utterance is dropped
    # without an STT pass.
    stt_min_utterance_ms: int
    user_repo: PSUserRepository
    persona_repo: PSPersonaRepository
    memory_brief_repo: PSMemoryBriefRepository
    memory_repo: PSMemoryRepository
    directive_repo: PSDirectiveRepository
    conversation_repo: PSConversationRepository
    # Live, per-turn (ProcessTurn) recall policy (FR-309/TR-314) — persona-scoped;
    # unlike `llm` above, does not vary with [llm].provider at all (it's local
    # threshold logic, no LLM call). default_recall_gate covers GA and any persona
    # without a registered override.
    default_recall_gate: DefaultRecallGate
    worthiness_evaluator: OllamaWorthinessEvaluator
    disambiguator: OllamaDisambiguationEvaluator
    synthesizer: OllamaMemorySynthesizer
    extractor: OllamaConsolidationExtractor
    # Dedicated connection + repos for the background offline pipeline (TurnLogReplayer ->
    # ConsolidateMemory), run via asyncio.to_thread. Kept separate from `conn` above: a
    # Postgres connection is a single logical session, so a live per-connection query
    # (StartSession etc., run directly on the event loop thread) racing against the
    # background thread's open per-conversation transaction on the *same* connection
    # could execute as part of that transaction, or block the event loop waiting on it.
    # Two independent connections sidestep this entirely.
    offline_conn: psycopg.Connection
    offline_user_repo: PSUserRepository
    offline_persona_repo: PSPersonaRepository
    offline_memory_brief_repo: PSMemoryBriefRepository
    offline_memory_repo: PSMemoryRepository
    offline_conversation_repo: PSConversationRepository
    offline_unit_of_work: PSUnitOfWork
    idle_timer_task: asyncio.Task | None = None
    # Connect-time consolidation catch-up (see _start_consolidation_catch_up). Held so a
    # rapid reconnect never starts a second pipeline over the first.
    catch_up_task: asyncio.Task | None = None


# The wire format of TR-101: PCM int16 (2 bytes) mono at 16 kHz. Named here because the
# only thing this module does with raw audio is measure how much of it there is.
STT_SAMPLE_RATE = 16000
STT_BYTES_PER_SAMPLE = 2


def utterance_duration_ms(audio: bytes) -> int:
    """How much speech the buffer holds, per TR-101's wire format.

    Wall-clock duration is *not* what this measures, and the difference matters: TR-203
    excises inter-word silence, so a 6-second sentence arrives as ~3 seconds of audio.
    That is why TR-958's floor is set against voiced content, not against how long the
    user held the floor.
    """
    return len(audio) * 1000 // (STT_SAMPLE_RATE * STT_BYTES_PER_SAMPLE)


def _replay_sessions(
    log_dir: Path,
    conversation_repo: PSConversationRepository,
    persona_repo: PSPersonaRepository,
) -> int:
    replayer = TurnLogReplayer(
        session_reader=JSONLSessionReplayReader(log_dir),
        conversation_repo=conversation_repo,
        persona_repo=persona_repo,
    )
    return replayer.execute()



# Keepalive/backpressure settings, both ends kept in step (2026-08-02).
#
# The failure they fix, diagnosed from a real session: audio is sent one chunk per
# synthesised sentence, and the client plays them one at a time. On a long reply the
# client's inbound queue fills, its reader PAUSES, TCP backpressure stops the socket
# draining — and the peer's ping then sits unread behind the queued audio. No pong
# arrives, and ~20s later the connection dies with "1011 keepalive ping timeout",
# mid-answer.
#
# The earlier reading (recorded in docs/PLAN.md 2026-07-20) blamed turn *duration* —
# turns measured 19.3-19.5s against a 20s default. That was wrong: the real window is
# ping_interval + ping_timeout = 40s, and turn processing never blocks either event loop
# (every blocking call is already off-loop). What correlates is reply LENGTH, because
# length is what fills the queue — which is exactly what that note observed without
# identifying why.
#
# max_queue is therefore the primary fix: hold a whole reply's audio so the reader never
# pauses. 64 chunks is minutes of speech, far beyond any single reply, and bounded (a
# chunk is one sentence, a few hundred KB at worst). The longer ping window is defence in
# depth for a backlog beyond even that, and still detects a genuinely dead peer.
WS_MAX_QUEUE = 64
WS_PING_INTERVAL = 30
WS_PING_TIMEOUT = 60

async def _run_offline_pipeline(ctx: ServerContext) -> None:
    """TurnLogReplayer -> ConsolidateMemory -> GenerateMemoryBrief. Triggered by the
    idle timer below; also doubles as crash recovery since replay runs on every connect.

    TurnLogReplayer and ConsolidateMemory are both fully synchronous (file/DB/LLM calls
    with no real await point) — dispatched via asyncio.to_thread so a long consolidation
    run doesn't block the event loop and delay an incoming reconnect. Both use the
    dedicated `offline_*` repos/connection, never the live `ctx.conn`. GenerateMemoryBrief
    stays awaited directly: it streams via ollama.AsyncClient and genuinely cooperates
    with the event loop (and only touches the DB via `offline_memory_brief_repo`, cheap
    enough not to need its own thread hop)."""
    replayed = await asyncio.to_thread(
        _replay_sessions, ctx.log_dir, ctx.offline_conversation_repo, ctx.offline_persona_repo,
    )
    consolidate = ConsolidateMemory(
        conversation_repo=ctx.offline_conversation_repo,
        memory_repo=ctx.offline_memory_repo,
        embedding_service=ctx.embedding_service,
        extractor=ctx.extractor,
        worthiness_evaluator=ctx.worthiness_evaluator,
        disambiguator=ctx.disambiguator,
        synthesizer=ctx.synthesizer,
        unit_of_work=ctx.offline_unit_of_work,
        user_repo=ctx.offline_user_repo,
        persona_repo=ctx.offline_persona_repo,
        assessment_strategies=_build_assessment_strategies(ctx),
        merge_threshold=ctx.memory_merge_threshold,
        disambiguate_threshold=ctx.memory_disambiguate_threshold,
    )
    processed = await asyncio.to_thread(consolidate.execute)
    # Enrichment runs after consolidation so strategies see freshly written
    # persona_state; strategies decide internally whether anything qualifies.
    enrich = EnrichMemory(
        memory_repo=ctx.offline_memory_repo,
        embedding_service=ctx.embedding_service,
        disambiguator=ctx.disambiguator,
        synthesizer=ctx.synthesizer,
        unit_of_work=ctx.offline_unit_of_work,
        enrichment_strategies=_build_enrichment_strategies(ctx),
        merge_threshold=ctx.memory_merge_threshold,
        disambiguate_threshold=ctx.memory_disambiguate_threshold,
    )
    enriched = await asyncio.to_thread(enrich.execute)
    if processed:
        brief_gen = GenerateMemoryBrief(
            llm=ctx.offline_llm,
            memory_brief_repo=ctx.offline_memory_brief_repo,
            memory_repo=ctx.offline_memory_repo,
        )
        await brief_gen.execute(datetime.now(UTC))
    print(f"[offline] replayed={replayed} consolidated={processed} enriched={enriched}")


def _start_consolidation_catch_up(ctx: "ServerContext") -> None:
    """Run the offline pipeline on *connect* when the gap since the previous session was
    already long enough to have earned it.

    Consolidation is otherwise idle-triggered: it starts after a disconnect and any new
    connection cancels it. That is right when someone reconnects straight away — but it
    starves completely when sessions are short and a few minutes apart, because every
    timer is cancelled before it fires. Replay keeps running on connect, so
    `conversations` fills up while nothing is ever consolidated, long-term memory stays
    frozen and the memory brief goes stale. Observed exactly that on 2026-08-02: 8
    sessions in 50 minutes left 7 conversations unconsolidated and a 13-day-old brief.

    The fix reuses the condition the cancelled timer was waiting for rather than
    inventing a new one: if the quiet gap since the previous session ended already
    exceeds `idle_consolidation_minutes`, that window *did* elapse — the timer simply
    was not around to notice — so the work is due now.

    Runs as a background task so the session starts immediately. The cost is real and
    worth stating: consolidation is LLM-heavy and Ollama serialises, so the first turn or
    two of this session may be slower while it finishes. That is a far better trade than
    never consolidating, and it self-limits — once the backlog is cleared, a connect with
    nothing to do returns almost instantly.
    """
    if ctx.catch_up_task is not None and not ctx.catch_up_task.done():
        return  # already catching up; never run two pipelines at once
    user = ctx.user_repo.get()
    if user is None:
        return
    previous = JSONLSessionLogReader(ctx.log_dir).get_previous()
    now = datetime.now(UTC)
    if not consolidation_is_due(
        previous.ended_at if previous else None, now, user.idle_consolidation_minutes
    ):
        return
    quiet_for = (now - previous.ended_at).total_seconds() / 60
    print(f"[offline] catching up: {quiet_for:.1f} min since the last session ended")
    ctx.catch_up_task = asyncio.create_task(_run_offline_pipeline_guarded(ctx))


async def _run_offline_pipeline_guarded(ctx: "ServerContext") -> None:
    try:
        await _run_offline_pipeline(ctx)
    except Exception:
        traceback.print_exc()


async def _run_offline_pipeline_after_idle(ctx: ServerContext, idle_consolidation_minutes: float) -> None:
    try:
        await asyncio.sleep(idle_consolidation_minutes * 60)
    except asyncio.CancelledError:
        return
    try:
        await _run_offline_pipeline(ctx)
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Persona strategy registry
# ---------------------------------------------------------------------------

# AssistantPersona.strategy name -> selection-strategy factory. Binding personas to
# concrete strategy implementations is a composition-root concern, so the registry lives
# here. One entry serves every persona of that class (e.g. all language tutors, any
# target language) — new bundles bind without code changes.
SELECTION_STRATEGY_FACTORIES: dict[str, Callable[["ServerContext"], PersonaSelectionPort]] = {
    LANGUAGE_TUTOR: lambda ctx: LanguageTutorSelectionStrategy(
        memory_repo=ctx.memory_repo,
        persona_repo=ctx.persona_repo,
        embedding_service=ctx.embedding_service,
        focus_interpreter=OllamaFocusInterpreter(
            model=ctx.llm_model, host=ctx.llm_ollama_host, num_ctx=ctx.llm_num_ctx,
        ),
    ),
    # No LLM helper of its own: this persona's selection is pure ranking over the
    # user's existing memory, so nothing here needs a model.
    COGNITIVE_COACH: lambda ctx: CognitiveSelectionStrategy(
        memory_repo=ctx.memory_repo,
        persona_repo=ctx.persona_repo,
    ),
}

# Live, same registry shape (FR-309/TR-314) — but unlike the three above, a persona
# without an entry here does NOT get a no-op: ProcessTurn falls back to
# ctx.default_recall_gate, since recall gating applies to ordinary conversation too,
# not just advanced personas opting in. Empty for now: the language tutor's own
# selection strategy (SELECTION_STRATEGY_FACTORIES above) already injects the item
# currently being tested via a separate mechanism (`select_items`, spaced-repetition
# scheduling off `persona_state`) — a one-word answer to the tutor's own prompt
# doesn't need its own generic memory search on top of that (2026-07-30, removed
# LanguageTutorRecallGate — see docs/PLAN.md). Kept as its own registry, not deleted
# outright, since a future persona-type may genuinely need a distinct policy here.
RECALL_GATE_FACTORIES: dict[str, Callable[["ServerContext"], RecallGate]] = {}


# Same registry, offline half: assessment strategies run inside the consolidation
# pipeline and are constructed against the offline_* repos/connection.
ASSESSMENT_STRATEGY_FACTORIES: dict[str, Callable[["ServerContext"], PersonaAssessmentPort]] = {
    LANGUAGE_TUTOR: lambda ctx: LanguageTutorAssessmentStrategy(
        memory_repo=ctx.offline_memory_repo,
        persona_repo=ctx.offline_persona_repo,
        user_repo=ctx.offline_user_repo,
        judge=OllamaPracticeJudge(model=ctx.llm_model, host=ctx.llm_ollama_host, num_ctx=ctx.llm_num_ctx),
    ),
    COGNITIVE_COACH: lambda ctx: CognitiveAssessmentStrategy(
        persona_repo=ctx.offline_persona_repo,
    ),
}

# Offline half, enrichment: dispatched by EnrichMemory after consolidation, so the
# strategies see freshly written persona_state (e.g. user_initiated salience).
ENRICHMENT_STRATEGY_FACTORIES: dict[str, Callable[["ServerContext"], PersonaEnrichmentPort]] = {
    LANGUAGE_TUTOR: lambda ctx: LanguageTutorEnrichmentStrategy(
        memory_repo=ctx.offline_memory_repo,
        persona_repo=ctx.offline_persona_repo,
        proposer=OllamaClusterProposer(model=ctx.llm_model, host=ctx.llm_ollama_host, num_ctx=ctx.llm_num_ctx),
    ),
}


# Every strategy name this build of the server knows, across all four registries. A name
# absent from one registry but present in another is a persona declining that capability,
# not an error — see _build_strategies.
_KNOWN_STRATEGY_NAMES = (
    SELECTION_STRATEGY_FACTORIES.keys()
    | RECALL_GATE_FACTORIES.keys()
    | ASSESSMENT_STRATEGY_FACTORIES.keys()
    | ENRICHMENT_STRATEGY_FACTORIES.keys()
)


def _build_strategies(ctx: "ServerContext", factories: dict, persona_repo) -> dict[UUID, object]:
    """Resolve each persona's declared strategy name against a registry. Built per
    connection / per offline run so a bundle installed between sessions binds without
    a server restart. Unknown names degrade gracefully: warn, bind nothing.

    "Unknown" means unknown to the *whole server* (`_KNOWN_STRATEGY_NAMES`), not missing
    from this one registry: a persona implements the capabilities it has, and most have no
    enrichment or recall-gate strategy at all. Keying the warning on the registry instead
    meant `cognitive_coach` — registered for selection and assessment, deliberately absent
    from enrichment — warned on every server start and every offline run (observed
    2026-08-03). Suppressing only a wholly-empty registry, as this did until then, covered
    RECALL_GATE_FACTORIES but nothing else, and a permanent warning for a non-problem is
    how real ones get ignored. A name in no registry at all still warns, once per registry
    — noisy, but that persona genuinely is misconfigured."""
    strategies: dict[UUID, object] = {}
    for persona in persona_repo.list_all():
        if persona.strategy is None:
            continue
        factory = factories.get(persona.strategy)
        if factory is None:
            if persona.strategy not in _KNOWN_STRATEGY_NAMES:
                print(
                    f"[strategy] persona '{persona.name}' declares unknown strategy "
                    f"'{persona.strategy}' — nothing bound from this registry"
                )
            continue
        strategies[persona.id] = factory(ctx)
    return strategies


def _build_selection_strategies(ctx: "ServerContext") -> dict[UUID, PersonaSelectionPort]:
    return _build_strategies(ctx, SELECTION_STRATEGY_FACTORIES, ctx.persona_repo)


def _build_recall_gates(ctx: "ServerContext") -> dict[UUID, RecallGate]:
    return _build_strategies(ctx, RECALL_GATE_FACTORIES, ctx.persona_repo)


def _build_assessment_strategies(ctx: "ServerContext") -> dict[UUID, PersonaAssessmentPort]:
    return _build_strategies(ctx, ASSESSMENT_STRATEGY_FACTORIES, ctx.offline_persona_repo)


def _build_enrichment_strategies(ctx: "ServerContext") -> dict[UUID, PersonaEnrichmentPort]:
    return _build_strategies(ctx, ENRICHMENT_STRATEGY_FACTORIES, ctx.offline_persona_repo)


# ---------------------------------------------------------------------------
# WebSocket handler (one per connection)
# ---------------------------------------------------------------------------


async def _handle(ws, ctx: ServerContext) -> None:
    print("Client connected")

    # A new connection means the previous disconnect's idle window is moot.
    if ctx.idle_timer_task is not None:
        ctx.idle_timer_task.cancel()
        ctx.idle_timer_task = None

    replayed = _replay_sessions(ctx.log_dir, ctx.conversation_repo, ctx.persona_repo)
    if replayed:
        print(f"Replayed {replayed} unprocessed session(s) into the database")

    _start_consolidation_catch_up(ctx)

    session_id = uuid.uuid4()
    started_at = datetime.now(UTC)

    start_session = StartSession(
        user_repo=ctx.user_repo,
        persona_repo=ctx.persona_repo,
        memory_brief_repo=ctx.memory_brief_repo,
        session_log_reader=JSONLSessionLogReader(ctx.log_dir),
        directive_repo=ctx.directive_repo,
        session_tail_turns=ctx.session_tail_turns,
    )
    turn_logger = JSONLTurnLogger(ctx.log_dir)

    session = start_session.execute(session_id, started_at)

    process_turn = ProcessTurn(
        stt=ctx.stt,
        llm=ctx.llm,
        tts=ctx.tts,
        embedding_service=ctx.embedding_service,
        memory_repo=ctx.memory_repo,
        default_recall_gate=ctx.default_recall_gate,
        persona_repo=ctx.persona_repo,
        turn_logger=turn_logger,
        language_detector=ctx.language_detector,
        selection_strategies=_build_selection_strategies(ctx),
        recall_gates=_build_recall_gates(ctx),
        # FR-701 language directives: what this machine can actually speak, and how to
        # pick a voice for a language. Same map the wizard and bundle installer use.
        installed_languages=ctx.installed_languages,
        default_voice_for=lambda language: KOKORO_DEFAULT_VOICES.get(language.code, "af_heart"),
    )
    end_session = EndSession(turn_logger=turn_logger)
    complete_onboarding = CompleteOnboarding(user_repo=ctx.user_repo)
    edit_persona = EditPersona(persona_repo=ctx.persona_repo)

    if session.needs_onboarding:
        # Only installed languages are offered (FR-002/FR-705) — the primary language
        # must be one whose TTS voice actually exists on this machine.
        supported = [lang.code for lang in ctx.installed_languages]
        await ws.send(json.dumps({"type": "select_language", "supported": supported}))

    audio_buffer = b""
    onboarding_done = not session.needs_onboarding

    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                if onboarding_done:
                    audio_buffer += msg
                continue

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            msg_type = data.get("type")

            if msg_type == "language_selected" and not onboarding_done:
                lang_code = data.get("language", "en")
                lang = Language(lang_code)
                voice = KOKORO_DEFAULT_VOICES.get(lang_code, "af_heart")
                complete_onboarding.execute(session.user, lang)
                session.active_persona = edit_persona.execute(
                    persona_id=session.active_persona.id,
                    now=datetime.now(UTC),
                    voices={DEFAULT_VOICE_ROLE: voice},
                    response_language=lang,
                )
                onboarding_done = True
                print(f"Language selected: {lang_code}, voice: {voice}")
                continue

            if msg_type == "end_utterance" and onboarding_done:
                if not audio_buffer:
                    continue
                # TR-958. Loud, not silent: an STT pass skipped here is either noise the
                # client's onset gate let through, or a real turn being eaten by too high
                # a floor, and only this line tells them apart. Every drop is one saved
                # `medium`-model pass plus its language detection — 75% of them, measured.
                duration_ms = utterance_duration_ms(audio_buffer)
                if duration_ms < ctx.stt_min_utterance_ms:
                    print(f"[stt] skipped {duration_ms}ms utterance (< {ctx.stt_min_utterance_ms}ms floor)")
                    audio_buffer = b""
                    continue
                audio = audio_buffer
                audio_buffer = b""
                try:
                    # TR-316: each synthesised segment is sent from inside the turn, as
                    # soon as it exists, rather than gathered and sent here once the whole
                    # reply is finished. `result.audio_chunks` is consequently empty and
                    # must not be re-sent — see TurnResult.
                    await process_turn.execute(session, audio, datetime.now(UTC), emit_audio=ws.send)
                except Exception:
                    # A single turn failing (e.g. TTS error) must not take down the
                    # whole connection/server — log it and let the session continue.
                    # With streaming, earlier segments of this turn have already reached
                    # the client and been played; speaking_end below still restores the
                    # mic, so the session recovers with a truncated reply rather than a
                    # silent one.
                    traceback.print_exc()
                await ws.send(json.dumps({"type": "speaking_end"}))

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        end_session.execute(session, datetime.now(UTC))
        ctx.idle_timer_task = asyncio.create_task(
            _run_offline_pipeline_after_idle(ctx, session.user.idle_consolidation_minutes)
        )
        print("Client disconnected")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _ensure_user_exists(user_repo: PSUserRepository, name_seed: str) -> None:
    """Single-user-per-schema system with no auth (INV-3) — bootstrap the one
    User row on first run instead of requiring a manual SQL insert before
    first connect. `name_seed` comes from the wizard's `[user] name` (the
    alias that also became this schema's slug); only read here, at the row's
    creation — the DB row is authoritative afterward, so re-running the
    wizard with a different answer has no effect on an already-bootstrapped
    install."""
    if user_repo.get() is None:
        user_repo.save(User(id=uuid4(), name=name_seed))


def main() -> None:
    # Same fix as the setup wizard (mamemai_setup.cli): patches ssl.SSLContext to verify
    # against the OS's native trust store instead of certifi's bundled CA list. The live
    # server itself shouldn't need network for model loading (see embedding.py's
    # HF_HUB_OFFLINE guard and the wizard's pre-download steps), but this is cheap
    # insurance against any adapter that still touches the network — e.g. a model the
    # wizard didn't have a chance to pre-download — behind a TLS-inspecting proxy.
    truststore.inject_into_ssl()

    cfg = load_config()

    # Installed languages (FR-705): [languages].installed ∩ SUPPORTED_LANGUAGES, in
    # SUPPORTED_LANGUAGES order. Key absent (config predates it) → everything supported,
    # preserving pre-existing installs' behaviour. A code Mamemai can't support (e.g. a
    # future wizard offering more than Kokoro covers) is ignored with a warning rather
    # than failing startup.
    installed_languages = resolve_installed_languages(cfg.installed_languages)
    if cfg.installed_languages:
        unsupported = sorted(set(cfg.installed_languages) - {lang.code for lang in SUPPORTED_LANGUAGES})
        if unsupported:
            print(f"[config] ignoring installed languages Mamemai does not support: {', '.join(unsupported)}")
        if not installed_languages:
            raise RuntimeError(
                "[languages].installed contains no language Mamemai supports "
                f"(supported: {', '.join(lang.code for lang in SUPPORTED_LANGUAGES)}) — re-run mamemai-setup"
            )

    print("Connecting to database…")
    conn = postgres.connect(cfg.database_url, cfg.database_schema)
    user_repo = PSUserRepository(conn)
    _ensure_user_exists(user_repo, cfg.user_name_seed or "user-1")
    # Separate connection for the background offline pipeline — see ServerContext's
    # offline_* fields docstring for why this can't share `conn`. Same schema as the
    # live connection above (TR-5xx) — one deployment always targets exactly one.
    offline_conn = postgres.connect(cfg.database_url, cfg.database_schema)

    print("Loading Whisper model…")
    stt = FasterWhisperSTTService(
        cfg.stt_model_path, device=cfg.stt_device, compute_type=cfg.stt_compute_type,
        cpu_threads=cfg.stt_cpu_threads,
    )
    # Live conversation (FR-707/TR-955): local Ollama by default, or any OpenAI-compatible
    # remote endpoint when configured. offline_llm is always a separate, always-Ollama
    # instance (see ServerContext's docstring on it) — even when provider == "ollama",
    # where the two happen to be equivalent, keeping construction uniform is simpler and
    # safer than special-casing "reuse `llm`" for one provider only. Recall gating
    # (FR-309/TR-314) does not branch on provider at all — it's local threshold logic,
    # not an LLM call, so default_recall_gate below is built the same way regardless.
    if cfg.llm_provider == "openai_compatible":
        from .infrastructure.llm import OpenAICompatibleLLMService

        llm: LLMService = OpenAICompatibleLLMService(
            base_url=cfg.llm_base_url, model=cfg.llm_remote_model, api_key=cfg.llm_api_key,
        )
    else:
        llm = OllamaLLMService(model=cfg.llm_model, host=cfg.llm_ollama_host, num_ctx=cfg.llm_num_ctx)
    offline_llm = OllamaLLMService(model=cfg.llm_model, host=cfg.llm_ollama_host, num_ctx=cfg.llm_num_ctx)
    default_recall_gate = DefaultRecallGate()
    tts = KokoroTTSService(device=cfg.tts_device)
    print("Loading embedding model…")
    embedding_service = SentenceTransformerEmbeddingService(device=cfg.embedding_device)
    language_detector = Py3LangidLanguageDetector()
    print("Services ready.")

    ctx = ServerContext(
        conn=conn,
        stt=stt,
        llm=llm,
        tts=tts,
        embedding_service=embedding_service,
        language_detector=language_detector,
        log_dir=cfg.log_dir,
        memory_merge_threshold=cfg.memory_merge_threshold,
        memory_disambiguate_threshold=cfg.memory_disambiguate_threshold,
        llm_model=cfg.llm_model,
        llm_ollama_host=cfg.llm_ollama_host,
        llm_num_ctx=cfg.llm_num_ctx,
        offline_llm=offline_llm,
        installed_languages=installed_languages,
        session_tail_turns=cfg.session_tail_turns,
        stt_min_utterance_ms=cfg.stt_min_utterance_ms,
        user_repo=user_repo,
        persona_repo=PSPersonaRepository(conn),
        memory_brief_repo=PSMemoryBriefRepository(conn),
        memory_repo=PSMemoryRepository(conn),
        directive_repo=PSDirectiveRepository(conn),
        conversation_repo=PSConversationRepository(conn),
        default_recall_gate=default_recall_gate,
        worthiness_evaluator=OllamaWorthinessEvaluator(
            model=cfg.llm_model, host=cfg.llm_ollama_host, num_ctx=cfg.llm_num_ctx,
        ),
        disambiguator=OllamaDisambiguationEvaluator(
            model=cfg.llm_model, host=cfg.llm_ollama_host, num_ctx=cfg.llm_num_ctx,
        ),
        synthesizer=OllamaMemorySynthesizer(model=cfg.llm_model, host=cfg.llm_ollama_host, num_ctx=cfg.llm_num_ctx),
        extractor=OllamaConsolidationExtractor(
            model=cfg.llm_model, host=cfg.llm_ollama_host, num_ctx=cfg.llm_num_ctx,
        ),
        offline_conn=offline_conn,
        offline_user_repo=PSUserRepository(offline_conn),
        offline_persona_repo=PSPersonaRepository(offline_conn),
        offline_memory_brief_repo=PSMemoryBriefRepository(offline_conn),
        offline_memory_repo=PSMemoryRepository(offline_conn),
        offline_conversation_repo=PSConversationRepository(offline_conn),
        offline_unit_of_work=PSUnitOfWork(offline_conn),
    )
    # Idempotent (FR-207) — the "switch back to the general assistant" Directive must
    # exist regardless of which personas have been created; safe to re-run on every
    # startup.
    sync = PersonaDirectiveSync(ctx.directive_repo)
    sync.ensure_return_to_general_assistant(ctx.installed_languages)
    # "Answer me in X" for every installed language (FR-701/FR-705), each worded in the
    # language it asks for. Idempotent, so a language added by re-running the wizard picks
    # up its directive on next start — and one removed loses it on the same pass.
    sync.ensure_language_directives(ctx.installed_languages)

    async def _run() -> None:
        async def handler(ws):
            await _handle(ws, ctx)

        async with websockets.serve(
            handler, "0.0.0.0", cfg.ws_port, max_size=None,
            max_queue=WS_MAX_QUEUE, ping_interval=WS_PING_INTERVAL, ping_timeout=WS_PING_TIMEOUT,
        ):
            print(f"Server listening on :{cfg.ws_port}")
            await asyncio.Future()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
