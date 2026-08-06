import pytest
from datetime import datetime, UTC, timedelta
from uuid import UUID, uuid4

from mamemai_server.domain.events import ConversationBoundaryType
from mamemai_server.domain.model import (
    AssistantPersona,
    Concept,
    Directive,
    GENERAL_ASSISTANT_ID,
    Language,
    MemoryBrief,
    Speaker,
    Turn,
    User,
)
from mamemai_server.services.ports import SelectedItem, SelectionPacing, SessionInfo
from mamemai_server.services.session import EndSession, ProcessTurn, StartSession, _compose_working_context

from tests.fakes.fakes import (
    FakeDirectiveRepository,
    FakeEmbeddingService,
    FakeLanguageDetector,
    FakeLLMService,
    FakeMemoryBriefRepository,
    FakeMemoryRepository,
    FakePersonaRepository,
    FakePersonaSelectionPort,
    FakeRecallGate,
    FakeSessionLogReader,
    FakeSTTService,
    FakeTTSService,
    FakeTurnLogger,
    FakeUserRepository,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _general_assistant() -> AssistantPersona:
    return AssistantPersona.general_assistant("You are a helpful assistant.")


def _user() -> User:
    return User(id=uuid4(), primary_language=Language("en"))


def _concept(name: str, id_: int) -> Concept:
    return Concept(
        id=id_, persona_id=GENERAL_ASSISTANT_ID, name=name,
        description=f"{name} description", language=Language("es"),
    )


def _tutor_persona(is_active: bool = True) -> AssistantPersona:
    now = _now()
    return AssistantPersona(
        id=uuid4(), name="Tutor", system_prompt="You are a language tutor.",
        languages=[Language("es"), Language("en")], response_language=Language("es"),
        voices={"default": "ef_dora"}, is_system=False, created_at=now, updated_at=now,
        strategy="language_tutor", is_active=is_active,
    )


def _directive(target_persona_id: UUID, id_: int = 1) -> Directive:
    """A Directive (FR-207) with a deterministic keyword trigger — matches an
    stt_transcript of "Switch to Tutor" (see _tutor_persona's name)."""
    return Directive(
        id=id_, target_persona_id=target_persona_id, action="switch_persona",
        language=Language("en"), trigger={"keywords": ["switch", "tutor"], "max_words": 6},
        description="Switch to Tutor.",
    )


def _make_start_session(
    user: User | None = None,
    brief: MemoryBrief | None = None,
    previous: SessionInfo | None = None,
    tail_turns: list[Turn] | None = None,
    threshold_hours: float = 24.0,
    directive_repo: FakeDirectiveRepository | None = None,
) -> tuple[StartSession, FakePersonaRepository]:
    persona_repo = FakePersonaRepository()
    persona_repo.save(_general_assistant())
    use_case = StartSession(
        user_repo=FakeUserRepository(user=user or _user()),
        persona_repo=persona_repo,
        memory_brief_repo=FakeMemoryBriefRepository(brief=brief),
        session_log_reader=FakeSessionLogReader(previous=previous, tail=tail_turns),
        directive_repo=directive_repo or FakeDirectiveRepository(),
        session_tail_turns=10,
        session_continuation_threshold_hours=threshold_hours,
    )
    return use_case, persona_repo


def _make_process_turn(
    stt_transcript: str = "hello",
    llm_response: str = "Hello there.",
    detected_language: Language = Language("en"),
    rolling_window_size: int = 100,
    selection_strategies: dict | None = None,
    persona_repo: FakePersonaRepository | None = None,
    tts: FakeTTSService | None = None,
    language_detector: FakeLanguageDetector | None = None,
    default_recall_gate: FakeRecallGate | None = None,
    recall_gates: dict | None = None,
    embedding_service: FakeEmbeddingService | None = None,
    memory_repo: FakeMemoryRepository | None = None,
) -> tuple[ProcessTurn, FakeMemoryRepository, FakeTurnLogger, FakeLLMService]:
    memory_repo = memory_repo or FakeMemoryRepository()
    wal = FakeTurnLogger()
    llm = FakeLLMService(response=llm_response)
    if persona_repo is None:
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
    process_turn = ProcessTurn(
        stt=FakeSTTService(transcript=stt_transcript, language=detected_language),
        llm=llm,
        tts=tts or FakeTTSService(),
        embedding_service=embedding_service or FakeEmbeddingService(),
        memory_repo=memory_repo,
        default_recall_gate=default_recall_gate or FakeRecallGate(),
        persona_repo=persona_repo,
        turn_logger=wal,
        language_detector=language_detector or FakeLanguageDetector(),
        selection_strategies=selection_strategies,
        recall_gates=recall_gates,
        rolling_window_size=rolling_window_size,
    )
    return process_turn, memory_repo, wal, llm


class TestStartSession:
    def test_loads_user_and_general_assistant(self):
        """Spec: TR-301, FR-201"""
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.active_persona.id == GENERAL_ASSISTANT_ID
        assert ctx.memory_brief is None

    def test_injects_memory_brief(self):
        """Spec: FR-109, TR-301"""
        brief = MemoryBrief(content="User likes Python.", created_at=_now(), updated_at=_now())
        use_case, _ = _make_start_session(brief=brief)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.memory_brief is brief

    def test_raises_if_user_missing(self):
        """Spec: TR-301"""
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        use_case = StartSession(
            user_repo=FakeUserRepository(user=None),
            persona_repo=persona_repo,
            memory_brief_repo=FakeMemoryBriefRepository(),
            session_log_reader=FakeSessionLogReader(),
            directive_repo=FakeDirectiveRepository(),
        )
        with pytest.raises(RuntimeError, match="No user record found"):
            use_case.execute(session_id=uuid4(), started_at=_now())

    def test_injects_tail_when_previous_session_within_threshold(self):
        """Spec: FR-109, TR-301"""
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=1), clean_exit=True)
        tail = [Turn(timestamp=now, speaker=Speaker.USER, content="earlier turn")]
        use_case, _ = _make_start_session(previous=previous, tail_turns=tail, threshold_hours=24.0)
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        assert len(ctx.session_tail) == 1
        assert ctx.session_tail[0].content == "earlier turn"

    def test_no_tail_when_previous_session_exceeds_threshold(self):
        """Spec: FR-109, TR-301"""
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=30), clean_exit=True)
        tail = [Turn(timestamp=now, speaker=Speaker.USER, content="old turn")]
        use_case, _ = _make_start_session(previous=previous, tail_turns=tail, threshold_hours=24.0)
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        assert ctx.session_tail == []

    def test_no_tail_when_no_previous_session(self):
        """Spec: FR-109, TR-301"""
        use_case, _ = _make_start_session(previous=None)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.session_tail == []

    def test_no_persona_scaffolding_even_with_tail_from_another_persona(self):
        """Spec: FR-207 — a continued session's tail can show a different persona's
        content (e.g. last time's tutor lesson), but with FR-202/FR-203 retired there
        is no persona-listing/switch-instruction block left at all for that tail to
        prime — the system prompt never names another persona, regardless of tail
        content. This is the actual fix for the language-drift this scaffolding used
        to cause; a directive match (not the LLM) is what decides a switch now."""
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=1), clean_exit=True)
        tail = [Turn(timestamp=now, speaker=Speaker.ASSISTANT, content="Ciao! Ripassiamo il vocabolario.")]
        use_case, persona_repo = _make_start_session(previous=previous, tail_turns=tail, threshold_hours=24.0)
        persona_repo.save(_tutor_persona())
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        assert ctx.active_persona.id == GENERAL_ASSISTANT_ID  # FR-201, still true in-memory

        system_prompt, _ = _compose_working_context(ctx, recalled_memories=[])
        assert "Tutor" not in system_prompt
        assert "Available personas" not in system_prompt
        assert "[PERSONA:" not in system_prompt

    def test_no_selection_batches_fetched_at_session_start(self):
        """Spec: TR-306"""
        # Batches are fetched lazily by ProcessTurn — sessions always start on GA,
        # which has no strategy; a tutor arrives via mid-session switch.
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.selection_batches == {}


