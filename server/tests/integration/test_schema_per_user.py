# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Real-Postgres verification of schema-per-user (TR-501b, INV-3).

Uses its own disposable database rather than the shared `mamemai_test` (via `pg_dsn`)
on purpose: `pg_dsn` already has pgvector installed in `public` for the whole test
session, which would silently mask the one bug this file exists to catch — an
unqualified `CREATE EXTENSION IF NOT EXISTS vector` installs into whichever schema is
*first* on the connection's search_path, not `public`, the moment it actually has to
create the extension rather than find it already there. A second family member's
schema then can't resolve the `vector` type at all. Reproducing that requires a
database where the extension has never existed — hence the disposable one here.
"""
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from mamemai_server.infrastructure.postgres import connect

_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "migrations" / "001_initial_schema.sql"
# Independent of conftest's own _TEST_DB_URL (module-private there) — same env var,
# same default, so this file needs its own maintenance database, never mamemai_test.
_TEST_DB_URL = os.environ.get(
    "MAMEMAI_TEST_DATABASE_URL", "postgresql://mamemai:mamemai@localhost:5432/mamemai_test"
)


def _maintenance_url(dsn: str) -> str:
    base, _, _dbname = dsn.rpartition("/")
    return f"{base}/postgres"


@pytest.fixture
def scratch_db():
    """A brand-new, never-touched database — pgvector genuinely absent until a test
    creates it, unlike `mamemai_test`."""
    dbname = f"mamemai_schema_fixture_{uuid4().hex[:8]}"
    maint_url = _maintenance_url(_TEST_DB_URL)
    try:
        with psycopg.connect(maint_url, autocommit=True, connect_timeout=5) as maint:
            maint.execute(f'CREATE DATABASE "{dbname}"')
    except psycopg.OperationalError as e:
        pytest.skip(f"Postgres not reachable at {maint_url}: {e}")

    base = _TEST_DB_URL.rpartition("/")[0]
    yield f"{base}/{dbname}"

    with psycopg.connect(maint_url, autocommit=True, connect_timeout=5) as maint:
        maint.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


def _apply_migration_in_schema(dsn: str, schema: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as maint:
        maint.execute(f'CREATE SCHEMA "{schema}"')
        maint.execute(f'SET search_path TO "{schema}", public')
        maint.execute(_MIGRATION_PATH.read_text(encoding="utf-8"))


def test_first_schemas_extension_lands_in_public_not_the_schema_itself(scratch_db: str):
    """Spec: TR-501b. The regression this guards: a bare `CREATE EXTENSION IF NOT
    EXISTS vector` (no `WITH SCHEMA public`) installs into whichever schema is first
    on search_path — the first family member's own schema — leaving every later one
    unable to resolve the `vector` type at all."""
    _apply_migration_in_schema(scratch_db, "alice")

    with psycopg.connect(scratch_db, autocommit=True) as conn:
        row = conn.execute(
            "SELECT n.nspname FROM pg_extension e "
            "JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'vector'"
        ).fetchone()
        assert row is not None, "vector extension was not created at all"
        assert row[0] == "public"


def test_a_second_schema_resolves_vector_without_reinstalling_it(scratch_db: str):
    """Spec: TR-501b. The actual end-to-end promise: family member #2's connection
    must work even though member #1's schema is the one whose migration run first
    triggered the (correctly public-pinned) extension creation."""
    _apply_migration_in_schema(scratch_db, "alice")
    _apply_migration_in_schema(scratch_db, "bob")

    conn = connect(scratch_db, schema="bob")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT n.nspname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.oid = 'users'::regclass"
            )
            assert cur.fetchone()[0] == "bob"

            cur.execute("SELECT '[1,2,3]'::vector")
            assert cur.fetchone()[0] is not None
    finally:
        conn.close()
