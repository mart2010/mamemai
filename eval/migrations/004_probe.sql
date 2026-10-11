-- Mamemai eval — Probes generated from a Ledger (S1.3).
CREATE TABLE IF NOT EXISTS eval.probe (
    profile_id     UUID   NOT NULL REFERENCES eval.profile(profile_id) ON DELETE CASCADE,
    probe_id       TEXT   NOT NULL,
    kind           TEXT   NOT NULL CHECK (kind IN
                       ('single_fact', 'aggregation', 'update', 'abstention', 'cross_lingual', 'hard_negative')),
    language       TEXT   NOT NULL CHECK (language IN ('en', 'fr')),
    text           TEXT   NOT NULL,
    expected       TEXT[] NOT NULL DEFAULT '{}',
    hard_negatives TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (profile_id, probe_id)
);