class TestProcessTurn:
    @pytest.mark.asyncio
    async def test_basic_turn_produces_audio(self):
        """Spec: TR-302, FR-104"""
        process_turn, _, _, _ = _make_process_turn(stt_transcript="hello", llm_response="Hello there.")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"audio", now=_now())
        assert result is not None
        assert result.audio_chunks
        assert result.assistant_content

    @pytest.mark.asyncio
    async def test_empty_transcript_returns_none(self):
        """Spec: FR-103"""
        process_turn, _, _, _ = _make_process_turn(stt_transcript="   ")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"silence", now=_now())
        assert result is None

    @pytest.mark.asyncio
    async def test_recall_search_enriches_context_with_matching_memory(self):
        """Spec: FR-302, FR-309, TR-302, TR-314"""
        process_turn, memory_repo, _, llm = _make_process_turn(
            stt_transcript="remember when we talked about python",
        )
        memory_repo.search_results = [(0.9, _concept("python tips", id_=1))]
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        _, system_prompt = llm.calls[0]
        assert "python tips" in system_prompt
        # Honesty about memory, found case (TR-303): the reply should be nudged to
        # acknowledge drawing on a past conversation, naturally.
        assert "acknowledge you're drawing on a past conversation" in system_prompt

    @pytest.mark.asyncio
    async def test_recall_attempted_but_nothing_found_prompts_honesty(self):
        """Spec: FR-309, TR-303, TR-314 — a recall attempt that finds nothing must
        say so plainly, not stay silent about it (which reads the same as recall
        never having run at all) and risk the reply guessing instead.

        The miss is modelled the way the real repository produces one — rows returned,
        all of them too far away — not by an empty result list. `search` has no distance
        floor, so with anything at all in the database it always answers with `top_n`
        rows; the previous version of this test set `search_results = []`, a state the
        real repository cannot reach, and so passed against an unreachable branch while
        the live system claimed to remember a subject it had never heard of."""
        process_turn, memory_repo, _, llm = _make_process_turn(
            stt_transcript="remember when we talked about python",
            default_recall_gate=FakeRecallGate(min_similarity=0.85),
        )
        memory_repo.search_results = [
            (0.81, _concept("cerebral health", id_=1)),
            (0.79, _concept("mnemonic techniques", id_=2)),
        ]
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        _, system_prompt = llm.calls[0]
        assert "checked your memory" in system_prompt
        assert "found nothing" in system_prompt
        # And the far-away rows must not be smuggled in alongside the honesty line.
        assert "cerebral health" not in system_prompt

    @pytest.mark.asyncio
    async def test_only_memories_above_the_relevance_floor_are_injected(self):
        """Spec: FR-309, TR-303 — a partial miss keeps the close row and drops the rest,
        rather than presenting all five of what `LIMIT 5` happened to return as
        "relevant memories"."""
        process_turn, memory_repo, _, llm = _make_process_turn(
            stt_transcript="remember when we talked about python",
            default_recall_gate=FakeRecallGate(min_similarity=0.85),
        )
        memory_repo.search_results = [
            (0.91, _concept("python tips", id_=1)),
            (0.80, _concept("cerebral health", id_=2)),
        ]
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        _, system_prompt = llm.calls[0]
        assert "python tips" in system_prompt
        assert "cerebral health" not in system_prompt
        assert "acknowledge you're drawing on a past conversation" in system_prompt

    @pytest.mark.asyncio
    async def test_a_repeated_question_that_found_nothing_is_not_searched_twice(self):
        """Spec: FR-309, TR-314 — the empty result is cached like any other: nothing can
        enter memory mid-session (INV-1), so asking again cannot find anything either."""
        process_turn, memory_repo, _, llm = _make_process_turn(
            stt_transcript="remember when we talked about python",
            default_recall_gate=FakeRecallGate(min_similarity=0.85, should_search_queue=[True, False]),
        )
        memory_repo.search_results = [(0.79, _concept("cerebral health", id_=1))]
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())
        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert len(memory_repo.search_calls) == 1
        _, second_prompt = llm.calls[1]
        assert "found nothing" in second_prompt

    @pytest.mark.asyncio
    async def test_recall_not_attempted_has_no_memory_framing(self):
        """Spec: FR-309, TR-303, TR-314 — an ordinary turn the RecallGate never even
        considers worth checking memory for must get neither the "found" nor the
        "found nothing" framing — only a turn recall actually ran on gets either."""
        process_turn, _, _, llm = _make_process_turn(
            stt_transcript="hello",
            default_recall_gate=FakeRecallGate(should_embed_result=False),
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        _, system_prompt = llm.calls[0]
        assert "checked your memory" not in system_prompt
        assert "drawing on a past conversation" not in system_prompt

    @pytest.mark.asyncio
    async def test_topic_break_tag_is_now_inert(self):
        """Spec: FR-112, TR-304 — the LLM's [TOPIC_BREAK] self-tag was retired with the
        rest of the in-band channel: it had a parser but never a producer (no prompt
        anywhere asked for it), so it only ever fired by accident. A model emitting one
        anyway must cause no boundary — and, per FR-106's marker strip, must not be
        spoken either. Inert in both directions, same posture as the retired [PERSONA:]
        tag below. The only mid-session break left is a persona switch (TR-315)."""
        process_turn, _, wal, _ = _make_process_turn(llm_response="[TOPIC_BREAK] Sure, new topic.")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        await process_turn.execute(ctx, audio=b"a", now=_now())  # advance past first turn
        wal.markers.clear()

        result = await process_turn.execute(ctx, audio=b"b", now=_now())
        assert result is not None
        assert result.conversation_boundary is None
        assert not wal.markers  # the fake records only non-None markers
        assert "TOPIC_BREAK" not in result.assistant_content
        assert "Sure, new topic." in result.assistant_content

    @pytest.mark.asyncio
    async def test_topic_continuation_tag_is_now_inert(self):
        """Spec: FR-112, TR-304 — as above, including on the first turn, which was the
        only position [TOPIC_CONTINUATION] was ever valid in."""
        process_turn, _, wal, _ = _make_process_turn(llm_response="[TOPIC_CONTINUATION] Yes, continuing.")
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())
        assert result is not None
        assert result.conversation_boundary is None
        assert not wal.markers  # the fake records only non-None markers
        assert "TOPIC_CONTINUATION" not in result.assistant_content
        assert "Yes, continuing." in result.assistant_content

    @pytest.mark.asyncio
    async def test_selection_batch_fetched_lazily_on_first_active_turn(self):
        """Spec: TR-306, FR-501"""
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1), context="Anchor: your trip to Madrid.")]
        )
        process_turn, _, _, llm = _make_process_turn(
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert strategy.calls == []  # nothing fetched at session start

        await process_turn.execute(ctx, audio=b"a", now=_now())

        assert strategy.calls == [(GENERAL_ASSISTANT_ID, None, 10)]
        messages, _ = llm.calls[0]
        injected = [m for m in messages if m.role == "system" and "hola" in m.content]
        assert len(injected) == 1
        assert "Anchor: your trip to Madrid." in injected[0].content
        # Injected verbatim as context, never as literal dialogue text
        assert ctx.selection_batches[GENERAL_ASSISTANT_ID] == []  # consumed

    @pytest.mark.asyncio
    async def test_exhausted_batch_is_not_refetched_by_default(self):
        """Spec: TR-306 — the pipeline's default, and as of 2026-08-02 a *declared*
        default (SelectionPacing.refetch_when_exhausted=False) rather than a hard-coded
        rule. A strategy that says nothing still behaves exactly like this."""
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1)), SelectedItem(item=_concept("adios", 2))]
        )
        process_turn, _, _, llm = _make_process_turn(
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())
        assert len(ctx.selection_batches[GENERAL_ASSISTANT_ID]) == 1

        await process_turn.execute(ctx, audio=b"b", now=_now())
        assert ctx.selection_batches[GENERAL_ASSISTANT_ID] == []
        second_messages, _ = llm.calls[1]
        assert any("adios" in m.content for m in second_messages if m.role == "system")

        # Batch exhausted — third turn injects nothing and does NOT re-query.
        await process_turn.execute(ctx, audio=b"c", now=_now())
        third_messages, _ = llm.calls[2]
        assert not any("Work this item" in m.content for m in third_messages if m.role == "system")
        assert len(strategy.calls) == 1

    @pytest.mark.asyncio
    async def test_refetch_when_exhausted_requeries(self):
        """Spec: TR-306 — the other half of the declaration. A persona whose sessions are
        short and frequent enough to exhaust a batch routinely asks for more instead of
        going quiet; the pipeline no longer decides that for everyone."""
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1))],
            pacing=SelectionPacing(refetch_when_exhausted=True),
        )
        process_turn, _, _, _ = _make_process_turn(
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())
        assert len(strategy.calls) == 1
        assert ctx.selection_batches[GENERAL_ASSISTANT_ID] == []  # exhausted immediately

        await process_turn.execute(ctx, audio=b"b", now=_now())
        assert len(strategy.calls) == 2  # re-queried rather than staying silent

    @pytest.mark.asyncio
    async def test_no_fetch_during_onboarding_turn(self):
        """Spec: TR-306"""
        strategy = FakePersonaSelectionPort(items=[SelectedItem(item=_concept("hola", 1))])
        process_turn, _, _, _ = _make_process_turn(
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        use_case, _ = _make_start_session(user=User(id=uuid4(), primary_language=None))
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        assert ctx.needs_onboarding

        await process_turn.execute(ctx, audio=b"a", now=_now())
        assert strategy.calls == []  # onboarding turn — no selection

        await process_turn.execute(ctx, audio=b"b", now=_now())
        assert len(strategy.calls) == 1  # onboarding done, lazy fetch resumes

    @pytest.mark.asyncio
    async def test_switched_persona_batch_fetched_on_its_first_turn(self):
        """Spec: FR-207, TR-306"""
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(_directive(tutor.id))
        strategy = FakePersonaSelectionPort(items=[SelectedItem(item=_concept("hola", 1))])
        process_turn, _, _, llm = _make_process_turn(
            stt_transcript="Switch to Tutor",
            selection_strategies={tutor.id: strategy},
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())
        assert result is not None and result.persona_switched is not None
        assert result.persona_switched.to_persona_id == tutor.id
        assert strategy.calls == []  # switch turn — selection/recall skipped this turn (3b)

        await process_turn.execute(ctx, audio=b"b", now=_now())
        assert strategy.calls == [(tutor.id, None, 10)]
        second_messages, _ = llm.calls[1]
        assert any("hola" in m.content for m in second_messages if m.role == "system")

    @pytest.mark.asyncio
    async def test_persona_switch_forces_conversation_boundary(self):
        """Spec: FR-207, TR-304 — a persona switch always forces a break. Without it,
        consolidation's _group_into_conversations only re-evaluates a conversation's
        persona_id on a BREAK, so a switch mid-session would silently let the new
        persona's turns be attributed to the old persona's conversation record."""
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(_directive(tutor.id))
        process_turn, _, wal, _ = _make_process_turn(
            stt_transcript="Switch to Tutor",
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None and result.persona_switched is not None
        assert result.conversation_boundary is not None
        assert result.conversation_boundary.boundary_type == ConversationBoundaryType.BREAK
        assert any(m == ConversationBoundaryType.BREAK for _, m in wal.markers)

    @pytest.mark.asyncio
    async def test_persona_switch_boundary_overrides_topic_continuation_tag(self):
        """Spec: FR-207, TR-304 — the switch-forced boundary is deterministic and wins
        over whatever the LLM's own [TOPIC_CONTINUATION] tag said, same as the switch
        decision itself is never left to LLM judgment."""
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(_directive(tutor.id))
        process_turn, _, wal, _ = _make_process_turn(
            stt_transcript="Switch to Tutor",
            llm_response="[TOPIC_CONTINUATION] Ciao! Let's continue where we left off.",
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None and result.persona_switched is not None
        assert result.conversation_boundary is not None
        assert result.conversation_boundary.boundary_type == ConversationBoundaryType.BREAK

    @pytest.mark.asyncio
    async def test_focus_marker_is_now_inert(self):
        """Spec: FR-502, TR-304 — the in-band [FOCUS:] channel was retired: it charged
        every persona a lead-in buffer before first audio and leaked into speech whenever
        a multilingual model translated its own control token. A model emitting one
        anyway must not re-fetch the batch — only the lazy default fetch happens — and
        must not be spoken (FR-106's marker strip). Detecting the wish on the user turn
        is the intended replacement, deferred; `select_items(focus=...)` and the strategy
        side that interprets it are deliberately left intact for it."""
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1)), SelectedItem(item=_concept("adios", 2))],
            focused_items=[SelectedItem(item=_concept("repaso", 3))],
        )
        process_turn, _, _, _ = _make_process_turn(
            llm_response="[FOCUS: review known vocabulary] Sure, let me pull up your review items.",
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None
        assert "FOCUS" not in result.assistant_content  # stripped, never spoken
        assert "Sure, let me pull up your review items." in result.assistant_content
        assert strategy.calls == [(GENERAL_ASSISTANT_ID, None, 10)]  # lazy default fetch only
        # Still the DEFAULT batch ("hola" already popped for this turn), never the
        # focused one — "repaso" would mean the retired re-fetch had fired.
        assert [s.item.name for s in ctx.selection_batches[GENERAL_ASSISTANT_ID]] == ["adios"]

    @pytest.mark.asyncio
    async def test_text_around_a_stray_marker_is_still_spoken_intact(self):
        """Spec: FR-106, TR-304 — regression kept from the retired lead-in scan: a marker
        mid-reply must be excised without eating the prose on either side of it."""
        process_turn, _, _, _ = _make_process_turn(
            llm_response="Sure, let's review. [FOCUS: old vocabulary] Perfetto.",
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None
        assert "FOCUS" not in result.assistant_content
        assert "Sure, let's review." in result.assistant_content
        assert "Perfetto." in result.assistant_content

    @pytest.mark.asyncio
    async def test_directive_switch_still_applies_when_reply_carries_a_stray_marker(self):
        """Spec: FR-207, TR-304 — the switch is decided from the user's own utterance
        (step 3b, before the LLM is called), so it is entirely independent of anything
        the reply contains. This used to also assert that a same-turn [FOCUS:] steered
        the NEW persona's batch; that half went with the in-band channel, and what
        remains is the property that actually matters: a stray marker in the reply
        neither blocks nor alters the switch."""
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(_directive(tutor.id))
        strategy = FakePersonaSelectionPort(
            focused_items=[SelectedItem(item=_concept("verbos", 1))],
        )
        process_turn, _, _, _ = _make_process_turn(
            stt_transcript="Switch to Tutor",
            llm_response="[FOCUS: new verbs] Claro, un verbo nuevo.",
            selection_strategies={tutor.id: strategy},
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None and result.persona_switched is not None
        assert result.persona_switched.to_persona_id == tutor.id
        assert "FOCUS" not in result.assistant_content
        assert strategy.calls == []  # a directive turn skips selection entirely (TR-306)

    @pytest.mark.asyncio
    async def test_ordinary_utterance_does_not_trigger_directive_switch(self):
        """Spec: FR-207 — a Directive concept being present this session must not make
        ordinary conversation about the persona's topic a false-positive switch; the
        deterministic keyword/word-count rule (services/directives.matches_trigger),
        not the mere presence of a directive concept, decides."""
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(_directive(tutor.id))
        process_turn, _, _, _ = _make_process_turn(
            stt_transcript="I had a tutor back in school who was really strict",
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None and result.persona_switched is None
        assert ctx.active_persona.id == GENERAL_ASSISTANT_ID

    @pytest.mark.asyncio
    async def test_ambiguous_directive_prefers_more_specific_match(self):
        """Spec: FR-207 — found live-testing 2026-07-30: "Coach" (keywords: switch,
        coach) and "Old Coach" (keywords: switch, old, coach) both match "Switch to
        Old Coach." — Coach's keyword set is a strict subset of Old Coach's. Picking
        the first match in id order silently resolved to the wrong persona (Coach
        was created first); the most-keywords match must win instead."""
        coach = _tutor_persona()  # name doesn't matter here, only its id/languages
        old_coach = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(coach)
        persona_repo.save(old_coach)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(Directive(
            id=1, target_persona_id=coach.id, action="switch_persona",
            language=Language("en"), trigger={"keywords": ["switch", "coach"], "max_words": 6},
            description="Switch to Coach.",
        ))
        directive_repo.directives.append(Directive(
            id=2, target_persona_id=old_coach.id, action="switch_persona",
            language=Language("en"), trigger={"keywords": ["switch", "old", "coach"], "max_words": 6},
            description="Switch to Old Coach.",
        ))
        process_turn, _, _, _ = _make_process_turn(
            stt_transcript="Switch to Old Coach.",
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None and result.persona_switched is not None
        assert result.persona_switched.to_persona_id == old_coach.id
        assert ctx.active_persona.id == old_coach.id

    @pytest.mark.asyncio
    async def test_switch_blocked_when_target_persona_deactivated(self):
        """Spec: FR-207 — a deactivated persona's Directive isn't cleaned up (only
        removal cascades, INV-9), so its trigger still matches; the switch itself must
        be blocked rather than silently reactivating the persona via conversation."""
        tutor = _tutor_persona(is_active=False)
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(_directive(tutor.id))
        process_turn, _, _, _ = _make_process_turn(
            stt_transcript="Switch to Tutor",
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None and result.persona_switched is None
        assert ctx.active_persona.id == GENERAL_ASSISTANT_ID

    @pytest.mark.asyncio
    async def test_blocked_switch_injects_explanatory_context(self):
        """Spec: FR-207 — the reply should be able to explain why the switch didn't
        happen, via a turn-scoped context injection (same mechanism as recalled
        memories/the selected item), not a standing persona-listing block."""
        tutor = _tutor_persona(is_active=False)
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(_directive(tutor.id))
        process_turn, _, _, llm = _make_process_turn(
            stt_transcript="Switch to Tutor",
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        messages, _ = llm.calls[0]
        injected = [
            m for m in messages
            if m.role == "system" and "Tutor" in m.content and "deactivated" in m.content
        ]
        assert len(injected) == 1

    @pytest.mark.asyncio
    async def test_include_activation_turn_brings_content_on_the_switch_turn(self):
        """Spec: FR-207, TR-306 — the gap that made this work unarguable. A persona whose
        session opening IS its first piece of content used to greet empty-handed and get
        its batch only on the *next* turn, burning a turn of a short session. Skipping
        selection on the switch turn was one persona's sensible UX choice, frozen into the
        pipeline for everyone."""
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(_directive(tutor.id))
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1))],
            pacing=SelectionPacing(include_activation_turn=True),
        )
        process_turn, _, _, llm = _make_process_turn(
            stt_transcript="Switch to Tutor",
            selection_strategies={tutor.id: strategy},
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None and result.persona_switched is not None
        assert strategy.calls == [(tutor.id, None, 10)]  # fetched on the activation turn itself
        messages, _ = llm.calls[0]
        assert any("hola" in m.content for m in messages if m.role == "system")

    @pytest.mark.asyncio
    async def test_blocked_switch_never_counts_as_an_activation_turn(self):
        """Spec: FR-207, TR-306 — the exclusion is unconditional, regardless of what the
        persona declared. A blocked switch activates nobody: the active persona is
        explaining that another one is unavailable, which is no moment to also hand it an
        exercise. Here the ACTIVE persona (GA) opts in and must still get nothing."""
        tutor = _tutor_persona(is_active=False)
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(_directive(tutor.id))
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1))],
            pacing=SelectionPacing(include_activation_turn=True),
        )
        process_turn, _, _, _ = _make_process_turn(
            stt_transcript="Switch to Tutor",
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        assert strategy.calls == []

    @pytest.mark.asyncio
    async def test_blocked_switch_skips_recall_and_selection_this_turn(self):
        """Spec: FR-207, TR-306/TR-314 — a blocked switch attempt is still a directive
        utterance, not a content question, same as a real switch."""
        tutor = _tutor_persona(is_active=False)
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(_directive(tutor.id))
        strategy = FakePersonaSelectionPort(items=[SelectedItem(item=_concept("hola", 1))])
        process_turn, memory_repo, _, _ = _make_process_turn(
            stt_transcript="Switch to Tutor",
            selection_strategies={GENERAL_ASSISTANT_ID: strategy},
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        assert strategy.calls == []
        assert memory_repo.search_calls == []

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_persona_tag_in_llm_response_is_now_inert(self):
        """Spec: FR-207, FR-106 — the retired [PERSONA:] tag scheme (FR-202) is no
        longer parsed from the LLM's response at all: a model emitting one anyway (it
        isn't instructed to — no persona-listing scaffolding exists in the prompt any
        more) causes no switch, regardless of position.

        It is also no longer *spoken*. This assertion was inverted on 2026-07-31: the
        tag used to fall through as literal spoken text, which is the same defect as
        the cast-role brackets read aloud in live testing. FR-106's marker strip now
        removes it. Inert means inert in both directions — no switch, no audio.
        """
        tutor = _tutor_persona()
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        persona_repo.save(tutor)
        process_turn, _, _, _ = _make_process_turn(
            llm_response="[PERSONA:Tutor] Hola.",
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert result is not None and result.persona_switched is None
        assert "[PERSONA:Tutor]" not in result.assistant_content  # stripped, never spoken
        assert result.assistant_content == "Hola."

    @pytest.mark.asyncio
    async def test_no_injection_without_selection_batch(self):
        """Spec: TR-306"""
        process_turn, _, _, llm = _make_process_turn()
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())
        messages, _ = llm.calls[0]
        assert not any("Work this item" in m.content for m in messages)

    @pytest.mark.asyncio
    async def test_rolling_window_triggered(self):
        """Spec: FR-110, TR-309"""
        process_turn, _, _, llm = _make_process_turn(llm_response="Got it.", rolling_window_size=2)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        calls_before = len(llm.calls)
        await process_turn.execute(ctx, audio=b"a", now=_now())
        assert len(llm.calls) > calls_before


class TestRecallGating:
    """Spec: FR-309, TR-314 — RecallGate replaces the old per-turn LLM classification
    call with persona-scoped, local threshold logic. See
    tests/unit/infrastructure/test_recall_gate.py for the real gates' own policy
    logic (word count, dedup threshold); these tests only cover how ProcessTurn
    wires a gate's decisions into whether it searches at all."""

    @pytest.mark.asyncio
    async def test_gate_declining_to_embed_means_no_search_call_at_all(self):
        gate = FakeRecallGate(should_embed_result=False)
        process_turn, memory_repo, _, _ = _make_process_turn(default_recall_gate=gate)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert gate.should_embed_calls == ["hello"]
        assert memory_repo.search_calls == []

    @pytest.mark.asyncio
    async def test_gate_declining_to_search_still_calls_should_embed_first(self):
        gate = FakeRecallGate(should_embed_result=True, should_search_result=False)
        process_turn, memory_repo, _, _ = _make_process_turn(default_recall_gate=gate)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert gate.should_embed_calls == ["hello"]
        assert memory_repo.search_calls == []

    @pytest.mark.asyncio
    async def test_first_turn_ever_passes_none_as_max_similarity(self):
        gate = FakeRecallGate()
        process_turn, _, _, _ = _make_process_turn(default_recall_gate=gate)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert gate.should_search_calls == [None]

    @pytest.mark.asyncio
    async def test_declined_search_reuses_last_real_searchs_cached_results(self):
        """The second turn's should_search() call declines — its LLM context must
        still contain what the first (real) search found, not come up empty."""
        gate = FakeRecallGate(should_search_queue=[True, False])
        process_turn, memory_repo, _, llm = _make_process_turn(default_recall_gate=gate)
        memory_repo.search_results = [(0.9, _concept("python tips", id_=1))]
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"first turn", now=_now())
        await process_turn.execute(ctx, audio=b"second turn", now=_now())

        assert len(memory_repo.search_calls) == 1  # only the first turn actually searched
        _, second_system_prompt = llm.calls[1]
        assert "python tips" in second_system_prompt

    @pytest.mark.asyncio
    async def test_declined_search_matches_against_the_whole_session_history_not_just_the_last_entry(self):
        """Spec: FR-309, TR-314 — nothing new can enter memory mid-session (INV-1), so
        comparing against every prior search this session (not only the most recent
        one) is correct, not just an optimisation. Seeds two historical entries: an
        older one whose embedding exactly matches this turn's, and a more recent,
        unrelated one — proves the older match is found and reused, and the unrelated
        one does not leak into context."""
        embedding_service = FakeEmbeddingService(vector=[1.0, 0.0])
        gate = FakeRecallGate(should_embed_result=True, should_search_result=False)
        process_turn, memory_repo, _, llm = _make_process_turn(
            default_recall_gate=gate, embedding_service=embedding_service,
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        old_match = _concept("old topic", id_=1)
        recent_unrelated = _concept("unrelated topic", id_=2)
        ctx.recall_history[GENERAL_ASSISTANT_ID] = [
            ([1.0, 0.0], [old_match]),  # older, identical embedding to this turn's
            ([0.0, 1.0], [recent_unrelated]),  # more recent, but orthogonal (similarity 0)
        ]

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert gate.should_search_calls[-1] == pytest.approx(1.0)
        _, system_prompt = llm.calls[0]
        assert "old topic" in system_prompt
        assert "unrelated topic" not in system_prompt

    @pytest.mark.asyncio
    async def test_persona_specific_gate_overrides_the_default(self):
        """A persona with its own registered RecallGate uses it instead of
        default_recall_gate — mirrors selection_strategies' per-persona lookup."""
        tutor = _tutor_persona()
        tutor_gate = FakeRecallGate(should_embed_result=False)
        default_gate = FakeRecallGate(should_embed_result=True)
        process_turn, memory_repo, _, _ = _make_process_turn(
            default_recall_gate=default_gate,
            recall_gates={tutor.id: tutor_gate},
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        ctx.active_persona = tutor

        await process_turn.execute(ctx, audio=b"audio", now=_now())

        assert tutor_gate.should_embed_calls == ["hello"]
        assert default_gate.should_embed_calls == []
        assert memory_repo.search_calls == []


class TestSpeakerCast:
    """Per-segment language detection — voice switching for the two-teacher cast.
    Deliberately whole-segment granularity, never mid-sentence: a segment that's
    mostly the native language but quotes a target-language word stays in the
    native voice (accented, as a real bilingual guide would sound) — see
    _synthesise_segment's comment in session.py."""

    def _cast_persona(self, voices: dict[str, str]) -> AssistantPersona:
        now = _now()
        return AssistantPersona(
            id=GENERAL_ASSISTANT_ID, name="Vocal Assistant", system_prompt="Teach.",
            languages=[], response_language=Language("es"), voices=voices,
            is_system=True, created_at=now, updated_at=now,
        )

    async def _run(
        self, llm_response: str, voices: dict[str, str], detected: list[str | None] | None = None,
        session_id=None,
    ):
        tts = FakeTTSService()
        detector = FakeLanguageDetector(results=detected)
        process_turn, _, _, _ = _make_process_turn(llm_response=llm_response, tts=tts, language_detector=detector)
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=session_id or uuid4(), started_at=_now())
        ctx.active_persona = self._cast_persona(voices)
        result = await process_turn.execute(ctx, audio=b"a", now=_now())
        return result, tts, detector

    @pytest.mark.asyncio
    async def test_segment_language_switches_voice(self):
        """Spec: FR-205, TR-305"""
        result, tts, _ = await self._run(
            "Hola amigo. Now in your language.",
            voices={"default": "vd", "es": "vt"},
            detected=["es", "en"],
        )
        assert [voice for _, voice, _ in tts.synthesised] == ["vt", "vd"]
        assert "Hola amigo." in result.assistant_content

    @pytest.mark.asyncio
    async def test_low_confidence_detection_keeps_current_voice(self):
        """Spec: TR-305 — a None result (see FakeLanguageDetector/the real detector's
        min-length gate) must not force a switch."""
        _, tts, _ = await self._run(
            "Just a plain answer.",
            voices={"default": "vd", "es": "vt"},
            detected=[None],
        )
        assert [voice for _, voice, _ in tts.synthesised] == ["vd"]

    @pytest.mark.asyncio
    async def test_detected_language_outside_voices_map_falls_back_to_default(self):
        """Spec: FR-205, TR-305 — e.g. the detector's candidate set includes the
        learner's own language, which is never itself a voices key."""
        _, tts, _ = await self._run(
            "Hola.",
            voices={"default": "vd", "es": "vt"},
            detected=["fr"],
        )
        assert [voice for _, voice, _ in tts.synthesised] == ["vd"]

    @pytest.mark.asyncio
    async def test_no_detection_call_when_persona_has_no_cast_voices(self):
        """Spec: TR-305 — a single-voice persona skips detection entirely, not just
        falls back to the default: there is nothing to choose between, so the call could
        only ever confirm the one voice available.

        Reversed on 2026-08-02 and restored the same day; recorded here rather than
        flipped back quietly. For one day detection ran for every persona over every
        installed language, as a safety net for a real symptom — a French voice reading
        English right after a language switch, while the model kept answering in the old
        language for a turn. The safety net worked, but it paid for one persona's problem
        on every segment of every reply, and it put a seven-way guess in front of text
        that only ever had one voice to be spoken by. FR-708 now removes the cause
        instead: the switch must be asked for in the language being switched to, so the
        model's own last user turn agrees with the instruction it was given."""
        _, tts, detector = await self._run(
            "Just a plain answer.",
            voices={"default": "vd"},
        )
        assert detector.calls == []
        assert [voice for _, voice, _ in tts.synthesised] == ["vd"]

    @pytest.mark.asyncio
    async def test_cast_persona_anchor_is_not_overridden_by_the_machine_voice_map(self):
        """Spec: TR-305/TR-307 — for a cast persona the anchor is the deliberate choice
        for every language the cast does not cover, above all the learner's own. Resolving
        it through the machine's language→voice map instead (as it briefly did while
        detection ran for cast-less personas) would silently discard a bundle-chosen
        voice."""
        tts = FakeTTSService()
        detector = FakeLanguageDetector(results=["en"])
        process_turn, _, _, _ = _make_process_turn(
            llm_response="Now a sentence in the learner's own language.",
            tts=tts, language_detector=detector,
        )
        process_turn._default_voice_for = lambda lang: {"en": "af_heart", "es": "ef_dora"}[lang.code]
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        ctx.active_persona = self._cast_persona({"default": "am_narrator", "es": "vt"})

        await process_turn.execute(ctx, audio=b"a", now=_now())

        assert [voice for _, voice, _ in tts.synthesised] == ["am_narrator"]

    @pytest.mark.asyncio
    async def test_detection_candidates_are_native_language_plus_cast_voice_keys(self):
        """Spec: TR-305 — restricting candidates to exactly the languages in play
        (not open-domain detection) is what makes short/ambiguous segments tractable."""
        _, _, detector = await self._run(
            "Hola.",
            voices={"default": "vd", "es": "vt"},
            detected=["es"],
        )
        assert detector.calls[0][1] == ("en", "es")  # native (User.primary_language) + cast key

    @pytest.mark.asyncio
    async def test_single_segment_never_splits_mid_sentence(self):
        """Spec: TR-305 — a sentence mixing languages is spoken whole, in whichever
        voice its own overall detected language resolves to; never split mid-sentence
        even when the model writes a bilingual aside in one sentence."""
        _, tts, _ = await self._run(
            "Listen now, escucha bene, all in one breath.",
            voices={"default": "vd", "es": "vt"},
            detected=["en"],
        )
        assert len(tts.synthesised) == 1
        assert tts.synthesised[0][1] == "vd"

    @pytest.mark.asyncio
    async def test_rotation_pool_resolved_deterministically_per_session(self):
        """Spec: FR-206, TR-307"""
        voices = {"default": "vd", "es": "va|vb"}
        _, tts_even, _ = await self._run("Hola.", voices, detected=["es"], session_id=UUID(int=2))
        _, tts_odd, _ = await self._run("Hola.", voices, detected=["es"], session_id=UUID(int=3))
        assert [v for _, v, _ in tts_even.synthesised] == ["va"]
        assert [v for _, v, _ in tts_odd.synthesised] == ["vb"]


class TestResponseLanguageInstruction:
    """Every persona, GA included, is instructed in its own fixed
    `response_language` (FR-105) — detection-independent, and only ever changes on
    explicit request (INV-14). GA mirroring/uninstalled-language reminder
    (formerly FR-113/TR-313) was retired: this replaces its coverage."""

    @pytest.mark.asyncio
    async def test_ga_instruction_ignores_detected_language(self):
        """Spec: FR-105, INV-14 — the GA's system-prompt instruction and TTS voice
        both stay pinned to its own response_language/default voice no matter what
        language the user is detected speaking."""
        for detected in (Language("es"), Language("de")):
            tts = FakeTTSService()
            process_turn, _, _, llm = _make_process_turn(detected_language=detected, tts=tts)
            use_case, _ = _make_start_session()
            ctx = use_case.execute(session_id=uuid4(), started_at=_now())

            await process_turn.execute(ctx, audio=b"hola", now=_now())

            _, system_prompt = llm.calls[0]
            assert "Always respond in the language with IETF code 'en'" in system_prompt
            assert "currently speaking" not in system_prompt
            assert "not installed" not in system_prompt
            assert all(voice == "af_heart" for _, voice, _ in tts.synthesised)

    @pytest.mark.asyncio
    async def test_tutor_persona_keeps_its_own_response_language(self):
        """Spec: FR-105 — a strategy persona (tutor) is instructed in its
        configured response language whatever the user speaks."""
        process_turn, _, _, llm = _make_process_turn(detected_language=Language("en"))
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        ctx.active_persona = _tutor_persona()

        await process_turn.execute(ctx, audio=b"hello", now=_now())

        _, system_prompt = llm.calls[0]
        assert "Always respond in the language with IETF code 'es'" in system_prompt


class TestUserPersonalization:
    """Every persona may address the user by User.name — generic, not gated on
    any persona property (unlike the response-language instruction above)."""

    def test_system_prompt_includes_users_name(self):
        """Spec: FR-007"""
        user = User(id=uuid4(), primary_language=Language("en"), name="Alice")
        use_case, _ = _make_start_session(user=user)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        system_prompt, _ = _compose_working_context(ctx, recalled_memories=[])

        assert "Alice" in system_prompt

    def test_default_seeded_name_is_used_when_none_was_ever_changed(self):
        """Spec: FR-007"""
        user = User(id=uuid4(), primary_language=Language("en"))
        use_case, _ = _make_start_session(user=user)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        system_prompt, _ = _compose_working_context(ctx, recalled_memories=[])

        assert "user-1" in system_prompt


def _lang_aware_general_assistant() -> AssistantPersona:
    """GA, but declaring it wants [lang:] tags — stands in for a language-sensitive
    persona without dragging the tutor's whole fixture into these tests."""
    persona = _general_assistant()
    persona.sees_turn_language = True
    return persona


class TestLanguageTags:
    """User-turn [lang:code] tags (FR-114), rendered only for a persona that declares
    `sees_turn_language`: a language-sensitive persona reads them as
    production/aside/pronunciation-stumble evidence. Rendering-only: stored content and
    logs stay clean, and a mimicked tag in the response is stripped before TTS like
    every other bracket marker."""

    @pytest.mark.asyncio
    async def test_user_turn_rendered_with_language_tag(self):
        """Spec: FR-114, TR-303"""
        persona_repo = FakePersonaRepository()
        persona_repo.save(_lang_aware_general_assistant())
        process_turn, _, wal, llm = _make_process_turn(
            stt_transcript="vorrei un caffè", detected_language=Language("it"),
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())
        ctx.active_persona = persona_repo.get(GENERAL_ASSISTANT_ID)

        await process_turn.execute(ctx, audio=b"a", now=_now())

        messages, _ = llm.calls[0]
        user_messages = [m for m in messages if m.role == "user"]
        assert user_messages[-1].content == "[lang:it] vorrei un caffè"
        # Rendering-only: the stored/logged turn content carries no tag.
        logged_user_turns = [t for _, t in wal.written if t.speaker == Speaker.USER]
        assert logged_user_turns[-1].content == "vorrei un caffè"

    @pytest.mark.asyncio
    async def test_session_tail_user_turns_tagged_assistant_untagged(self):
        """Spec: FR-114, TR-303"""
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=1), clean_exit=True)
        tail = [
            Turn(timestamp=now, speaker=Speaker.USER, content="ciao", language=Language("it")),
            Turn(timestamp=now, speaker=Speaker.ASSISTANT, content="Ciao! Ben fatto."),
        ]
        persona_repo = FakePersonaRepository()
        persona_repo.save(_lang_aware_general_assistant())
        process_turn, _, _, llm = _make_process_turn(persona_repo=persona_repo)
        use_case, _ = _make_start_session(previous=previous, tail_turns=tail)
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        ctx.active_persona = persona_repo.get(GENERAL_ASSISTANT_ID)

        await process_turn.execute(ctx, audio=b"a", now=now)

        messages, _ = llm.calls[0]
        tail_message = next(m for m in messages if "Tail of previous session" in m.content)
        assert "user: [lang:it] ciao" in tail_message.content
        assert "assistant: Ciao! Ben fatto." in tail_message.content

    @pytest.mark.asyncio
    async def test_persona_that_does_not_declare_it_never_sees_a_language_tag(self):
        """Spec: FR-114 — the default, and the fix for a real 2026-08-02 session: STT
        misdetected an English sentence as French, and the model read the resulting
        [lang:fr] as an instruction, switching language mid-conversation and overriding
        its own standing response-language rule. A persona that cannot use the signal is
        no longer shown it — the FR-207/TR-304 lesson applied again: do not emit a signal
        and then ask the model to ignore it."""
        process_turn, _, _, llm = _make_process_turn(
            stt_transcript="Can you explain me TLS please ?", detected_language=Language("fr"),
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        messages, _ = llm.calls[0]
        user_messages = [m for m in messages if m.role == "user"]
        assert user_messages[-1].content == "Can you explain me TLS please ?"
        assert "lang:" not in user_messages[-1].content

    @pytest.mark.asyncio
    async def test_user_turn_without_language_rendered_untagged(self):
        """Spec: FR-114 — no detected language, no tag (e.g. tail lines from logs
        written before the language field existed)."""
        now = _now()
        previous = SessionInfo(session_id=uuid4(), ended_at=now - timedelta(hours=1), clean_exit=True)
        tail = [Turn(timestamp=now, speaker=Speaker.USER, content="hello there")]
        process_turn, _, _, llm = _make_process_turn()
        use_case, _ = _make_start_session(previous=previous, tail_turns=tail)
        ctx = use_case.execute(session_id=uuid4(), started_at=now)

        await process_turn.execute(ctx, audio=b"a", now=now)

        messages, _ = llm.calls[0]
        tail_message = next(m for m in messages if "Tail of previous session" in m.content)
        assert "user: hello there" in tail_message.content
        assert "[lang:" not in tail_message.content

    @pytest.mark.asyncio
    async def test_mimicked_lang_tag_in_response_is_never_spoken(self):
        """Spec: FR-114, TR-308 — a model imitating the inbound tag convention must
        not have the tag read aloud or logged."""
        tts = FakeTTSService()
        process_turn, _, _, _ = _make_process_turn(
            llm_response="[lang:en] Hello there. [lang:it] Ciao a tutti.", tts=tts,
        )
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        result = await process_turn.execute(ctx, audio=b"a", now=_now())

        assert "[lang:" not in result.assistant_content
        assert all("[lang:" not in text for text, _, _ in tts.synthesised)
        assert "Hello there." in result.assistant_content
        assert "Ciao a tutti." in result.assistant_content

    @pytest.mark.asyncio
    async def test_cast_persona_gets_no_generic_response_language_instruction(self):
        """Spec: FR-105, TR-303 — a two-teacher cast deliberately speaks two
        languages per reply; the persona's own prompt owns language use, so the
        generic 'always respond in X' directive is suppressed."""
        now = _now()
        cast_tutor = AssistantPersona(
            id=uuid4(), name="Tutor", system_prompt="Teach Italian.",
            languages=[Language("it"), Language("en")], response_language=Language("it"),
            voices={"default": "vd", "it": "vt"}, is_system=False,
            created_at=now, updated_at=now, strategy="language_tutor",
        )
        process_turn, _, _, llm = _make_process_turn()
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=now)
        ctx.active_persona = cast_tutor

        await process_turn.execute(ctx, audio=b"a", now=now)

        _, system_prompt = llm.calls[0]
        assert "Always respond in the language" not in system_prompt


