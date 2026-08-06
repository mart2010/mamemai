-- Mamemai — initial schema
-- Requires PostgreSQL 15+ and the pgvector extension.
-- Run once against a fresh database:
--   psql -d mamemai -f migrations/001_initial_schema.sql

CREATE EXTENSION IF NOT EXISTS vector;

-- ─────────────────────────────────────────────────────────────────────────────
-- Users (singleton — one record only)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id                          UUID             PRIMARY KEY,
    primary_language            TEXT,                           -- NULL until onboarding
    secondary_languages         TEXT[]           NOT NULL DEFAULT '{}',
    idle_consolidation_minutes  DOUBLE PRECISION NOT NULL DEFAULT 5.0
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Personas (Persona bounded context)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS personas (
    id                UUID             PRIMARY KEY,
    name              TEXT             NOT NULL,
    system_prompt     TEXT             NOT NULL,
    languages         TEXT[]           NOT NULL DEFAULT '{}',  -- input languages; empty = primary language only
    response_language TEXT             NOT NULL DEFAULT 'en',  -- IETF tag; drives TTS voice selection
    voices            JSONB            NOT NULL DEFAULT '{"default": "af_heart"}',  -- speaker role -> Kokoro voice; must include "default"
    is_system         BOOLEAN          NOT NULL DEFAULT FALSE,
    speaking_rate     DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    is_active         BOOLEAN          NOT NULL DEFAULT TRUE,
    persona_key       TEXT             UNIQUE,                 -- author-namespaced bundle identity (e.g. 'meo/spanish-tutor'); NULL for GA and user-created personas
    settings          JSONB,                                   -- opaque persona-owned tunables; read only by the persona's own strategies (same leak-prevention contract as persona_state)
    strategy          TEXT,                                    -- strategy-set name resolved by the composition root (e.g. 'language_tutor'); NULL binds no strategies
    extracts_episodes BOOLEAN          NOT NULL DEFAULT TRUE,   -- FALSE for a persona whose sessions are rehearsal (drills, role-play) rather than a record of real events (FR-407)
    reads_across_personas BOOLEAN      NOT NULL DEFAULT FALSE,  -- TRUE for a persona that practises over the user's whole store rather than its own content; READ-only, authoring/cascade unaffected (FR-303)
    sees_turn_language BOOLEAN         NOT NULL DEFAULT FALSE,  -- TRUE to prefix user turns with [lang:code] in the LLM context (FR-114); only for a persona whose job depends on which language was spoken
    created_at        TIMESTAMPTZ      NOT NULL,
    updated_at        TIMESTAMPTZ      NOT NULL
);
-- Already-provisioned databases predate `extracts_episodes`, which replaced the old
-- "has an assessment strategy" proxy (FR-407). Added nullable so the backfill can tell
-- pre-existing rows from new ones, then constrained — re-applying this file is a no-op
-- because no row is left NULL, so a persona later declared TRUE *with* a strategy (the
-- exact case the proxy could not express) is never clobbered back to FALSE.
ALTER TABLE personas ADD COLUMN IF NOT EXISTS extracts_episodes BOOLEAN;
UPDATE personas SET extracts_episodes = (strategy IS NULL) WHERE extracts_episodes IS NULL;
ALTER TABLE personas ALTER COLUMN extracts_episodes SET DEFAULT TRUE;
ALTER TABLE personas ALTER COLUMN extracts_episodes SET NOT NULL;
-- Added 2026-08-02 (FR-303). No backfill block needed, unlike extracts_episodes:
-- every persona predating this wants the column default, so DEFAULT FALSE is already
-- the right answer for existing rows.
ALTER TABLE personas ADD COLUMN IF NOT EXISTS reads_across_personas BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE personas ADD COLUMN IF NOT EXISTS sees_turn_language BOOLEAN NOT NULL DEFAULT FALSE;

