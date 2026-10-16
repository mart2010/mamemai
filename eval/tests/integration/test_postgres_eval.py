# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-6 (scratch schemas), EV-INV-8 (cache), repository contract for Fake and Pg."""
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from mamemai_eval.adapters import postgres_eval as pg
from mamemai_eval.domain.model import (
    EvalLanguage, FactKind, Ledger, LedgerFact, Run, RunFingerprint, Score, SpeechArchetype, SyntheticUserProfile, Tier,
)
from mamemai_eval.usecases.ports import ReferenceRequest, ReferenceResponse
from tests.fakes import FakeEvalRepository

SERVER_MIGRATION = Path(__file__).resolve().parents[3] / "server" / "migrations" / "001_initial_schema.sql"


@pytest.fixture(params=["fake", "postgres"])
def repo(request, conn):
    return FakeEvalRepository() if request.param == "fake" else pg.PgEvalRepository(conn)


def _profile():
    pid = uuid4()
    profile = SyntheticUserProfile(pid, EvalLanguage.FR, "Camille", 7, "Astronome amateur à Lyon.", SpeechArchetype.HESITANT,
                                  "phrases courtes")
    ledger = Ledger(pid, (
        LedgerFact("f1", FactKind.ATTRIBUTE, "Habite à Lyon", 0, "maison", None, ("Je vis à Lyon",), key_terms=("Lyon",)),
        LedgerFact("f2", FactKind.ATTRIBUTE, "Habite à Paris", 3, "maison", None, (), supersedes="f1"),
        LedgerFact("f3", FactKind.EVENT, "A vu une éclipse à Nice", 1, "voyage", date(2026, 6, 10)),
    ), (date(2026, 6, 8), date(2026, 6, 22), date(2026, 7, 13), date(2026, 8, 3)))
    return profile, ledger


def test_profile_and_ledger_round_trip(repo):
    profile, ledger = _profile()
    repo.save_profile_with_ledger(profile, ledger)
    assert repo.get_profile(profile.profile_id) == profile
    assert repo.get_ledger(profile.profile_id) == ledger


def test_profile_can_be_deleted_and_regenerated(repo):
    profile, ledger = _profile()
    repo.save_profile_with_ledger(profile, ledger)
    assert repo.profile_exists(profile.profile_id)
    repo.delete_profile(profile.profile_id)
    assert not repo.profile_exists(profile.profile_id)
    repo.save_profile_with_ledger(profile, ledger)


def test_run_round_trip_and_listing(repo):
    fp = RunFingerprint("abc123", {"embedding": "e5"}, "v1", EvalLanguage.EN, "claude-x", None, (1, 2), {"k": "v"})
    now = datetime.now(UTC)
    run = Run(uuid4(), "retrieval", Tier.T1, fp, (Score("mrr", 0.61, 120), Score("recall@5", 0.9, 120)), now, now)
    repo.save_run(run)
    assert repo.get_run(run.run_id) == run
    assert [r.run_id for r in repo.list_runs("retrieval")] == [run.run_id]
    assert repo.list_runs("consolidation") == []


def test_reference_cache_round_trip(conn):
    cache = pg.PgReferenceLLMCache(conn)
    req = ReferenceRequest("s", "p", "claude-x")
    assert cache.get("k") is None
    cache.put("k", req, ReferenceResponse("answer", "claude-x"))
    cache.put("k", req, ReferenceResponse("ignored", "claude-x"))  # first write wins
    assert cache.get("k") == ReferenceResponse("answer", "claude-x")


def test_scratch_schema_lifecycle_leaves_eval_schema_alone(conn):
    name = pg.scratch_schema_name(uuid4())
    pg.create_scratch_schema(conn, name, SERVER_MIGRATION)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (name,))}
        assert {"users", "personas"} <= tables
        eval_tables = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'eval'")}
        assert "profile" in eval_tables and "users" not in eval_tables
    finally:
        pg.drop_scratch_schema(conn, name)
    assert conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,)).fetchone() is None


@pytest.mark.parametrize("name", ["eval", "public", "mamemai", "eval_run_x; DROP SCHEMA eval"])
def test_only_scratch_schemas_can_be_dropped(conn, name):
    with pytest.raises(ValueError):
        pg.drop_scratch_schema(conn, name)


def test_scenarios_round_trip_and_replace(repo):
    from mamemai_eval.usecases.plan_scenarios import PlanScenarios

    profile, ledger = _profile()
    repo.save_profile_with_ledger(profile, ledger)
    # the sample ledger has a scenario (2) with no fact: planning must refuse the hole, so plan on a gap-free one
    gap_free = Ledger(ledger.profile_id, (ledger.get("f1"), ledger.get("f3"), ledger.get("f2")),
                      (date(2026, 6, 8), date(2026, 6, 22), date(2026, 7, 13), date(2026, 8, 3)))
    with pytest.raises(ValueError, match="hole"):
        PlanScenarios().execute(profile, gap_free)
    from dataclasses import replace
    full = Ledger(ledger.profile_id, gap_free.facts + (
        LedgerFact("f4", FactKind.KNOWLEDGE, "A appris que Jupiter est géante", 2, "astronomie", None, ()),), gap_free.scenario_dates)
    scenarios = PlanScenarios().execute(profile, full)
    repo.save_scenarios(profile.profile_id, scenarios)
    assert repo.get_scenarios(profile.profile_id) == scenarios
    repo.save_scenarios(profile.profile_id, scenarios[:2])  # replaces, does not append
    assert [s.index for s in repo.get_scenarios(profile.profile_id)] == [0, 1]
    assert replace(scenarios[0]) == repo.get_scenarios(profile.profile_id)[0]