class TestEndSession:
    def test_turn_logger_closed_with_clean_exit(self):
        """Spec: TR-402"""
        turn_logger = FakeTurnLogger()
        session_id = uuid4()
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=session_id, started_at=_now())

        ended_at = _now()
        EndSession(turn_logger=turn_logger).execute(ctx, ended_at=ended_at)

        assert turn_logger.closed.get(session_id) == ended_at
        assert turn_logger.clean_exits.get(session_id) is True


class TestGeneralAssistantIsolation:
    """Spec: FR-201, INV-6 — insurance taken 2026-08-02, before building a second
    non-tutor persona.

    GA is not special-cased anywhere in `ProcessTurn`. It is simply the persona with no
    registered strategies, so **the shared turn pipeline IS GA's code path** — which makes
    every future persona's needs a potential edit to the code GA runs. That is the whole
    argument behind `DefaultPersona` in docs/BRIEF_persona_contract.md; until that lands,
    this class is the guard.

    Deliberately adversarial: the second persona is registered with its own directive, its
    own selection strategy, **non-default pacing on both flags**, and its own episode
    declaration — every extension point added this session. None of it may reach GA.
    `test_no_persona_leak.py` guards persona *vocabulary* in generic code; this guards
    persona *behaviour* leaking into another persona's turn, which no test covered before.
    """

    _FIXED_TIME = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)
    _UTTERANCE = "I have been thinking about the trip we planned together last spring."

    async def _ga_turn(self, *, with_second_persona: bool):
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        directive_repo = FakeDirectiveRepository()
        strategy = FakePersonaSelectionPort(
            items=[SelectedItem(item=_concept("hola", 1))],
            pacing=SelectionPacing(refetch_when_exhausted=True, include_activation_turn=True),
        )
        selection_strategies = None
        if with_second_persona:
            other = _tutor_persona()
            other.extracts_episodes = False
            persona_repo.save(other)
            directive_repo.directives.append(_directive(other.id))
            selection_strategies = {other.id: strategy}

        process_turn, memory_repo, wal, llm = _make_process_turn(
            stt_transcript=self._UTTERANCE,
            selection_strategies=selection_strategies,
            persona_repo=persona_repo,
        )
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=UUID(int=7), started_at=self._FIXED_TIME)
        result = await process_turn.execute(ctx, audio=b"a", now=self._FIXED_TIME)
        return result, llm.calls, strategy, ctx, wal

    @pytest.mark.asyncio
    async def test_ga_prompt_and_messages_are_identical_with_a_second_persona(self):
        """The load-bearing assertion: the entire LLM call — system prompt and every
        message — must be byte-identical. A second persona must not add so much as a word
        to GA's context, which is also the FR-202/FR-203 language-drift lesson restated as
        a test rather than a comment."""
        _, alone_calls, _, _, _ = await self._ga_turn(with_second_persona=False)
        _, with_other_calls, _, _, _ = await self._ga_turn(with_second_persona=True)

        assert alone_calls == with_other_calls

    @pytest.mark.asyncio
    async def test_second_personas_strategy_is_never_consulted_on_a_ga_turn(self):
        """Its pacing declares include_activation_turn and refetch_when_exhausted; neither
        may cause its selection strategy to run while GA is the active persona."""
        _, _, strategy, ctx, _ = await self._ga_turn(with_second_persona=True)

        assert strategy.calls == []
        assert ctx.selection_batches == {}  # GA has no strategy, so no batch exists at all

    @pytest.mark.asyncio
    async def test_ga_turn_outcome_is_identical_with_a_second_persona(self):
        """Everything observable downstream of the turn: persona switch, conversation
        boundary, and what was written to the session log.

        Weaker than the two above by construction, and worth knowing why: `FakeLLMService`
        returns a canned response whatever prompt it is handed, so `assistant_content`
        cannot detect a changed prompt — that is the first test's job. What this one
        actually guards is the *other* axis: that a second persona's mere existence never
        produces a spurious switch, boundary marker, or extra log write."""
        alone, _, _, _, alone_wal = await self._ga_turn(with_second_persona=False)
        with_other, _, _, _, other_wal = await self._ga_turn(with_second_persona=True)

        assert alone is not None and with_other is not None
        assert alone.assistant_content == with_other.assistant_content
        assert alone.persona_switched is None and with_other.persona_switched is None
        assert alone.conversation_boundary is None and with_other.conversation_boundary is None
        assert alone_wal.markers == other_wal.markers == []
        assert [t.content for _, t in alone_wal.written] == [t.content for _, t in other_wal.written]


