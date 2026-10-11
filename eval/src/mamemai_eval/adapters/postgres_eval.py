# Copyright (c) 2026 Mamemai. Licensed under AGPL-3.0.
"""Postgres adapters over the durable `eval` schema (database mamemai_eval), plus Scratch
Schema management for the SUT."""
from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from ..domain.model import (
    Corpus, EvalLanguage, FactKind, Ledger, LedgerFact, Probe, ProbeKind, Run, RunFingerprint, Score, SpeechArchetype, SyntheticUserProfile,
    Tier,
)
from ..usecases.ports import FrozenCorpusError, ReferenceRequest, ReferenceResponse

DEFAULT_DSN = "postgresql:///mamemai_eval"
_EVAL_MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"
_SAFE_SCHEMA = re.compile(r"^[a-z][a-z0-9_]*$")


def eval_dsn() -> str:
    return os.environ.get("MAMEMAI_EVAL_DATABASE_URL", DEFAULT_DSN)


def connect(dsn: str | None = None) -> psycopg.Connection:
    return psycopg.connect(dsn or eval_dsn(), autocommit=True, connect_timeout=5)


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply every migrations/*.sql in order (all idempotent). Returns the files applied."""
    applied = []
    for path in sorted(_EVAL_MIGRATIONS.glob("*.sql")):
        conn.execute(path.read_text(encoding="utf-8"))
        applied.append(path.name)
    return applied


def _frozen_guard(fn):
    """Translate the database's immutability trigger (migration 005) into FrozenCorpusError."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except psycopg.errors.IntegrityConstraintViolation as e:
            if "frozen corpus" in str(e):
                raise FrozenCorpusError(str(e).splitlines()[0]) from e
            raise
    return wrapper


class PgEvalRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    @_frozen_guard
    def save_profile_with_ledger(self, profile: SyntheticUserProfile, ledger: Ledger) -> None:
        if ledger.profile_id != profile.profile_id:
            raise ValueError("ledger belongs to another profile")
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO eval.profile (profile_id, language, display_name, seed, biography, archetype, speech_style) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (profile.profile_id, profile.language.value, profile.display_name, profile.seed,
                 profile.biography, profile.archetype.value, profile.speech_style),
            )
            for f in sorted(ledger.facts, key=lambda f: f.revealed_in):  # superseded fact first (FK)
                cur.execute(
                    "INSERT INTO eval.ledger_fact (profile_id, fact_id, kind, text, revealed_in, domain, happened_on, "
                    "paraphrases, supersedes, key_terms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (profile.profile_id, f.fact_id, f.kind.value, f.text, f.revealed_in, f.domain, f.happened_on,
                     list(f.paraphrases), f.supersedes, list(f.key_terms)),
                )
            for i, d in enumerate(ledger.scenario_dates):
                cur.execute("INSERT INTO eval.ledger_scenario_date VALUES (%s,%s,%s)", (profile.profile_id, i, d))

    @_frozen_guard
    def delete_profile(self, profile_id: UUID) -> None:
        """Drafts only: a Profile whose Ledger is part of a frozen Corpus must not be deleted (EV-INV-5;
        enforced once the corpus tables exist)."""
        self._conn.execute("DELETE FROM eval.profile WHERE profile_id = %s", (profile_id,))

    def profile_exists(self, profile_id: UUID) -> bool:
        return self._conn.execute("SELECT 1 FROM eval.profile WHERE profile_id = %s", (profile_id,)).fetchone() is not None

    def get_profile(self, profile_id: UUID) -> SyntheticUserProfile:
        row = self._conn.execute(
            "SELECT profile_id, language, display_name, seed, biography, archetype, speech_style "
            "FROM eval.profile WHERE profile_id = %s", (profile_id,)).fetchone()
        if row is None:
            raise KeyError(profile_id)
        return SyntheticUserProfile(row[0], EvalLanguage(row[1]), row[2], row[3], row[4], SpeechArchetype(row[5]), row[6])

    def get_ledger(self, profile_id: UUID) -> Ledger:
        rows = self._conn.execute(
            "SELECT fact_id, kind, text, revealed_in, domain, happened_on, paraphrases, supersedes, key_terms "
            "FROM eval.ledger_fact WHERE profile_id = %s ORDER BY fact_id", (profile_id,)).fetchall()
        if not rows:
            raise KeyError(profile_id)
        dates = self._conn.execute(
            "SELECT on_date FROM eval.ledger_scenario_date WHERE profile_id = %s ORDER BY idx", (profile_id,)).fetchall()
        return Ledger(profile_id, tuple(
            LedgerFact(r[0], FactKind(r[1]), r[2], r[3], r[4], r[5], tuple(r[6]), r[7], tuple(r[8])) for r in rows),
            tuple(d[0] for d in dates))

    @_frozen_guard
    def save_probes(self, profile_id: UUID, probes: list[Probe]) -> None:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("DELETE FROM eval.probe WHERE profile_id = %s", (profile_id,))
            for p in probes:
                cur.execute(
                    "INSERT INTO eval.probe (profile_id, probe_id, kind, language, text, expected, hard_negatives) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (profile_id, p.probe_id, p.kind.value, p.language.value, p.text, list(p.expected_fact_ids),
                     list(p.hard_negative_fact_ids)))

    def get_probes(self, profile_id: UUID) -> list[Probe]:
        rows = self._conn.execute(
            "SELECT probe_id, kind, language, text, expected, hard_negatives FROM eval.probe "
            "WHERE profile_id = %s ORDER BY probe_id", (profile_id,)).fetchall()
        return [Probe(r[0], ProbeKind(r[1]), EvalLanguage(r[2]), r[3], tuple(r[4]), tuple(r[5])) for r in rows]

    def save_corpus(self, corpus: Corpus) -> None:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO eval.corpus (version, digest, created_at, generated_with, notes, counts) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (corpus.version, corpus.digest, corpus.created_at, corpus.generated_with, corpus.notes,
                 Jsonb(dict(corpus.counts))))
            for pid in corpus.profile_ids:
                cur.execute("INSERT INTO eval.corpus_member (version, profile_id) VALUES (%s,%s)", (corpus.version, pid))

    def get_corpus(self, version: str) -> Corpus:
        row = self._conn.execute(
            "SELECT version, digest, created_at, generated_with, notes, counts FROM eval.corpus WHERE version = %s",
            (version,)).fetchone()
        if row is None:
            raise KeyError(version)
        members = self._conn.execute(
            "SELECT profile_id FROM eval.corpus_member WHERE version = %s ORDER BY profile_id::text", (version,)).fetchall()
        return Corpus(row[0], row[1], row[2], row[3], tuple(m[0] for m in members), row[5], row[4])

    def list_corpora(self) -> list[Corpus]:
        versions = self._conn.execute("SELECT version FROM eval.corpus ORDER BY created_at").fetchall()
        return [self.get_corpus(v[0]) for v in versions]

    def is_frozen(self, profile_id: UUID) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM eval.corpus_member WHERE profile_id = %s", (profile_id,)).fetchone() is not None

    def list_profile_ids(self) -> list[UUID]:
        return [r[0] for r in self._conn.execute("SELECT profile_id FROM eval.profile ORDER BY language, archetype")]

    def save_run(self, run: Run) -> None:
        fp = run.fingerprint
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO eval.run VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run.run_id, run.suite, run.tier.value, fp.language.value, Jsonb(_fp_to_json(fp)), fp.digest(),
                 run.comparable, run.started_at, run.finished_at),
            )
            for s in run.scores:
                cur.execute("INSERT INTO eval.score VALUES (%s,%s,%s,%s,%s)", (run.run_id, s.metric, s.value, s.n, s.unit))

    def get_run(self, run_id: UUID) -> Run:
        row = self._conn.execute(
            "SELECT run_id, suite, tier, fingerprint, comparable, started_at, finished_at "
            "FROM eval.run WHERE run_id = %s", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._build_run(row)

    def list_runs(self, suite: str) -> list[Run]:
        rows = self._conn.execute(
            "SELECT run_id, suite, tier, fingerprint, comparable, started_at, finished_at "
            "FROM eval.run WHERE suite = %s ORDER BY started_at DESC", (suite,)).fetchall()
        return [self._build_run(r) for r in rows]

    def _build_run(self, row: tuple) -> Run:
        scores = self._conn.execute(
            "SELECT metric, value, n, unit FROM eval.score WHERE run_id = %s ORDER BY metric", (row[0],)).fetchall()
        return Run(row[0], row[1], Tier(row[2]), _fp_from_json(row[3]), tuple(Score(*s) for s in scores),
                   row[5], row[6], comparable=row[4])


def _fp_to_json(fp: RunFingerprint) -> dict:
    return {"git_sha": fp.git_sha, "sut": dict(fp.sut), "corpus_version": fp.corpus_version,
            "language": fp.language.value, "reference_llm": fp.reference_llm, "rubric_hash": fp.rubric_hash,
            "seeds": list(fp.seeds), "extra": dict(fp.extra)}


def _fp_from_json(d: dict) -> RunFingerprint:
    return RunFingerprint(d["git_sha"], d["sut"], d["corpus_version"], EvalLanguage(d["language"]),
                          d["reference_llm"], d["rubric_hash"], tuple(d["seeds"]), d["extra"])


class PgReferenceLLMCache:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def get(self, key: str) -> ReferenceResponse | None:
        row = self._conn.execute(
            "SELECT response, answered_by FROM eval.reference_llm_cache WHERE cache_key = %s", (key,)).fetchone()
        return ReferenceResponse(text=row[0], model=row[1]) if row else None

    def put(self, key: str, request: ReferenceRequest, response: ReferenceResponse) -> None:
        self._conn.execute(
            "INSERT INTO eval.reference_llm_cache (cache_key, model, request, response, answered_by) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (cache_key) DO NOTHING",
            (key, request.model, Jsonb({"system": request.system, "prompt": request.prompt,
                                        "temperature": request.temperature}), response.text, response.model))


# ---------------------------------------------------------------------------
# Scratch Schema (EV-INV-6)
# ---------------------------------------------------------------------------

def scratch_schema_name(run_id: UUID) -> str:
    return f"eval_run_{run_id.hex[:12]}"


def create_scratch_schema(conn: psycopg.Connection, name: str, sut_migration: Path) -> None:
    """Create `name` and apply the SUT's own schema file into it — the same recipe the
    server's tests use for schema-per-user (search_path = <schema>, public)."""
    if not (_SAFE_SCHEMA.match(name) and name.startswith("eval_run_")):
        raise ValueError(f"not a scratch schema name: {name!r}")
    conn.execute(f'CREATE SCHEMA "{name}"')
    try:
        conn.execute(f'SET search_path TO "{name}", public')
        conn.execute(sut_migration.read_text(encoding="utf-8"))
    finally:
        conn.execute("SET search_path TO DEFAULT")


def drop_scratch_schema(conn: psycopg.Connection, name: str) -> None:
    if not (_SAFE_SCHEMA.match(name) and name.startswith("eval_run_")):
        raise ValueError(f"refusing to drop non-scratch schema {name!r}")  # never drop `eval` or user data
    conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
