# Condor telemetry collector

The receiving end of [`condor/telemetry/`](../condor/telemetry/) (FEAT-023).
Consenting installs POST batched, anonymous envelopes here; this service
validates them against the client's own taxonomy, stores them idempotently,
rolls them up nightly, and leaves five SQL files behind that answer the
questions the exercise exists for.

It is not installed by default and it is not running anywhere. Standing it up is
a deliberate act.

## Why it lives in this repository

`ingest.py` imports `condor.telemetry.schema` — the *same module* the emitter
sanitises with. A separate collector repo would be tidier operationally but
would force the event taxonomy to exist in two places, and a taxonomy that
drifts between emitter and validator is the classic failure of this kind of
system: the client starts sending a property, the server silently drops it, and
nobody notices for three months. Here, drift is not unlikely — it is impossible,
and `tests/test_telemetry_server.py` asserts there is no second copy in the repo.

The cost is a container image carrying Condor's dependency set rather than four
packages. That trade was made knowingly.

## Endpoints

| Method | Path | Behaviour |
|---|---|---|
| `POST` | `/v1/events` | Ingest one envelope → `202 {"accepted": n, "rejected": m, "duplicates": d}` |
| `GET`  | `/health`    | Liveness plus a real database round trip |

There is no endpoint that reads data back to an install, and no API docs are
served: a schema browser on an unauthenticated endpoint is surface with no
reader.

## Surviving the open internet

Installs are anonymous by design, so there is no credential to issue without
creating the identity we explicitly do not want. The endpoint is therefore
public and unauthenticated, and the pipeline is ordered so that everything cheap
happens before anything expensive:

1. **Body cap — 1 MB.** Counted as the stream arrives. `Content-Length` is
   checked first because it is free, but it is not trusted: a chunked request
   can omit or understate it.
2. **Rate limit by source IP** — token bucket, 60/h, *before* the JSON parser
   runs. `X-Forwarded-For` is ignored unless `TELEMETRY_TRUSTED_PROXY` is set,
   because otherwise the header is a one-line rate-limit bypass. The limiter's
   key space is capped and evicts LRU, so an attacker-chosen `install_id` cannot
   become a memory-exhaustion primitive.
3. **Envelope validation** — unknown `schema` versions, unknown `level`s, a
   malformed `install_id` and batches over 500 events are refused whole.
4. **Event validation** against `condor.telemetry.schema`. An unknown event name
   or an out-of-spec property is **dropped and counted**, and the rest of the
   batch is still accepted — rejecting a whole envelope over one malformed event
   would let a single client bug erase a day of otherwise good data. The
   `rejected` counter is the early warning that a taxonomy change broke
   something, so chart it.
5. **Persist** in one transaction, `ON CONFLICT DO NOTHING`.

Two invariants hold throughout. **No payload value is ever formatted into SQL** —
`store.py` takes bound parameters only, and the only interpolation anywhere is
partition names built from dates the rollup computed itself. **No error response
echoes any part of the request** — every refusal is a fixed string, so this
endpoint cannot be turned into a reflector or used as an oracle.

Unknown fields inside `app` and `config` are not stored, not logged, and not
reflected. They simply do not exist here.

### What this does *not* defend against

Anyone can POST fabricated envelopes with random `install_id`s and skew the
adoption numbers. Rate limits raise the cost; the real defence is that the payoff
is nil and the data steers internal direction, not anything with money attached.
If it ever becomes a problem the fix is a signed `install_id` issued on first
contact — deliberately not built now.

## Storage

`migrations/001_init.sql` (Postgres) and `001_init.sqlite.sql` (SQLite) define
the same four tables:

- **`installs`** — one upserted row per install: identity, platform, capability
  flags, counts, and a running `dropped_total`.
- **`events`** — append-only, partitioned by month in Postgres so the 90-day
  retention is a `DROP TABLE` rather than a mass `DELETE`.
- **`install_days`** — written on the **ingest path**, from newly inserted rows
  only. This is what every retention question depends on and what survives raw
  expiry, which is why it is not deferred to the rollup.
