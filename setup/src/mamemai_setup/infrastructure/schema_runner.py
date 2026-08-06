# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
from __future__ import annotations

import re
from pathlib import Path

import psycopg

# Cross-package file read (not a Python import) — `setup` doesn't bundle its
# own copy of the schema, to avoid drift from the authoritative one in
# `server`. Assumes the standard monorepo layout (setup/ and server/ as
# siblings), which is what mamemai-setup is designed to run inside anyway.
_DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "server" / "migrations" / "001_initial_schema.sql"

# Belt-and-suspenders: `schema` normally already passed through domain.slugify.schema_slug,
# but this is the point a raw string gets interpolated into DDL rather than bound as a
# parameter (Postgres has no placeholder syntax for identifiers), so it is checked again
# right here regardless of what the caller already validated.
_SAFE_SCHEMA = re.compile(r"^[a-z][a-z0-9_]*$")


class PsycopgSchemaRunner:
    """Applies migrations/001_initial_schema.sql inside a named Postgres schema
    (TR-5xx) — one schema per family member. The SQL itself is written to be
    idempotent (`CREATE TABLE/INDEX IF NOT EXISTS`, `ON CONFLICT DO NOTHING`),
    so a straightforward re-apply on every wizard run is safe — no migration
    framework needed, matching the project's explicit non-goal."""

    def __init__(self, schema_path: Path = _DEFAULT_SCHEMA_PATH) -> None:
        self._schema_path = schema_path

    def apply_schema(self, database_url: str, schema: str) -> None:
        if not _SAFE_SCHEMA.match(schema):
            raise ValueError(f"Unsafe Postgres schema name: {schema!r}")
        sql = self._schema_path.read_text(encoding="utf-8")
        # `public` stays on the search path behind the new schema so pgvector's
        # `vector` type (CREATE EXTENSION below, installed at its own default
        # location) remains resolvable from inside it.
        prelude = f'CREATE SCHEMA IF NOT EXISTS "{schema}";\nSET search_path TO "{schema}", public;\n'
        with psycopg.connect(database_url) as conn, conn.cursor() as cur:
            cur.execute(prelude + sql)
