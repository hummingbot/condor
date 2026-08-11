-- Collector schema (FEAT-024), Postgres flavour. Idempotent: safe to re-run on
-- every boot, which is how telemetry_server.app applies it.
--
-- `installs` is upserted on every envelope; `events` is append-only and
-- partitioned by month so the 90-day retention is a DROP TABLE rather than a
-- mass DELETE; `install_days` is written on the ingest path because it is what
-- survives raw expiry and every retention question depends on it;
-- `daily_metrics` is the nightly rollup and is permanent.

CREATE TABLE IF NOT EXISTS installs (
    install_id     uuid PRIMARY KEY,
    first_seen     timestamptz NOT NULL,
    last_seen      timestamptz NOT NULL,
    level          text        NOT NULL,
    version        text,
    branch         text,
    os             text,
    arch           text,
    python         text,
    in_docker      boolean,
    -- Capability flags the client actually sends (condor/telemetry/context.py
    -- `config_shape`). The original design omitted them; the wire has them.
    has_hb_api     boolean,
    has_gateway    boolean,
    has_web        boolean,
    user_count     integer,
    server_count   integer,
    agent_count    integer,
    llm_providers  text[],
    dropped_total  bigint NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id          uuid        NOT NULL,
    install_id  uuid        NOT NULL,
    -- Both timestamps are kept on purpose. Client clocks on self-hosted boxes
    -- drift and occasionally lie; ingest clamps `ts` to received_at +/- 48h,
    -- dashboards count volume by `received_at`, and `ts` is only trusted for
    -- ordering inside one install's session.
    ts          timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    name        text        NOT NULL,
    props       jsonb       NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX IF NOT EXISTS events_name_ts ON events (name, ts);
CREATE INDEX IF NOT EXISTS events_install_ts ON events (install_id, ts);

CREATE TABLE IF NOT EXISTS install_days (
    install_id uuid   NOT NULL,
    day        date   NOT NULL,
    events     bigint NOT NULL DEFAULT 0,
    errors     bigint NOT NULL DEFAULT 0,
    version    text,
    PRIMARY KEY (install_id, day)
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    day    date    NOT NULL,
    metric text    NOT NULL,
    dim    text    NOT NULL DEFAULT '',
    value  numeric NOT NULL,
    PRIMARY KEY (day, metric, dim)
);

-- A partitioned table with no partition rejects every insert, so seed this
-- month and next. telemetry_server.rollup keeps running a month ahead and drops
-- what has aged out.
DO $$
DECLARE
    start_month date := date_trunc('month', now())::date;
    bound       date;
BEGIN
    FOR i IN 0..1 LOOP
        bound := (start_month + (i || ' month')::interval)::date;
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF events '
            'FOR VALUES FROM (%L) TO (%L)',
            'events_' || to_char(bound, 'YYYY_MM'),
            bound,
            (bound + interval '1 month')::date
        );
    END LOOP;
END $$;
