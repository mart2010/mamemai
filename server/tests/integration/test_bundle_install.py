# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""End-to-end bundle install against real Postgres: real TOML reader on the committed
spanish_mini fixture, real repositories/UnitOfWork/pgvector upsert path. Fakes only for
the LLM-dependent ports (disambiguation, synthesis) — same posture as the consolidation
pipeline test. Embeddings use a deterministic hash-seeded fake: distinct texts get
near-orthogonal vectors (auto-insert), identical text gets the identical vector
(reinstall → similarity 1.0 → exact-duplicate merge) — so the full insert/merge
behaviour is exercised without loading the real model."""
import math
import random
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from mamemai_server.domain.model import AssistantPersona, EngagementLevel, Language, MemoryType, User
from mamemai_server.infrastructure.bundle_toml import TomlPersonaBundleSource
from mamemai_server.infrastructure.postgres import (
    PSBundleInstallLog,
    PSDirectiveRepository,
    PSMemoryRepository,
    PSPersonaRepository,
    PSUnitOfWork,
    PSUserRepository,
)
from mamemai_server.services.bundle_install import InstallPersonaBundle
from mamemai_server.services.directives import PersonaDirectiveSync
from mamemai_server.services.upsert import MemoryUpserter

from tests.fakes.fakes import FakeDisambiguationEvaluator, FakeMemorySynthesizer

FIXTURE_BUNDLE = Path(__file__).parent / "fixtures" / "spanish_mini"
PERSONA_KEY = "mamemai-test/spanish-mini"
_DIM = 1024


class HashEmbeddingService:
    """Deterministic stand-in for the real model: identical text always yields the same
    vector, unrelated text is near-orthogonal. That is all the bundle installer needs —
    it exercises dedupe/idempotence, not similarity quality.

    Both methods hash the bare text, deliberately: the prefixes (TR-509) are the real
    model's business, and adding them here would only make identical text embed
    differently by role without changing anything this test asserts.
    """

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_passage(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        rng = random.Random(text)  # str seeds hash deterministically across runs
        v = [rng.gauss(0.0, 1.0) for _ in range(_DIM)]
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v]


def _make_installer(conn: psycopg.Connection, synthesizer: FakeMemorySynthesizer) -> InstallPersonaBundle:
    return InstallPersonaBundle(
        bundle_source=TomlPersonaBundleSource(),
        persona_repo=PSPersonaRepository(conn),
        user_repo=PSUserRepository(conn),
        upserter=MemoryUpserter(
            PSMemoryRepository(conn),
            HashEmbeddingService(),
            FakeDisambiguationEvaluator(),
            synthesizer,
        ),
        unit_of_work=PSUnitOfWork(conn),
        install_log=PSBundleInstallLog(conn),
        directive_sync=PersonaDirectiveSync(PSDirectiveRepository(conn)),
        default_voice_for=lambda language: "ff_siwis",
    )


@pytest.fixture
def onboarded_user(pg_conn: psycopg.Connection) -> User:
    user = User(id=uuid4(), primary_language=Language("fr"))
    PSUserRepository(pg_conn).save(user)
    # GA must exist for this fixture's namesake claim ("onboarded") to be real — a real
    # system always has it seeded (migration seed data). Not itself an FK precondition
    # for the bundle's own Directive row anymore (FR-207: `directives.target_persona_id`
    # now points at the *installed* persona's own id, not GA's, since Directive moved
    # off the GA-owned-Concept scheme onto its own first-class table).
    PSPersonaRepository(pg_conn).save(AssistantPersona.general_assistant("You are a helpful assistant."))
    return user


class TestBundleInstallPipeline:
    def test_fresh_install_creates_persona_and_content(
        self, pg_conn: psycopg.Connection, onboarded_user: User
    ) -> None:
        """Spec: FR-601, FR-602, FR-606, TR-904"""
        installer = _make_installer(pg_conn, FakeMemorySynthesizer())

        result = installer.execute(FIXTURE_BUNDLE)

        assert result.persona_created is True
        assert result.items_inserted == 5
        assert result.items_merged == 0

        persona = PSPersonaRepository(pg_conn).get_by_key(PERSONA_KEY)
        assert persona is not None
        assert persona.name == "Profesora Mini"
        # Pair-independence: "default" omitted in the bundle, derived at install.
        assert persona.voices == {"target_teacher": "ef_dora", "default": "ff_siwis"}
        # Bundle targets + User.primary_language.
        assert persona.languages == [Language("es"), Language("fr")]
        # Settings copied verbatim, including the learner-language-keyed map.
        assert persona.settings == {
            "elicitation_cap": 2,
            "pair_difficulty": {"en": 1.0, "fr": 1.2, "*": 1.5},
        }

        # Insertion order is the contract: lesson-filename sort → item order → ascending
        # SERIAL id (curriculum order for Phase 12's UNSEEN tiebreak). The "switch to
        # me" Directive (FR-207) also created alongside this persona lives in its own
        # `directives` table now, so this query needs no filtering to exclude it.
        with pg_conn.cursor() as cur:
            cur.execute("SELECT name, engagement_level, category FROM concepts WHERE persona_id = %s ORDER BY id", (persona.id,))
            concepts = cur.fetchall()
            cur.execute("SELECT name, engagement_level, steps FROM procedures WHERE persona_id = %s ORDER BY id", (persona.id,))
            procedures = cur.fetchall()
        assert [c[0] for c in concepts] == ["hola", "buenos días", "el café"]
        assert [p[0] for p in procedures] == ["greeting someone politely", "ordering a coffee"]
        # A bundle cannot claim the user knows things.
        assert all(c[1] == "unseen" for c in concepts)
        assert all(p[1] == "unseen" for p in procedures)
        assert concepts[0][2] == "function_word"
        assert procedures[1][2] == ["perdone", "quisiera un café, por favor", "gracias"]

        # Items are queryable through the real pgvector search path.
        memory_repo = PSMemoryRepository(pg_conn)
        # embed_passage, matching how the installer stored it: this searches for a row by
        # reproducing its own stored vector, not by asking a question about it.
        query = HashEmbeddingService().embed_passage(
            "hola: 'Hola' is the universal Spanish greeting, usable at any time of day "
            "in both formal and informal situations."
        )
        [(similarity, found)] = memory_repo.search(
            embedding=query, memory_types=(MemoryType.CONCEPT,), top_n=1, persona_id=persona.id
        )
        assert similarity == pytest.approx(1.0, abs=1e-5)
        assert found.name == "hola"
        assert found.engagement_level == EngagementLevel.UNSEEN

    def test_reinstall_is_idempotent_and_skips_synthesis(
        self, pg_conn: psycopg.Connection, onboarded_user: User
    ) -> None:
        """Spec: FR-603, TR-604"""
        synthesizer = FakeMemorySynthesizer()
        installer = _make_installer(pg_conn, synthesizer)

        first = installer.execute(FIXTURE_BUNDLE)
        second = installer.execute(FIXTURE_BUNDLE)

        assert (first.items_inserted, first.items_merged) == (5, 0)
        # Recovery contract: re-running merges every item into itself…
        assert (second.items_inserted, second.items_merged) == (0, 5)
        # …via the exact-duplicate short-circuit — no LLM synthesis calls at all.
        assert synthesizer.concept_calls == []
        assert synthesizer.procedure_calls == []
        # A reinstall updates the persona in place rather than duplicating or ignoring it
        # (FR-601, changed 2026-08-02 — see the unit tests for the field-ownership split).
        assert second.persona_created is False
        assert any("updated" in notice for notice in second.notices)
        personas = PSPersonaRepository(pg_conn).list_all()
        assert len(personas) == 2  # GA (onboarded_user) + the bundle's own — never a second copy
        [installed] = [p for p in personas if p.persona_key is not None]
        assert installed.system_prompt  # the authored definition survived the round trip

        with pg_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM concepts")
            assert cur.fetchone()[0] == 3
            cur.execute("SELECT count(*) FROM procedures")
            assert cur.fetchone()[0] == 2
            # Directive (FR-207) is first-class now, its own table — one row per
            # persona.languages (es + fr), created once on first install;
            # sync_created is idempotent on reinstall.
            cur.execute("SELECT count(*) FROM directives")
            assert cur.fetchone()[0] == 2
            # Provenance log is append-only and deliberately NOT a reinstall guard.
            cur.execute("SELECT items_inserted, items_merged FROM bundle_installs ORDER BY id")
            assert cur.fetchall() == [(5, 0), (0, 5)]
