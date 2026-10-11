-- Mamemai eval — ledger facts carry key_terms for deterministic presence / staleness checks.
ALTER TABLE eval.ledger_fact ADD COLUMN IF NOT EXISTS key_terms TEXT[] NOT NULL DEFAULT '{}';
