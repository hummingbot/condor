-- The same schema in SQLite, for the test suite and a single-box trial.
--
-- Three differences, all forced by the engine and none of them semantic:
-- there is no partitioning (retention is a DELETE, which is fine at test
-- volume), no array type (`llm_providers` is a JSON array in TEXT), and no
-- native date/timestamp type (everything is ISO-8601 UTC text, which sorts and
-- compares correctly). Column names, keys and conflict targets are identical to
-- 001_init.sql so telemetry_server.store can share one set of statements.

CREATE TABLE IF NOT EXISTS installs (
    install_id     TEXT PRIMARY KEY,
    first_seen     TEXT    NOT NULL,
    last_seen      TEXT    NOT NULL,
    level          TEXT    NOT NULL,
    version        TEXT,
    branch         TEXT,
    os             TEXT,
    arch           TEXT,
    python         TEXT,
    in_docker      INTEGER,
    has_hb_api     INTEGER,
    has_gateway    INTEGER,
    has_web        INTEGER,
    user_count     INTEGER,
    server_count   INTEGER,
    agent_count    INTEGER,
    llm_providers  TEXT,
    dropped_total  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT NOT NULL,
    install_id  TEXT NOT NULL,
    ts          TEXT NOT NULL,
    received_at TEXT NOT NULL,
    name        TEXT NOT NULL,
    props       TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, ts)
);

CREATE INDEX IF NOT EXISTS events_name_ts ON events (name, ts);
CREATE INDEX IF NOT EXISTS events_install_ts ON events (install_id, ts);

CREATE TABLE IF NOT EXISTS install_days (
    install_id TEXT    NOT NULL,
    day        TEXT    NOT NULL,
    events     INTEGER NOT NULL DEFAULT 0,
    errors     INTEGER NOT NULL DEFAULT 0,
    version    TEXT,
    PRIMARY KEY (install_id, day)
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    day    TEXT NOT NULL,
    metric TEXT NOT NULL,
    dim    TEXT NOT NULL DEFAULT '',
    value  REAL NOT NULL,
    PRIMARY KEY (day, metric, dim)
);