class TestLanguageDirective:
    """Spec: FR-701, INV-1 — the first voice-configurable setting, and the reason INV-1
    now enumerates configuration writes as its one live-path exception."""

    def _directive(self, target: str, keywords: list[str]) -> Directive:
        return Directive(
            id=99, target_persona_id=None, action="set_response_language",
            language=Language("en"), trigger={"keywords": keywords, "max_words": 6},
            description=f"Speak {target}.", target_language=Language(target),
        )

    def _make(self, *, installed: list[str], transcript: str):
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(self._directive("fr", ["speak", "french"]))
        process_turn, _, _, llm = _make_process_turn(
            stt_transcript=transcript, persona_repo=persona_repo,
        )
        process_turn._installed_languages = [Language(c) for c in installed]
        process_turn._default_voice_for = lambda lang: {"fr": "ff_siwis"}.get(lang.code, "af_heart")
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        return process_turn, persona_repo, llm, use_case

    @pytest.mark.asyncio
    async def test_changes_response_language_and_voice_and_persists(self):
        """The whole point: it must survive the session, so it is written to the persona
        row rather than held in working memory."""
        process_turn, persona_repo, _, use_case = self._make(
            installed=["en", "fr"], transcript="Speak to me in French please",
        )
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        saved = persona_repo.get(GENERAL_ASSISTANT_ID)
        assert saved.response_language == Language("fr")
        assert saved.default_voice == "ff_siwis"  # voice follows the language, or you hear an accent
        assert ctx.active_persona.response_language == Language("fr")  # in effect this turn, not the next

    @pytest.mark.asyncio
    async def test_reply_is_already_in_the_new_language(self):
        """Same posture as a persona switch, whose confirmation is spoken BY the persona
        just switched to (FR-207): the response-language instruction in this turn's own
        system prompt is already the new language."""
        process_turn, _, llm, use_case = self._make(
            installed=["en", "fr"], transcript="Speak to me in French please",
        )
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        _, system_prompt = llm.calls[0]
        assert "'fr'" in system_prompt

    @pytest.mark.asyncio
    async def test_uninstalled_language_is_refused_without_switching(self):
        """Spec: FR-705 — these directives are only generated for installed languages,
        but a row outlives the config that made it. Switching to a language with no voice
        on this machine would produce silence rather than speech, so it is re-checked
        here and refused."""
        process_turn, persona_repo, llm, use_case = self._make(
            installed=["en"], transcript="Speak to me in French please",
        )
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        assert persona_repo.get(GENERAL_ASSISTANT_ID).response_language == Language("en")
        messages, _ = llm.calls[0]
        assert any("not installed" in m.content for m in messages if m.role == "system")

    @pytest.mark.asyncio
    async def test_language_turn_skips_recall_and_selection(self):
        """Spec: TR-306/TR-314 — a configuration request is not a content question, same
        as a persona switch."""
        strategy = FakePersonaSelectionPort(items=[SelectedItem(item=_concept("hola", 1))])
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(self._directive("fr", ["speak", "french"]))
        process_turn, memory_repo, _, _ = _make_process_turn(
            stt_transcript="Speak to me in French please",
            persona_repo=persona_repo, selection_strategies={GENERAL_ASSISTANT_ID: strategy},
        )
        process_turn._installed_languages = [Language("en"), Language("fr")]
        process_turn._default_voice_for = lambda lang: "ff_siwis"
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        assert strategy.calls == []
        assert memory_repo.search_calls == []

    @pytest.mark.asyncio
    async def test_asking_for_the_current_language_is_a_silent_no_op(self):
        """No write, no confirmation — the same silent no-op an already-active persona
        target gets."""
        persona_repo = FakePersonaRepository()
        persona_repo.save(_general_assistant())
        directive_repo = FakeDirectiveRepository()
        directive_repo.directives.append(self._directive("en", ["speak", "english"]))
        process_turn, _, _, llm = _make_process_turn(
            stt_transcript="Speak to me in English please", persona_repo=persona_repo,
        )
        process_turn._installed_languages = [Language("en")]
        process_turn._default_voice_for = lambda lang: "af_heart"
        use_case, _ = _make_start_session(directive_repo=directive_repo)
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        await process_turn.execute(ctx, audio=b"a", now=_now())

        messages, _ = llm.calls[0]
        assert not any("has now been done" in m.content for m in messages if m.role == "system")


