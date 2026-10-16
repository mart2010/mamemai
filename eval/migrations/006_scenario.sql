-- Mamemai eval — Scenarios planned from a Ledger (S2.1). Not yet guarded by the frozen-corpus trigger:
-- Scenarios join a Corpus only when v2 is frozen (S2.4), which adds the guard.
CREATE TABLE IF NOT EXISTS eval.scenario (
    profile_id   UUID   NOT NULL REFERENCES eval.profile(profile_id) ON DELETE CASCADE,
    scenario_id  TEXT   NOT NULL,
    idx          INT    NOT NULL,
    kind         TEXT   NOT NULL CHECK (kind IN ('disclosure', 'small_talk', 'assistant_operation')),
    language     TEXT   NOT NULL CHECK (language IN ('en', 'fr')),
    on_date      DATE   NOT NULL,
    reveals      TEXT[] NOT NULL DEFAULT '{}',
    omits        TEXT[] NOT NULL DEFAULT '{}',
    target_turns INT    NOT NULL,
    mood         TEXT   NOT NULL,
    topic        TEXT   NOT NULL DEFAULT '',
    PRIMARY KEY (profile_id, scenario_id),
    UNIQUE (profile_id, idx)
);
