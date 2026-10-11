# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Spec: EV-INV-5 — the DATABASE refuses changes to frozen data, not just the application."""
from datetime import UTC, datetime

import psycopg
import pytest

from mamemai_eval.adapters import corpus_files, postgres_eval as pg
from mamemai_eval.usecases.corpus import FreezeCorpus, ImportCorpus, verify
from mamemai_eval.usecases.ports import FrozenCorpusError
from tests.helpers import sample_content

NOW = datetime(2026, 10, 11, tzinfo=UTC)


@pytest.fixture
def frozen(conn):
    repo = pg.PgEvalRepository(conn)
    c = sample_content()
    repo.save_profile_with_ledger(c.profile, c.ledger)
    repo.save_probes(c.profile.profile_id, list(c.probes))
    corpus, files = FreezeCorpus(repo).execute("v1", [c.profile.profile_id], "claude-x", "n", NOW)
    return repo, c, corpus, files


def test_freeze_round_trips_through_postgres(frozen):
    repo, c, corpus, _ = frozen
    assert repo.get_corpus("v1") == corpus and repo.is_frozen(c.profile.profile_id) and verify(repo, "v1")
    assert [x.version for x in repo.list_corpora()] == ["v1"]


@pytest.mark.parametrize("sql", [
    "UPDATE eval.ledger_fact SET text = 'x' WHERE fact_id = 'f01'",
    "DELETE FROM eval.ledger_fact WHERE fact_id = 'f01'",
    "UPDATE eval.probe SET text = 'x'",
    "DELETE FROM eval.probe",
    "UPDATE eval.profile SET biography = 'x'",
    "DELETE FROM eval.profile",
    "DELETE FROM eval.ledger_scenario_date",
])
def test_database_refuses_raw_sql_changes_to_frozen_data(frozen, conn, sql):
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="frozen corpus"):
        conn.execute(sql)


def test_repository_methods_raise_frozen_corpus_error(frozen):
    repo, c, _, _ = frozen
    with pytest.raises(FrozenCorpusError):
        repo.delete_profile(c.profile.profile_id)
    with pytest.raises(FrozenCorpusError):
        repo.save_probes(c.profile.profile_id, [])


def test_export_then_import_into_an_empty_database(frozen, conn, tmp_path):
    repo, c, corpus, files = frozen
    target = corpus_files.write_corpus(corpus, files, tmp_path)
    with pytest.raises(FileExistsError):
        corpus_files.write_corpus(corpus, files, tmp_path)  # never overwritten
    conn.execute("TRUNCATE eval.corpus_member, eval.corpus, eval.probe, eval.ledger_scenario_date, eval.ledger_fact, "
                 "eval.profile CASCADE")
    manifest, read = corpus_files.read_corpus(target)
    imported = ImportCorpus(repo).execute(manifest, read)
    assert imported.digest == corpus.digest and verify(repo, "v1") and set(repo.get_ledger(c.profile.profile_id).facts) == set(c.ledger.facts)