class TestAudioStreaming:
    """Spec: TR-316, TR-105, FR-104 — audio leaves the turn as it is produced.

    These exist because the property they check was lost silently. The pre-refactor server
    synthesised each sentence inside the token loop and sent it immediately; commit 5e83853
    moved synthesis behind `TurnResult.audio_chunks`, and every test kept passing because
    the audio was all still *there* — just not until the whole reply had finished. On a
    turn whose generation takes ~16s, that is the entire wait (measured 2026-08-04).

    So asserting "audio was produced" is exactly the assertion that missed it. What these
    assert is *when* it left, relative to generation.
    """

    class _SlowLLM:
        """Records the order of its own yields against whatever else happens between them.

        The shared `events` log is the instrument: it interleaves LLM tokens with sink
        deliveries, so a test can tell "sent during generation" from "sent after it".
        """

        def __init__(self, sentences: list[str], events: list[str]) -> None:
            self.sentences = sentences
            self.events = events
            self.calls: list[tuple] = []

        async def complete(self, messages, system_prompt):
            self.calls.append((messages, system_prompt))
            for i, sentence in enumerate(self.sentences):
                self.events.append(f"token{i}")
                yield sentence + " "

    def _streaming_turn(self, sentences: list[str]):
        events: list[str] = []
        llm = self._SlowLLM(sentences, events)
        process_turn, _, _, _ = _make_process_turn(stt_transcript="hello")
        process_turn._llm = llm
        use_case, _ = _make_start_session()
        ctx = use_case.execute(session_id=uuid4(), started_at=_now())

        sent: list[bytes] = []

        async def sink(chunk: bytes) -> None:
            events.append(f"audio{len(sent)}")
            sent.append(chunk)

        return process_turn, ctx, sink, sent, events

    @pytest.mark.asyncio
    async def test_first_audio_is_sent_before_generation_finishes(self):
        """Spec: TR-316 — the whole point. The first sentence must reach the client while
        the model is still writing the rest, not after."""
        pt, ctx, sink, sent, events = self._streaming_turn(
            ["One sentence.", "Two sentence.", "Three sentence."]
        )

        await pt.execute(ctx, audio=b"a", now=_now(), emit_audio=sink)

        assert events.index("audio0") < events.index("token2"), events

    @pytest.mark.asyncio
    async def test_every_sentence_is_sent_and_in_order(self):
        """Spec: TR-105 — streaming must not drop or reorder segments; the client plays
        them in arrival order and cannot reassemble them."""
        pt, ctx, sink, sent, events = self._streaming_turn(
            ["Alpha here.", "Beta here.", "Gamma here."]
        )

        await pt.execute(ctx, audio=b"a", now=_now(), emit_audio=sink)

        assert len(sent) == 3
        assert [e for e in events if e.startswith("audio")] == ["audio0", "audio1", "audio2"]

    @pytest.mark.asyncio
    async def test_streaming_leaves_audio_chunks_empty(self):
        """Spec: TR-316 — the handler must not be able to send the same audio twice. The
        contract is that supplying a sink empties this, so a caller that (wrongly) also
        iterates it plays silence rather than every sentence doubled."""
        pt, ctx, sink, sent, _ = self._streaming_turn(["Only one."])

        result = await pt.execute(ctx, audio=b"a", now=_now(), emit_audio=sink)

        assert sent
        assert result is not None
        assert result.audio_chunks == []

    @pytest.mark.asyncio
    async def test_without_a_sink_audio_still_accumulates(self):
        """Spec: TR-316 — the no-sink path stays intact for callers that want the whole
        reply in hand (the LLM quality gates do)."""
        pt, ctx, _, _, _ = self._streaming_turn(["Alpha here.", "Beta here."])

        result = await pt.execute(ctx, audio=b"a", now=_now())

        assert result is not None
        assert len(result.audio_chunks) == 2

    @pytest.mark.asyncio
    async def test_a_trailing_fragment_is_sent_too(self):
        """Spec: TR-316 — a reply whose last sentence has no terminal punctuation is
        flushed after the loop; that flush must go through the sink like the rest, or the
        final words are silently dropped."""
        pt, ctx, sink, sent, _ = self._streaming_turn(["Complete one.", "then a fragment"])

        await pt.execute(ctx, audio=b"a", now=_now(), emit_audio=sink)

        assert len(sent) == 2

    @pytest.mark.asyncio
    async def test_a_failing_sink_does_not_lose_what_was_already_sent(self):
        """Spec: TR-316 — a client that disconnects mid-reply raises out of the sink. The
        turn fails (server.py logs it and still sends speaking_end), but the segments
        already delivered stay delivered — the failure truncates the reply rather than
        making the whole turn silent, which is why the handler no longer needs the audio
        back to recover."""
        pt, ctx, _, _, events = self._streaming_turn(["First one.", "Second one."])
        delivered: list[bytes] = []

        async def failing_sink(chunk: bytes) -> None:
            if delivered:
                raise ConnectionError("client went away")
            delivered.append(chunk)

        with pytest.raises(ConnectionError):
            await pt.execute(ctx, audio=b"a", now=_now(), emit_audio=failing_sink)

        assert len(delivered) == 1
