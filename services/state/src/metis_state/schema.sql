-- Local authoritative state. Shaped deliberately like the DynamoDB design:
-- (user_id, kind, id) mirrors PK = USER#<sub> / SK = <TYPE>#<id>, so the same
-- access patterns, revision checks and transaction boundaries apply to both
-- adapters and neither needs a table scan to find one user's rows.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS records (
    user_id      TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    id           TEXT    NOT NULL,
    revision     INTEGER NOT NULL,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    body         TEXT    NOT NULL,          -- full JSON projection of the entity
    -- denormalised query columns; the JSON body stays authoritative
    state        TEXT,
    project_id   TEXT,
    local_date   TEXT,
    deadline_date TEXT,
    trigger_at   TEXT,
    tombstoned   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, kind, id)
);

CREATE INDEX IF NOT EXISTS idx_records_kind_state
    ON records (user_id, kind, state);
CREATE INDEX IF NOT EXISTS idx_records_kind_project
    ON records (user_id, kind, project_id);
CREATE INDEX IF NOT EXISTS idx_records_kind_deadline
    ON records (user_id, kind, deadline_date);
CREATE INDEX IF NOT EXISTS idx_records_kind_localdate
    ON records (user_id, kind, local_date);
CREATE INDEX IF NOT EXISTS idx_records_trigger
    ON records (kind, trigger_at);

-- Immutable change log. Written in the same transaction as the record change,
-- so a superseded value stays inspectable instead of being overwritten.
CREATE TABLE IF NOT EXISTS revisions (
    user_id    TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    id         TEXT    NOT NULL,
    revision   INTEGER NOT NULL,
    recorded_at TEXT   NOT NULL,
    changed_fields TEXT NOT NULL,
    body       TEXT    NOT NULL,
    PRIMARY KEY (user_id, kind, id, revision)
);

-- Transactional outbox: the only bridge between a committed state change and
-- an external side effect (scheduler sync, indexing, delivery).
CREATE TABLE IF NOT EXISTS outbox (
    user_id         TEXT    NOT NULL,
    id              TEXT    NOT NULL,
    event_type      TEXT    NOT NULL,
    event_version   INTEGER NOT NULL DEFAULT 1,
    target_id       TEXT,
    target_revision INTEGER,
    correlation_id  TEXT    NOT NULL,
    idempotency_key TEXT    NOT NULL,
    payload         TEXT    NOT NULL,
    state           TEXT    NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    available_at    TEXT    NOT NULL,
    lease_owner     TEXT,
    lease_expires_at TEXT,
    failure         TEXT,
    error_message   TEXT,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    PRIMARY KEY (user_id, id)
);

CREATE INDEX IF NOT EXISTS idx_outbox_ready ON outbox (state, available_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_idem ON outbox (user_id, event_type, idempotency_key);

-- A repeated successful mutation returns the stored response instead of doing
-- the work twice.
CREATE TABLE IF NOT EXISTS idempotency (
    user_id      TEXT NOT NULL,
    key          TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response     TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

-- Per-user planning epoch. Bumped inside the same transaction as any change
-- affecting availability, obligations, dependencies or planning preferences,
-- so a plan assembled across several reads can detect a concurrent edit.
CREATE TABLE IF NOT EXISTS user_meta (
    user_id        TEXT    NOT NULL PRIMARY KEY,
    planning_epoch INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

-- Budget ledger. The gate compares reserved + settled against the cap, so two
-- concurrent workers cannot both pass on the same remaining budget.
CREATE TABLE IF NOT EXISTS usage_day (
    user_id             TEXT    NOT NULL,
    local_date          TEXT    NOT NULL,
    reserved_micro_usd  INTEGER NOT NULL DEFAULT 0,
    settled_micro_usd   INTEGER NOT NULL DEFAULT 0,
    model_calls         INTEGER NOT NULL DEFAULT 0,
    audio_seconds       REAL    NOT NULL DEFAULT 0,
    speech_jobs         INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT    NOT NULL,
    PRIMARY KEY (user_id, local_date)
);

CREATE TABLE IF NOT EXISTS budget_reservations (
    id                 TEXT    NOT NULL PRIMARY KEY,
    user_id            TEXT    NOT NULL,
    local_date         TEXT    NOT NULL,
    operation          TEXT    NOT NULL,
    model_id           TEXT,
    reserved_micro_usd INTEGER NOT NULL,
    actual_micro_usd   INTEGER,
    state              TEXT    NOT NULL DEFAULT 'reserved',
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    correlation_id     TEXT    NOT NULL,
    created_at         TEXT    NOT NULL,
    settled_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_reservations_user ON budget_reservations (user_id, local_date);

-- Conditional claim for a job occurrence. The unique key is what stops a
-- duplicate worker creating a second in-app notification.
CREATE TABLE IF NOT EXISTS claims (
    claim_key  TEXT NOT NULL PRIMARY KEY,
    user_id    TEXT NOT NULL,
    owner      TEXT NOT NULL,
    claimed_at TEXT NOT NULL
);
