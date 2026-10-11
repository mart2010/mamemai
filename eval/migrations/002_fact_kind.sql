-- Mamemai eval — ledger facts get a Fact Kind (event / attribute / knowledge) instead of the
-- product's memory type, a happened_on date for events, and scenario dates per profile.
-- Idempotent; the ledger tables held no data when this was written (2026-10-11).

ALTER TABLE eval.ledger_fact DROP COLUMN IF EXISTS memory_type;
ALTER TABLE eval.ledger_fact ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'attribute'
    CHECK (kind IN ('event', 'attribute', 'knowledge'));
ALTER TABLE eval.ledger_fact ADD COLUMN IF NOT EXISTS happened_on DATE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'eval' AND table_name = 'ledger_fact' AND column_name = 'life_domain') THEN
        ALTER TABLE eval.ledger_fact RENAME COLUMN life_domain TO domain;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS eval.ledger_scenario_date (
    profile_id  UUID    NOT NULL REFERENCES eval.profile(profile_id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL CHECK (idx >= 0),
    on_date     DATE    NOT NULL,
    PRIMARY KEY (profile_id, idx)
);
