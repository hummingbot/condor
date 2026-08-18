# Privacy

Condor is self-hosted. It runs on your machine, holds your exchange API keys,
and places your orders. So the default for anything that could leave that
machine is **off**, and this document is the complete statement of what the one
optional exception does.

**Short version:** a fresh install sends nothing. It has no telemetry consent
recorded, which resolves to level `off`, at which the emitter returns before it
has looked at its arguments — no buffer, no file, no directory. Nothing changes
until an admin taps "yes" on a prompt. Even then, this repository has no
collector address compiled into it: with `CONDOR_TELEMETRY_URL` unset, the send
path is inert and events can only ever reach a capped local file.

The whole mechanism is about 900 lines in [`condor/telemetry/`](condor/telemetry/).
It is meant to be read, not trusted.

---

## What is collected, if you opt in

There are three levels. You choose one; you can change it later.

| Level | What it sends |
|---|---|
| `off` | Nothing, ever. The emitter is a no-op. **This is the default.** |
| `ping` | Only that this install exists: `install`, `heartbeat`, `version_change`, `shutdown`. |
| `usage` | The above plus the feature, reliability and agent events below. |

Every batch carries one context block describing the *deployment*, not you:

| Field | Example | Why |
|---|---|---|
| `install_id` | a random UUID | Count installs and retention. Generated once, from `uuid4`. Not derived from your MAC, hostname, username, or any token. |
| `app.version`, `app.branch` | `54ad4dc`, `main` | Which commit is actually running, so we know what to support. |
| `app.python`, `app.os`, `app.arch`, `app.in_docker` | `3.12`, `linux`, `arm64`, `true` | What to test against. |
| `config.*` | `has_gateway: true`, `user_count: 3`, `server_count: 2`, `llm_providers: ["openai"]` | Counts and capability flags. Numbers and fixed provider *names* — never a server name, never a URL, never a key. |
| `dropped` | `0` | How many events the rate limiter discarded. An honest count, so a quiet incident is visibly quiet. |

And the events themselves:

| Event | What it carries |
|---|---|
| `command` | Which of our ~20 commands was used (anything else is `other`), the surface, and whether the sender was an approved user. |
| `action` | A module (`bots`, `dex`, `agents`, …) and a verb (`view`, `deploy`, `start`). On the web, derived from the matched route *template* — `/api/v1/bots/{name}`, never the URL. |
| `feature_first_use` | The first time this install ever uses a feature. |
| `bot_deploy`, `executor_deploy`, `trade` | Connector name, controller/executor type, side, order type, paper-or-not. |
| `routine_run` | The routine's name **only if it ships in this repo** — a routine you wrote is reported as `custom` — plus how it was triggered, how long it took, and whether it worked. |
| `error` | The exception *type*, a SHA-256 hash of its message, and up to five `file:line` frames from our own packages, relative to the repo root. |
| `upstream_error` | Which dependency (`hb_api`, `gateway`, `llm`, `telegram`), which operation, which status code. |
| `agent_turn` | Provider and model id, tool-call count by category, duration, outcome. |
| `mcp_tool` | Which MCP tool ran, whether it worked, how long it took. |
| `strategy_run` | Execution mode, tick frequency, tick count, whether risk limits were configured, why it stopped. |
| `confirmation` | Which tool was asked about, and whether it was allowed, denied, or timed out. |
| `heartbeat` | Uptime, a *count* of distinct users active in the last 24h, and a per-surface activity count. |

## What is never collected

This is not a policy the call sites are asked to respect. Every event and every
property is declared in [`condor/telemetry/schema.py`](condor/telemetry/schema.py),
and anything undeclared is **dropped** on the way in. Free-form strings are
truncated to 64 characters and stripped to identifier-shaped characters, so
nothing long enough to be a key, an address, a prompt or a URL survives intact.
[`tests/test_telemetry.py`](tests/test_telemetry.py) asserts it.

Never sent, under any level:

- **Secrets** — API keys, secret keys, passphrases, private keys, your Telegram bot token.
- **Money** — order amounts, balances, portfolio value, PnL, position sizes, leverage.
- **Positions** — trading pairs. Deliberately excluded: an install that trades one pair, plus event timestamps, is a deanonymizable disclosure of what you hold. Connector *names* are sent; pairs are not.
- **Addresses and identifiers** — wallet addresses, order ids, transaction hashes.
- **Your infrastructure** — server names, URLs, hostnames, IP addresses, file paths outside this repo, your home directory or username.
- **People** — Telegram user ids, usernames, chat ids, display names.
- **Content** — prompts, agent replies, journal entries, notes, routine configs, report bodies, and exception *message* strings.

Two of those deserve a note, because they are where this kind of thing usually
leaks:

- **Exception messages are hashed, not sent.** A message string is the single
  most common carrier of a balance, a hostname or a key. We send
  `sha256(message)[:12]`, which groups identical errors together and discloses
  nothing.
- **Agent tool calls are counted by category, not by title.** A tool call's
  title is free text and routinely contains a file path. We count the ACP
  `kind` (`read`, `execute`, …) instead.

### The one derived identifier

`user_hash` lets the collector count how many distinct people use an install
without knowing who they are. It is `sha256(install_secret + telegram_user_id)`,
truncated to 16 characters, where `install_secret` is a random UUID that **never
leaves your machine**. The Telegram id is never sent, and because the salt is
per-install, the same person on two installs produces two unrelated hashes.

## Where it goes

Nowhere, unless you configure a destination.

No collector URL is compiled into this repository. Events are buffered in memory
and, when a batch cannot be delivered, appended to
`condor/.runtime/telemetry/outbox.jsonl`, which is capped at 5,000 events and 7
days — oldest dropped. With `CONDOR_TELEMETRY_URL` unset, that is the entire
life cycle: a local file, capped, that you can delete.

You can read exactly what would be sent:

```bash
cat condor/.runtime/telemetry/outbox.jsonl | jq .
```

## How to turn it off — or check it is off

It is already off unless you turned it on. To be certain:

```bash
# The authoritative answer. Prints "off" on a default install.
uv run python -c "from condor.telemetry import consent; print(consent.level())"
```

Three ways to control it, in order of precedence:

1. **Environment** — `CONDOR_TELEMETRY=off` in your `.env`. This overrides
   everything, in both directions, and is the right answer for a headless or
   containerized install. `CONDOR_TELEMETRY_URL` is what enables sending at all;
   leave it unset and nothing can be transmitted.
2. **The dashboard API** — `PUT /api/v1/settings/telemetry?level=off` (admin
   only). `GET` the same path to see the current state.
3. **`config.yml`** — edit the `telemetry` section directly:

   ```yaml
   telemetry:
     consent: denied
     level: off
   ```

Turning it off is a **withdrawal, not a pause**: the in-memory buffer and the
outbox file are deleted, so nothing already recorded can be sent afterwards.

## Changes to this document

Adding anything to the collected list requires a change to `schema.py`, a change
to this file, and re-asking for consent. In particular, adding trading pairs
would make positions inferable from timing and must not be done quietly.
