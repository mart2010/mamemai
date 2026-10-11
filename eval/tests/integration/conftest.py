# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Integration tests run against their OWN database, never the durable `mamemai_eval`: they
truncate every eval table, which would wipe the ground truth (the lesson of 2026-10-11, when
a pytest run silently emptied freshly generated Ledgers)."""
import os

import psycopg
import pytest

from mamemai_eval.adapters import postgres_eval as pg

TEST_DSN = os.environ.get("MAMEMAI_EVAL_TEST_DATABASE_URL", "postgresql:///mamemai_eval_test")
_MAINTENANCE_DSN = "postgresql:///postgres"


def _ensure_test_database(dsn: str) -> None:
    name = psycopg.conninfo.conninfo_to_dict(dsn)["dbname"]
    assert name.endswith("_test"), f"refusing to use {name!r}: test databases must end in _test"
    with psycopg.connect(_MAINTENANCE_DSN, autocommit=True, connect_timeout=5) as maint:
        if not maint.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            maint.execute(f'CREATE DATABASE "{name}"')
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")


@pytest.fixture(scope="session")
def eval_conn_factory():
    try:
        _ensure_test_database(TEST_DSN)
        conn = pg.connect(TEST_DSN)
    except psycopg.Error as e:
        pytest.skip(f"eval test database not available at {TEST_DSN}: {e}")
    pg.migrate(conn)
    conn.close()
    return lambda: pg.connect(TEST_DSN)


@pytest.fixture
def conn(eval_conn_factory):
    c = eval_conn_factory()
    dbname = c.execute("SELECT current_database()").fetchone()[0]
    assert dbname.endswith("_test"), f"refusing to truncate {dbname!r}"
    c.execute("TRUNCATE eval.score, eval.run, eval.probe, eval.corpus_member, eval.corpus, eval.ledger_scenario_date, "
              "eval.ledger_fact, eval.profile, eval.reference_llm_cache CASCADE")
    yield c
    c.close()
