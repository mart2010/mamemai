-- Mamemai eval — frozen Corpora (S1.4) and the database-level immutability guard (EV-INV-5).

CREATE TABLE IF NOT EXISTS eval.corpus (
    version        TEXT        PRIMARY KEY,
    digest         TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL,
    generated_with TEXT        NOT NULL,
    notes          TEXT        NOT NULL DEFAULT '',
    counts         JSONB       NOT NULL
);

CREATE TABLE IF NOT EXISTS eval.corpus_member (
    version     TEXT NOT NULL REFERENCES eval.corpus(version)    ON DELETE RESTRICT,
    profile_id  UUID NOT NULL REFERENCES eval.profile(profile_id) ON DELETE RESTRICT,
    PRIMARY KEY (version, profile_id)
);

-- A Profile that belongs to any frozen Corpus (and everything hanging off it) can no longer change.
CREATE OR REPLACE FUNCTION eval.guard_frozen() RETURNS trigger AS $$
DECLARE pid UUID;
BEGIN
    pid := CASE WHEN TG_OP = 'INSERT' THEN NEW.profile_id ELSE OLD.profile_id END;
    IF EXISTS (SELECT 1 FROM eval.corpus_member WHERE profile_id = pid) THEN
        RAISE EXCEPTION 'profile % belongs to a frozen corpus and cannot be changed (EV-INV-5)', pid
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS guard_frozen ON eval.profile;
CREATE TRIGGER guard_frozen BEFORE UPDATE OR DELETE ON eval.profile
    FOR EACH ROW EXECUTE FUNCTION eval.guard_frozen();
DROP TRIGGER IF EXISTS guard_frozen ON eval.ledger_fact;
CREATE TRIGGER guard_frozen BEFORE INSERT OR UPDATE OR DELETE ON eval.ledger_fact
    FOR EACH ROW EXECUTE FUNCTION eval.guard_frozen();
DROP TRIGGER IF EXISTS guard_frozen ON eval.ledger_scenario_date;
CREATE TRIGGER guard_frozen BEFORE INSERT OR UPDATE OR DELETE ON eval.ledger_scenario_date
    FOR EACH ROW EXECUTE FUNCTION eval.guard_frozen();
DROP TRIGGER IF EXISTS guard_frozen ON eval.probe;
CREATE TRIGGER guard_frozen BEFORE INSERT OR UPDATE OR DELETE ON eval.probe
    FOR EACH ROW EXECUTE FUNCTION eval.guard_frozen();