-- ─────────────────────────────────────────────────────────────────────────────
-- Conversations and turns (Memory bounded context — offline only)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS conversations (
    id          BIGSERIAL   PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL,
    ended_at    TIMESTAMPTZ,                      -- NULL while grouping is incomplete
    persona_id  UUID        NOT NULL REFERENCES personas(id) ON DELETE RESTRICT,  -- traceability; see CLAUDE.md §Data Model
    worthiness  BOOLEAN,                          -- set by WorthinessEvaluator during consolidation
    summary     TEXT,                             -- set by consolidation
    consolidated BOOLEAN    NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS turns (
    conversation_id BIGINT      NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    session_id      UUID        NOT NULL,              -- source JSONL file; used by TurnLogReplayer for idempotency
    timestamp       TIMESTAMPTZ NOT NULL,
    speaker         TEXT        NOT NULL CHECK (speaker IN ('user', 'assistant')),
    content         TEXT        NOT NULL,
    language        TEXT,                              -- IETF tag, NULL if not detected
    PRIMARY KEY (conversation_id, timestamp)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Memory items (Memory bounded context)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS episodes (
    id                     BIGSERIAL   PRIMARY KEY,
    summary                TEXT        NOT NULL,
    happened_at            TIMESTAMPTZ NOT NULL,
    origin_conversation_id BIGINT      NOT NULL REFERENCES conversations(id),  -- first conversation where this episode was extracted
    created_at             TIMESTAMPTZ NOT NULL,
    updated_at             TIMESTAMPTZ NOT NULL,
    embedding              vector(1024)
);

CREATE TABLE IF NOT EXISTS concepts (
    id               SERIAL      PRIMARY KEY,
    persona_id       UUID        NOT NULL REFERENCES personas(id) ON DELETE CASCADE,  -- see CLAUDE.md §Data Model
    name             TEXT        NOT NULL,
    description      TEXT        NOT NULL,  -- LLM synthesis ~300 words; see CLAUDE.md
    language         TEXT        NOT NULL,  -- first introduced; fixed on upsert
    category         TEXT,                  -- free text, interpreted in the owning persona's vocabulary
    persona_state    JSONB,                 -- opaque; written only by the owning persona's assessment strategy
    engagement_level TEXT        NOT NULL DEFAULT 'mentioned',
    origin           TEXT        NOT NULL DEFAULT 'organic',  -- "authored" (bundle/enrichment) vs "organic" (live extraction); fixed on upsert — FR-307
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    embedding        vector(1024)
);
-- Already-provisioned databases predate the `origin` column above, and predate
-- `directive` moving off this table onto its own (this file is re-applied
-- idempotently, not chained migrations — see PsycopgSchemaRunner).
ALTER TABLE concepts ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'organic';
-- Directives moved to their own table (2026-07-30). Databases provisioned before that
-- still hold directive rows IN `concepts`, and dropping the column alone would strip the
-- only thing marking them as control-plane data — leaving them as ordinary concepts that
-- the search/list queries now return, since those queries dropped their
-- `WHERE directive IS NULL` filter on the same premise that no such rows exist. Found on
-- a real database 2026-08-02 (4 rows: "Switch to Tutor Italiano" et al.). Delete them
-- while the column can still identify them; PersonaDirectiveSync regenerates the real
-- Directive rows at server start. Guarded so re-applying this file stays a no-op.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'concepts' AND column_name = 'directive'
    ) THEN
        DELETE FROM concepts WHERE directive IS NOT NULL;
    END IF;
END $$;
ALTER TABLE concepts DROP COLUMN IF EXISTS directive;

CREATE TABLE IF NOT EXISTS procedures (
    id               SERIAL      PRIMARY KEY,
    persona_id       UUID        NOT NULL REFERENCES personas(id) ON DELETE CASCADE,  -- see CLAUDE.md §Data Model
    name             TEXT        NOT NULL,
    description      TEXT        NOT NULL,  -- LLM synthesis; see CLAUDE.md
    steps            TEXT[]      NOT NULL DEFAULT '{}',  -- empty when not decomposable into discrete steps
    language         TEXT        NOT NULL,  -- first introduced; fixed on upsert
    category         TEXT,                  -- free text, interpreted in the owning persona's vocabulary
    persona_state    JSONB,                 -- opaque; written only by the owning persona's assessment strategy
    engagement_level TEXT        NOT NULL DEFAULT 'mentioned',
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    embedding        vector(1024)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Directives (FR-207 — persona-switch triggers; deterministic keyword matching,
-- not memory content, hence not on `concepts`/no embedding column)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS directives (
    id                SERIAL      PRIMARY KEY,
    target_persona_id UUID        REFERENCES personas(id) ON DELETE CASCADE,  -- persona this directive switches to; cascades on persona removal, no explicit cleanup code needed. NULL for a directive that targets no persona (e.g. set_response_language)
    target_language   TEXT,                 -- IETF tag a set_response_language directive switches TO; NULL for persona-targeting directives
    action            TEXT        NOT NULL,  -- 'switch_persona' | 'set_response_language'
    language          TEXT        NOT NULL,  -- IETF tag this trigger's wording is in
    trigger           JSONB       NOT NULL,  -- {"keywords": [...], "max_words": N} — see services/directives.py
    description       TEXT        NOT NULL,  -- human-readable label, e.g. "Switch to Tutor Italiano."
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL
);
-- Databases provisioned before 2026-08-02 have directives that must target a persona.
-- FR-701 added a non-persona action (set_response_language), so the FK became optional
-- and a language target joined it: a directive now names a persona OR a language.
-- Placed after the CREATE above, not with the personas ALTERs — this file is applied
-- top to bottom on a fresh database too, where the table does not exist until here.
ALTER TABLE directives ALTER COLUMN target_persona_id DROP NOT NULL;
ALTER TABLE directives ADD COLUMN IF NOT EXISTS target_language TEXT;

-- Append-only provenance log: one row per bundle install ("which installed content came
-- from where/what generator" — same rationale as episodes.origin_conversation_id).
-- Nothing reads it in any code path, and it is deliberately NOT a reinstall guard.
-- persona_key is a plain TEXT, not an FK: the log must survive persona deletion.
CREATE TABLE IF NOT EXISTS bundle_installs (
    id             BIGSERIAL   PRIMARY KEY,
    persona_key    TEXT        NOT NULL,
    bundle_name    TEXT        NOT NULL,
    bundle_version TEXT        NOT NULL,
    bundle_author  TEXT        NOT NULL,
    installed_at   TIMESTAMPTZ NOT NULL,
    items_inserted INTEGER     NOT NULL,
    items_merged   INTEGER     NOT NULL,
    manifest       JSONB       NOT NULL   -- the bundle's [bundle] + [provenance] tables, verbatim
);

CREATE TABLE IF NOT EXISTS memory_brief (
    id         INTEGER     PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton
    content    TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Indexes
-- ─────────────────────────────────────────────────────────────────────────────

-- Partial index — the only query pattern on conversations is "give me all
-- unconsolidated ones ordered by started_at".
CREATE INDEX IF NOT EXISTS conversations_unconsolidated
    ON conversations (started_at)
    WHERE NOT consolidated;

-- Turn retrieval always scoped to a conversation, ordered by timestamp.
-- The composite PK (conversation_id, timestamp) already covers this pattern.
-- session_id index used by TurnLogReplayer for idempotency check (EXISTS by session).
CREATE INDEX IF NOT EXISTS turns_session_id ON turns (session_id);

-- HNSW indexes for vector similarity search (cosine distance).
-- m=16 and ef_construction=64 are pgvector defaults; tune after calibration.
CREATE INDEX IF NOT EXISTS episodes_embedding_hnsw
    ON episodes   USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS concepts_embedding_hnsw
    ON concepts   USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS procedures_embedding_hnsw
    ON procedures USING hnsw (embedding vector_cosine_ops);

-- ─────────────────────────────────────────────────────────────────────────────
-- Seed data
-- ─────────────────────────────────────────────────────────────────────────────

-- GeneralAssistant is the only system persona. It is seeded here so it is
-- always present regardless of application state. The system_prompt can be
-- updated after first launch via voice; the id is fixed and must match
-- GENERAL_ASSISTANT_ID in domain/model.py.
-- name is a generic placeholder — Mamemai is the product name, not the persona's
-- spoken identity; this should become voice-configurable in a future phase.
INSERT INTO personas (id, name, system_prompt, languages, response_language, voices, is_system, created_at, updated_at, speaking_rate, is_active)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'Vocal Assistant',
    'You are a helpful, honest voice assistant. Your text is spoken aloud verbatim — never use markdown formatting
    (no **bold**, no _italics_, no bullet points, no headers, no code blocks).
    Only if the user explicitly asks to hear your introduction again, give a brief spoken summary of what you are and what you can do.
    Never offer it unprompted, and never open a session with it. A session that starts without much context is not a first session — you have simply not been told what came before, which is not the same as nothing having happened.',
    '{}',
    'en',
    '{"default": "af_heart"}',
    TRUE,
    NOW(),
    NOW(),
    1.0,
    TRUE
) ON CONFLICT (id) DO NOTHING;
