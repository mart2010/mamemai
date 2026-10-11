-- Mamemai eval — durable schema `eval` (database mamemai_eval). Applied by
-- `mamemai-eval db migrate`; idempotent. Slice 0 tables only; Scenario/Dialog/Corpus/Probe
-- tables arrive with the slices that use them (migrations/00N_*.sql).
-- The SUT never touches this schema (EV-INV-6): it writes to eval_run_<id> scratch schemas.

CREATE SCHEMA IF NOT EXISTS eval;

CREATE TABLE IF NOT EXISTS eval.profile (
    profile_id    UUID    PRIMARY KEY,
    language      TEXT    NOT NULL CHECK (language IN ('en', 'fr')),
    display_name  TEXT    NOT NULL,
    seed          BIGINT  NOT NULL,
    biography     TEXT    NOT NULL,
    archetype     TEXT    NOT NULL CHECK (archetype IN ('hesitant', 'fluent')),
    speech_style  TEXT    NOT NULL
);

-- Added 2026-10-11 (Speech Archetypes). Idempotent for databases created before it; no backfill needed.
ALTER TABLE eval.profile ADD COLUMN IF NOT EXISTS archetype TEXT NOT NULL DEFAULT 'fluent'
    CHECK (archetype IN ('hesitant', 'fluent'));

CREATE TABLE IF NOT EXISTS eval.ledger_fact (
    profile_id   UUID    NOT NULL REFERENCES eval.profile(profile_id) ON DELETE CASCADE,
    fact_id      TEXT    NOT NULL,
    memory_type  TEXT    NOT NULL CHECK (memory_type IN ('episodic', 'concept', 'procedure')),
    text         TEXT    NOT NULL,
    revealed_in  INTEGER NOT NULL CHECK (revealed_in >= 0),
    life_domain  TEXT    NOT NULL,
    paraphrases  TEXT[]  NOT NULL DEFAULT '{}',
    supersedes   TEXT,
    PRIMARY KEY (profile_id, fact_id),
    FOREIGN KEY (profile_id, supersedes) REFERENCES eval.ledger_fact(profile_id, fact_id)
);

CREATE TABLE IF NOT EXISTS eval.run (
    run_id        UUID        PRIMARY KEY,
    suite         TEXT        NOT NULL,
    tier          TEXT        NOT NULL,
    language      TEXT        NOT NULL,
    fingerprint   JSONB       NOT NULL,
    fingerprint_digest TEXT   NOT NULL,
    comparable    BOOLEAN     NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS run_suite_idx ON eval.run (suite, started_at DESC);

CREATE TABLE IF NOT EXISTS eval.score (
    run_id  UUID             NOT NULL REFERENCES eval.run(run_id) ON DELETE CASCADE,
    metric  TEXT             NOT NULL,
    value   DOUBLE PRECISION NOT NULL,
    n       INTEGER          NOT NULL,
    unit    TEXT             NOT NULL,
    PRIMARY KEY (run_id, metric)
);

CREATE TABLE IF NOT EXISTS eval.reference_llm_cache (
    cache_key    TEXT        PRIMARY KEY,
    model        TEXT        NOT NULL,
    request      JSONB       NOT NULL,
    response     TEXT        NOT NULL,
    answered_by  TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