- **`daily_metrics`** — the nightly rollup. Permanent.

Both `ts` and `received_at` are kept. Client clocks on self-hosted boxes drift
and occasionally lie, so ingest clamps `ts` to `received_at ± 48 h`; dashboards
count volume by `received_at` and trust `ts` only for ordering inside one
install's session.

**SQLite is a real backend, not a mock.** The collector's tests run in Condor's
own `uv run pytest` with no database daemon, against the same statements
production executes. Postgres is what you deploy; SQLite is what keeps the tests
honest and runnable.

## Rollups

`rollup.py` runs on an interval inside the collector process (a plain asyncio
loop — the job is "every few hours, never concurrently with itself", and one
service with no extra runtime is easier to reason about than one with a
scheduler in it). SQL does the grouping; Python does the date arithmetic and
cohort matching, which is what lets one rollup run unchanged against both
backends.

It writes `dau`/`wau`/`mau`, `cohort_size` + `retention_d1/d7/d30`,
`version_share`/`os_share`/`provider_share`, event and command/action/routine
ranks, `error_rate` and `error_group`, the agent mix and per-turn cost, and
`dropped_total`/`dropped_rate` — so client-side rate limiting never masquerades
as "things got quieter". It also creates next month's partition and drops what
has aged past `TELEMETRY_RETENTION_DAYS`.

Rollups exist from day one rather than "later" because backfilling one after
dropping the raw data it came from is impossible.

## The five questions

One file each in `queries/`, each runnable standalone against Postgres *or* the
SQLite the tests seed, and each readable without Grafana:

| File | Question |
|---|---|
| `adoption.sql` | How many installs are alive, on what, and how fast do upgrades spread? |
| `retention.sql` | Do people stay? D1 / D7 / D30 by cohort. |
| `feature_usage.sql` | What is actually used, and what are we maintaining for nobody? |
| `reliability.sql` | What is broken out there, and against which upstream? |
| `agent_economics.sql` | Which providers and models, at what cost per turn, dry-run vs live? |

No query groups by `user_hash`. It is salted per install, so it can be counted
inside one install but is meaningless across them, and grouping by it globally
would invent a cross-install identity the client deliberately refused to
provide. A test asserts this.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TELEMETRY_DSN` | `sqlite:///telemetry.db` | Postgres URL in production; anything else is a SQLite path. |
| `TELEMETRY_TRUSTED_PROXY` | unset | Believe `X-Forwarded-For`. Only set this when a proxy really is in front. |
| `TELEMETRY_RETENTION_DAYS` | `90` | How long raw events live. Rollups are permanent regardless. |
| `TELEMETRY_ROLLUP_INTERVAL_S` | `21600` | How often the rollup runs. |

## Running it

```bash
cp .env.example .env      # set POSTGRES_PASSWORD and GRAFANA_PASSWORD
docker compose -f telemetry_server/docker-compose.yml up -d
```

Only `collector` should be reachable from the internet, and only through a
reverse proxy that terminates TLS — the service speaks plain HTTP inside the
compose network, matching how the `hummingbot/deploy` stack is already run.
Postgres and Grafana bind to loopback. **Grafana is the weakest operational link
here**: it is the only component with a login and a session cookie, so keep it
behind the same proxy or VPN as the rest, never exposed alongside ingest.

`grafana/datasource.yml` provisions the Postgres datasource. Dashboards are not
auto-provisioned — the `queries/*.sql` files are the deliverable and a panel is
a paste.

Once it is up, point an install at it:

```bash
CONDOR_TELEMETRY_URL=https://telemetry.example.org/v1/events
```

## Tests

```bash
uv run pytest tests/test_telemetry_server.py
```

They run against SQLite in a `tmp_path` and never bind a port, start a
container, or leave anything behind. The first test is the important one: it
builds its envelope by running the **real emitter** from `condor/telemetry/` and
handing the result to `context.envelope()`, so if FEAT-023 ever changes shape,
this suite fails instead of the collector silently dropping a field in
production.
