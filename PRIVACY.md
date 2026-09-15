# Privacy

Condor is self-hosted. It runs on your machine, holds your exchange API keys,
and places your orders. So what leaves that machine is anonymous, allowlisted,
and announced before it starts — and anything that is content is opt-in. This
document is the complete statement of both.

**Short version:** a fresh install counts itself: a random install id, the
version, and a periodic heartbeat — nothing about you, your users, or your
trading. Anonymous **usage summaries** (which features are used, what breaks,
which models agents run) are **on by default, but only after the admin has been
told**: Condor shows one notice — a Telegram message on boot, or a strip on the
dashboard — saying so, with a "Got it" button and a way to turn it off. Until
that notice has been delivered, nothing beyond the install count is recorded.
Batches go to the project's collector at
`https://telemetry.hummingbot.org/v1/events`, which is fixed in the source and
cannot be pointed elsewhere. An admin can turn it off entirely — from the
notice, in Settings → Privacy, or with `CONDOR_TELEMETRY=off` in the
environment — and a refusal, once recorded, is honoured across upgrades.

There is one other way anything can leave, and it is completely separate: you
can **hand us a conversation**. By default that means pressing a button and
confirming the redacted transcript we would send, one chat at a time. If you
would rather stop pressing it, you can turn on **Always** and let finished
conversations go automatically — off unless you choose it, and narrower than the
button in every direction. That is content, so it has its own rules, its own
code and its own section below — [Sharing a
conversation](#sharing-a-conversation). Nothing there happens on a fresh
install, and nothing automatic happens until you ask for it.

The whole mechanism is about 900 lines in [`condor/telemetry/`](condor/telemetry/),
and sharing is about 1,000 more in [`condor/sharing/`](condor/sharing/). They are
meant to be read, not trusted.

---

## What is collected

There are three levels. An install that has not answered is at `ping` until the
admin has been shown the notice, and at `usage` from then on. `off` is a
deliberate act: the admin presses "Turn off" on the notice or picks it in
Settings → Privacy, or the operator sets `CONDOR_TELEMETRY=off`. Ignoring the
notice is not a refusal — but it has been read, so it is not a secret either.
You can change the answer later, in either direction.

| Level | What it sends |
|---|---|
| `ping` | Only that this install exists: `install`, `heartbeat`, `version_change`, `shutdown`. What an install sends **before the notice has reached the admin**, and what "Only count my install" in Settings keeps it at. |
| `usage` | The above plus the feature, reliability and agent events below. **The default once the notice has been shown**; opt out at any time. |
| `off` | Nothing, ever. The emitter is a no-op — no install id is created, nothing is buffered, nothing is written, nothing is sent. Reached by "Turn off" on the notice, by an admin turning reporting off in Settings → Privacy, or by `CONDOR_TELEMETRY=off` in the environment. |

Every batch carries one context block describing the *deployment*, not you:

| Field | Example | Why |
|---|---|---|
| `install_id` | a random UUID | Count installs and retention. Generated once, from `uuid4`. Not derived from your MAC, hostname, username, or any token. |
| `app.version`, `app.branch` | `54ad4dc`, `main` | Which commit is actually running, so we know what to support. |
| `app.python`, `app.os`, `app.arch`, `app.in_docker` | `3.12`, `linux`, `arm64`, `true` | What to test against. |
| `config.*` | `has_gateway: true`, `user_count: 3`, `server_count: 2`, `llm_providers: ["openai"]` | Counts and capability flags. Numbers and fixed provider *names* — never a server name, never a URL, never a key. |
| `config.mode` | `telegram` or `local` | Whether the install runs Telegram at all, or only the dashboard. One of two fixed names. |
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

That list is about **telemetry**, and it stays true of telemetry no matter what
else you do. The one way a prompt or an agent reply can ever leave this install
is [Sharing a conversation](#sharing-a-conversation) — a different pipeline, a
different consent, and a different endpoint. It happens only because you pressed
the button, or because you turned on Always yourself; turning telemetry off does
not turn either of them on, and turning telemetry on does not share a word of
any conversation.

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

Before the notice has been shown, and at `ping`, only the four adoption events
and the envelope above. The usage events follow once the admin has been told,
unless they turned it off.

Batches are POSTed to `https://telemetry.hummingbot.org/v1/events`.
That address is compiled into
[`condor/telemetry/outbox.py`](condor/telemetry/outbox.py) as `COLLECTOR_URL`
and is not configurable — there is no environment variable or config key that
redirects it, so the only way to change where your data goes is to edit that
line in your own checkout. The collector itself is open source
(`condor-telemetry-server`).

Events are buffered in memory and, when a batch cannot be delivered, appended to
`.condor/telemetry/outbox.jsonl`, which is capped at 5,000 events and 7
days — oldest dropped. At level `off` no event is ever created, so nothing is
buffered, nothing is written, and nothing is sent.

You can read exactly what would be sent:

```bash
cat .condor/telemetry/outbox.jsonl | jq .
```

## How to change it — or turn it off entirely

The notice is the moment usage summaries begin for an install that has not
answered, and it is recorded as `noticed_at` in `config.yml` only once it has
actually been delivered — a Telegram message that failed to send, or a dashboard
nobody opened, turns nothing on. A refusal is a different thing from silence —
an admin who says no is obeyed, and that answer is written to `config.yml`, so
it keeps holding after an upgrade; the notice never overrides it, nor an install
that chose "Only count my install". To check what your install is doing:

```bash
# The authoritative answer. Prints "ping" before the notice has been shown,
# "usage" after it, and "off" when CONDOR_TELEMETRY=off is set or the admin
# turned reporting off.
uv run python -c "from condor.telemetry import consent; print(consent.level())"
```

Three ways to control it, in order of precedence:

1. **Environment** — `CONDOR_TELEMETRY=off` in your `.env`. The one full kill
   switch: it overrides everything, suppresses the notice, and is the right
   answer for an install that must send nothing at all.
2. **The dashboard** — Settings → Privacy, which every seat can read and only
   the admin can change
   (`GET`/`PUT /api/v1/settings/telemetry?level=ping|usage|off`). An install
   that runs without Telegram is shown the notice as a strip across the top of
   the dashboard instead, since it has no bot to be told through. Choosing
   `off` here — or "Turn off" on the notice — records a refusal, which is the
   durable form of the kill switch: it is stored rather than read from the
   environment of one process.
3. **`config.yml`** — edit the `telemetry` section directly:

   ```yaml
   telemetry:
     consent: granted   # or `denied`, which forces level `off`
     level: ping        # or `usage`
   ```

Downgrading from `usage` to `ping`, and turning reporting off, are both a
**withdrawal, not a pause**: the in-memory buffer and the outbox file are
deleted, so nothing already recorded can be sent afterwards.

**An install that already refused stays off.** Earlier builds shipped a "No
thanks" button, and its answer was recorded as `consent: denied`. That refusal
is honoured — such an install resolves to level `off`, is never re-asked, and
creates no install id — for as long as it stands. To opt back in, an admin
picks a level in Settings → Privacy (or sets `CONDOR_TELEMETRY=ping|usage`,
which overrides the stored answer like any other).

## Sharing a conversation

Separate from everything above. Telemetry is anonymous counts an admin consents
to once for the whole install; this is a **transcript**, and only the person who
held the conversation can hand it over. By default that is one at a time, every
time, after reading exactly what would be sent. There is a standing answer you
can choose instead — [Sharing automatically](#sharing-automatically) — and it is
off until you choose it.

The code is [`condor/sharing/`](condor/sharing/), and it deliberately shares no
module, no consent record, no queue file and no endpoint with
`condor/telemetry/`. The rule is asserted by a test: the sharing package never
imports `condor/telemetry/schema.py`. Widening that taxonomy to carry a
transcript would have deleted the very property that makes the "never
collected" list above checkable rather than merely claimed.

**How it works.** In the chat rail, each conversation has a share button. It
opens a dialog showing the redacted transcript, a plain sentence saying what was
replaced ("2 wallet addresses and 1 API key were replaced"), and a Download
button if you would rather keep the bytes than send them. Nothing is queued
until you confirm.

**What is redacted**, in two tiers:

- **Values this install holds are substituted exactly.** Server names, hosts and
  URLs, server credentials, LLM provider keys, your Telegram bot token, saved
  endpoint keys, Gateway wallet addresses, Telegram user ids and usernames, and
  your home directory path. These are not guessed at — they are read out of your
  own config and replaced wherever they appear.
- **Shapes that cannot be anything else are pattern-matched.** EVM and Solana
  addresses, 64-hex blobs (transaction hash *or* private key — never kept),
  prefixed API tokens (`sk-`, `sk-ant-`, `xoxb-`, `ghp_`, `AKIA…`), long
  mixed-case secret runs, email addresses, URLs carrying credentials in the
  userinfo or query string, IP addresses, and BIP-39 recovery phrases (checked
  against the actual wordlist, not guessed at structurally).

Each replacement becomes a stable pseudonym — the same wallet reads as the same
`SOL_ADDR_a3f91c` throughout, so the conversation still makes sense — computed
as an HMAC salted with a random secret that **never leaves your machine**. We
cannot reverse it, and the same address on two installs produces two unrelated
pseudonyms.

**What is deliberately kept, and why.** Amounts, balances, order sizes, prices
and PnL survive redaction. A transcript in which nobody can tell whether the
agent computed the right answer is a transcript with no value for improving the
agent, which is the entire reason for asking. Once the wallet, the server and
the user are pseudonymous, a number is not on its own an identifier. The dialog
says this before you confirm rather than leaving you to find out.

**What the scrubber will miss.** Free text is best-effort and cannot be
otherwise: no rule knows that "the vault key is hunter2" is a secret. That is
exactly why the payload is shown to you first, why it is one conversation at a
time, and why unsharing works. If you pasted something sensitive into a chat,
read the dialog before pressing Share.

**Group chats contain other people's words.** A transcript from a Telegram group
may quote people who never agreed to anything. Sharing it with the button is
your judgement call, and the dialog says so. Automatic sharing never takes one
at all — see [Sharing automatically](#sharing-automatically).

**Where it goes.** `https://telemetry.hummingbot.org/v1/conversations` — the
same host as telemetry, a different endpoint, a different table, its own rate
limit, and no code path in common. Fixed in the source like the other one. The
envelope carries a random `share_install_id` that is **not** your telemetry
install id, so a shared conversation cannot be joined to your install's
heartbeat history, and an install with `CONDOR_TELEMETRY=off` can still share.
Alongside it go the build version, branch, OS and Python version, which model
answered, and the counts of what was redacted.

## Sharing automatically

In Settings → Privacy there are three answers, and **Off** is what every install
starts at:

- **Off** — nothing is shared. The share button is still there if you want it.
- **Ask me** — the button, as described above. Every share is a decision you
  make with the transcript in front of you.
- **Always** — your conversations are shared on their own, redacted exactly the
  same way, **without showing you first**.

Always is the only path where nobody reads the payload before it leaves, so
everything below exists to compensate for that. It is deliberately narrower than
the button in five ways, each of which is enough on its own to stop a
conversation from going:

- **Idle.** A conversation is only taken once nothing has happened in it for 30
  minutes. Conversations never formally end, so "finished" has to be a
  heuristic; if you come back and continue one that was already sent, the next
  sweep replaces it with the longer transcript rather than adding a second copy.
- **Forward-only.** Only conversations *started after* you chose Always. Turning
  the setting on is consent to a policy from that moment, not a licence over
  your archive — your old chats are never swept, and if you want to hand one
  over you do it deliberately, with the button. Turning Always off and on again
  starts a fresh window rather than reaching back over the gap.
- **Single-author only.** A conversation that anyone but you can speak into — a
  Telegram group — is never taken automatically. You can consent for yourself;
  you cannot consent for the other people in the room. Those chats stay
  shareable with the button, where you are deciding with the transcript in front
  of you. A conversation from a surface this check does not recognise is
  excluded too, rather than assumed to be yours alone.
- **Excluded chats, forever.** While Always is on, every conversation it covers
  carries a marker in its own header saying so, with one click to leave that
  chat out. It cannot be dismissed — the point is that you should not be able to
  forget the setting is on — and an exclusion is honoured permanently, including
  after the conversation grows.
- **Rate limited.** At most 3 conversations per 15-minute sweep, which stays
  inside the collector's 12-per-hour allowance. A backlog drains oldest-first
  over several sweeps instead of bursting.

Everything else is identical to the button: the same scrubber, the same
pseudonyms, the same size cap, the same `share_id` and delete token, the same
endpoint. The only thing that differs on the wire is a flag saying the share was
automatic rather than chosen, so a reader of the corpus can tell a sample from a
signal.

**How to stop.** Choosing Off does two things immediately: no further
conversations are taken, and anything the sweep had queued but not yet sent is
**deleted** rather than merely held back. It does not touch what you have
already shared — that is the separate "Delete everything I've shared" button
below it, because turning off future sharing and withdrawing the past are two
different decisions and neither one is the default for the other. Both buttons
are there; you can press either, both, or neither.

The admin's install-wide veto and `CONDOR_SHARING=off` both outrank Always, the
same way they outrank the button. If sharing is turned off above your head,
Settings says so rather than leaving a switch that quietly does nothing.

**How to unshare.** Press Unshare in the dialog, or in Settings → Privacy, which
lists everything you currently have out there. Deleting a conversation unshares
it first, so a copy never outlives the chat it came from. The revocation works
without anyone knowing who you are: your install kept a random token and sent us
only its SHA-256, so posting the token is proof enough to delete the row. If the
network is down when you press it, the revocation is queued with its token and
completes later.

**How to turn it off.**

1. **Environment** — `CONDOR_SHARING=off` in your `.env`. The full kill switch;
   it overrides everything and nothing on the box can share.
2. **The dashboard** — Settings → Privacy. The admin holds an install-wide veto
   that hides the button for everyone
   (`GET`/`PUT /api/v1/sharing/settings`). Unsharing keeps working while the
   veto is on, so nobody is stranded with something they cannot take back.
3. **`config.yml`** — the `sharing` section:

   ```yaml
   sharing:
     enabled: false
   ```

There is nothing to turn off on a fresh install: the default is that nothing is
shared, and it stays that way until somebody presses the button — or turns on
Always, which nobody but that user can do for them.

## Changes to this document

Adding anything to the collected list requires a change to `schema.py`, a change
to this file, and a new notice to every install that has not turned reporting
off. In particular, adding trading pairs
would make positions inferable from timing and must not be done quietly.

The same applies to sharing, in its own terms. Sending anything a user has not
been shown, or sending anything a user has not asked for, would be a change to
`condor/sharing/`, a change to this file, and a new consent — not a default
someone flips. Always is exactly that kind of change, and it arrived as one: an
opt-in, off until chosen, described in full at the moment of the choice, and
narrowed by the five rules above precisely because it is the one path where the
scrubber is the last gate rather than the second-to-last.
