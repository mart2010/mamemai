# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Phase 6 end-to-end test: two sessions, second session's LLM context contains the
first session's memory brief. Real Postgres throughout (User/Persona/MemoryBrief
repos, StartSession, the real _compose_working_context prompt assembly) — only the
LLM call inside GenerateMemoryBrief is a Fake, since what the LLM would actually say
is not what this test is verifying; the plumbing that gets its answer into the next
session's prompt is."""
from datetime import UTC, datetime
from uuid import uuid4

import psycopg

from mamemai_server.domain.model import AssistantPersona, Concept, Conversation, Episode, Language, User
from mamemai_server.infrastructure.postgres import (
    PSConversationRepository,
    PSDirectiveRepository,
    PSMemoryBriefRepository,
    PSMemoryRepository,
    PSPersonaRepository,
    PSUserRepository,
)
from mamemai_server.services.memory import GenerateMemoryBrief
from mamemai_server.services.session import StartSession, _compose_working_context

from tests.fakes.fakes import FakeLLMService, FakeSessionLogReader

_NOW = datetime.now(UTC)
_EMBEDDING_DIM = 1024  # must match the schema's vector(1024) columns


async def test_second_session_llm_context_contains_first_sessions_brief(pg_conn: psycopg.Connection) -> None:
    """Spec: FR-109, FR-308, TR-303"""
    persona = AssistantPersona.general_assistant(system_prompt="You are a helpful assistant.")
    PSPersonaRepository(pg_conn).save(persona)
    user = User(id=uuid4(), primary_language=Language("en"))
    PSUserRepository(pg_conn).save(user)

    # --- End of session 1: generate and persist the memory brief ---
    # Real memory to summarise. Without it there is nothing to build a brief from and
    # execute() correctly declines to write one (FR-308) — see the dedicated unit tests.
    memory_repo = PSMemoryRepository(pg_conn)
    # Episodes carry real provenance (origin_conversation_id is FK-backed).
    conversation_id = PSConversationRepository(pg_conn).save_new(
        Conversation(id=None, started_at=_NOW, persona_id=persona.id, ended_at=_NOW),
        session_id=uuid4(),
    )
    memory_repo.upsert_episode(Episode(
        id=None, summary="Hiked in the Alps last weekend.", happened_at=_NOW,
        origin_conversation_id=conversation_id, embedding=[0.1] * _EMBEDDING_DIM,
    ))
    memory_repo.upsert_concept(Concept(
        id=None, persona_id=persona.id, name="local voice assistant",
        description="The user is building one that runs entirely offline.",
        language=Language("en"), origin="organic", embedding=[0.2] * _EMBEDDING_DIM,
    ))

    brief_content = "Martin enjoys hiking in the Alps and is building a local voice assistant."
    fake_llm = FakeLLMService(response=brief_content)
    generate_brief = GenerateMemoryBrief(
        llm=fake_llm,
        memory_brief_repo=PSMemoryBriefRepository(pg_conn),
        memory_repo=memory_repo,
    )
    await generate_brief.execute(generated_at=_NOW)

    # The hole this test used to have: FakeLLMService answers the same canned brief no
    # matter what it is asked, so asserting only on its output cannot tell a prompt built
    # from real memory apart from an empty one — which is exactly how a GenerateMemoryBrief
    # that never read memory at all survived from the first commit to 2026-08-02.
    (messages, _system_prompt), = fake_llm.calls
    assert "Hiked in the Alps last weekend." in messages[0].content
    assert "local voice assistant" in messages[0].content

    # --- Session 2 starts: StartSession pulls the brief from real Postgres ---
    start_session = StartSession(
        user_repo=PSUserRepository(pg_conn),
        persona_repo=PSPersonaRepository(pg_conn),
        memory_brief_repo=PSMemoryBriefRepository(pg_conn),
        session_log_reader=FakeSessionLogReader(),
        directive_repo=PSDirectiveRepository(pg_conn),
    )
    wm = start_session.execute(session_id=uuid4(), started_at=_NOW)

    assert wm.memory_brief is not None
    assert wm.memory_brief.content == brief_content

    # --- The literal claim under test: the brief actually reaches the LLM's context ---
    system_prompt, _messages = _compose_working_context(wm, recalled_memories=[])
    assert brief_content in system_prompt
