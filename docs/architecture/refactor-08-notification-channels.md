# Refactor 08 — notifications follow the user, not Telegram

Status: **proposed** (2026-07-12) · Research into how wedded Condor is to
Telegram, whether async messages (delegation done, tick errors, agent
reports) can land in the host the user is actually using, and where the
destination should be configured.

## 1. How wedded are we to Telegram? A layer-by-layer inventory

| Layer | Coupling | How hard-wired |
|---|---|---|
| **Identity** | `user_id` IS a Telegram user id everywhere: config.yml approved users, `store/user_{id}`, MCP identity auto-bind, web JWT `sub` | **Hard.** Telegram is the IdP. The web login itself bootstraps from Telegram (`/login` in TG chat mints a one-time token → redeemed at `/auth` → JWT). Softened once already: Tier-A auto-bind means a *runtime* needs no Telegram round-trip on single-user installs — the id is just an integer label there. |
| **Notifications** | Six emitters, ALL Telegram-only (see §2) | **Medium — this is the actionable layer.** Every emitter funnels to the Bot API with a `chat_id`; no abstraction in between. |
| **Confirmations** (`human_gate`) | Approve/Reject inline buttons in the TG chat | **Medium.** The web chat has its own WS permission path, but a consult triggered *from a host* (Claude Code via MCP) still sends its buttons to Telegram — the user is looking at Claude Code, the approval prompt is on their phone. Same-host problem #2. |
| **Command surface** | `/delegations`, `/memory`, `/agent` menus | **Soft.** Conveniences; the web has partial equivalents, MCP tools cover the rest. |
| **Chat transport** | Telegram long-polling bot + web WS | **Soft.** Already dual; hosts add a third via MCP. |

Verdict: Telegram is load-bearing as the **identity provider** and as the
**only push channel**. The first is a bigger project and not urgent (ids are
opaque integers at every call site — a future IdP swap is a mapping problem,
not a rewrite). The second is small, self-contained, and currently *broken
for non-Telegram users* — see the gaps.

## 2. Notification emitters (all → Telegram today)

1. **MCP `send_notification`** — what agents call inside ticks/delegations
   (the "🧪 Grid Range Harvester" reports). Direct Bot-API HTTP using
   `settings.bot_token` + `settings.chat_id`.
2. **`engine._notify`** — experiment complete, tick errors, max_ticks
   reached, risk blocks. Uses the live bot handed to `engine.start(bot=…)`.
3. **`delegate._notify_done`** — the completion ping. Live bot → routine
   bot → `_HttpBot` fallback chain, keyed by `dt.chat_id`.
4. **`shutdown.py` alerts** — emergency winddown reports, via `engine._notify`.
5. **Routine notifications** — routine-store bot.
6. **Telegram login tokens** — `/login` delivery (identity, not really a
   notification).

### Two real gaps found during this research

- **Web-launched runs notify NO ONE.** The web start route calls
  `engine.start()` with no bot and `chat_id=0`; `_notify` silently returns.
  A delegation started from the dashboard finishes and nothing anywhere
  tells the user. (The experiment pings during today's testing arrived only
  because the API caller passed a real TG chat_id.)
- **Host-launched work notifies the wrong surface.** Work started from
  Claude Code / OpenClaw via MCP pings Telegram — the user's attention is
  in the host. Tolerable (the phone buzzes), but it's the same
  wrong-surface problem, inverted.

(Hermes is the exception that proves the rule: it IS a Telegram harness, so
TG delivery lands "in the host" natively.)

## 3. Can we push INTO the host? (the hard truth)

For hosts connected via MCP (Claude Code, OpenClaw): **there is no
user-facing push channel.** MCP has server→client *protocol* notifications
(logging, progress, resource-updated) and, in newer spec revisions,
elicitation — but hosts surface none of these as an out-of-band "ping the
human" mechanism, and the condor MCP server is a *subprocess of the
session*: when the user closes the host, the channel is gone. Anything
async (a 10-minute delegation) cannot rely on it.

What DOES work per surface:

| Surface | Real-time (session open) | Async (user away) |
|---|---|---|
| Telegram | native | native |
| Hermes | native (it is Telegram) | native |
| Web dashboard | WS event → toast/badge | **inbox** (persisted, badge on return) |
| Claude Code / OpenClaw | **mailbox**: pending notifications piggy-backed on the next condor MCP tool result; the model relays them | none via MCP — falls back to Telegram / webhook / OS notifier |
| Any desktop (single-user install) | — | OS notification (macOS `osascript` / `terminal-notifier`), or ntfy/Pushover webhook |

The mailbox pattern deserves emphasis because it is cheap and matches how
users actually work: if you started a delegation from Claude Code, you are
almost certainly still *in* Claude Code making tool calls. Appending
`"unread_notifications": [...]` to condor MCP tool results (plus a
`check_notifications` tool and one line in the server instructions telling
the host to surface them) delivers the ping to the same host with zero new
infrastructure — it just requires notifications to be *persisted* somewhere
a tool can read.

## 4. Design: a notification bus with persisted inbox + sinks

One entry point in the main process replaces all six direct Bot-API calls:

```
notify(user_id, text, kind="info", source="agent_id | task_id | routine")
   │
   ├─ 1. PERSIST  → notifications table (condor.db): the source of truth.
   │               Web inbox + MCP mailbox read from here; delivery status
   │               per channel is bookkeeping, not truth.
   ├─ 2. DELIVER  → per-user channel list, in order:
   │      telegram   → Bot API (today's behavior; default ON when the user
   │                   has a chat binding)
   │      web        → WS broadcast to the user's open dashboard sockets
   │                   (toast + unread badge); no socket open = inbox only
   │      webhook    → POST to a user-configured URL (ntfy.sh, Pushover,
   │                   Slack/Discord webhook) — covers "any device" without
   │                   us owning another transport
   │      desktop    → osascript on the single-user box (optional nicety)
   └─ 3. MAILBOX  → condor MCP tools attach unread items to their results;
                    check_notifications / an ack marks them read
```

Notes that make this small rather than grand:

- The **emitters don't change semantics** — `engine._notify`,
  `_notify_done`, and the MCP tool become one-line calls into the bus. The
  bus lives in the main process (web API), which every emitter can already
  reach (the MCP tool goes through `call_main_api` like everything else).
- The **web-gap fix falls out for free**: web-launched runs have a
  `user_id`; persist + WS + badge needs no chat_id at all. `chat_id=0`
  stops meaning "silence".
- **Confirmations stay out of scope.** The gate needs a *reply*, not a
  ping; routing approvals cross-surface is its own refactor (the web WS
  path shows the shape). Don't entangle it here.

## 5. Where is the destination configured? (.env vs per-user)

**Not .env-first.** `.env` holds *credentials and global defaults*;
*routing* is per-user data. The split:

| Concern | Where | Why |
|---|---|---|
| Bot token, webhook signing secret | `.env` | secrets, per-install |
| Default channel order (`NOTIFY_CHANNELS=telegram,web`) | `.env` | install-level default |
| A user's channels + webhook URL | `config.yml` user entry (`users.{id}.notify:`) | per-user data, editable at runtime via `/settings` or a `manage_notifications` action — no restart to change where your pings go |
| Mailbox on/off | nothing | free; always on when notifications are persisted |

Rationale: config.yml already carries per-user structure (roles, approvals)
and has runtime read/write machinery; `.env` requires a restart and cannot
express "user A wants Telegram, user B wants ntfy". Upfront definition is
only genuinely required for webhook-type sinks (there's nothing to deliver
to until a URL exists) — Telegram and web inbox both work with zero new
config.

## 6. Identity: what we deliberately do NOT change

Decoupling identity from Telegram (local-first user ids, alternate login)
is a separate, larger refactor. This design keeps Telegram as the IdP and
notes only that nothing in the bus deepens the coupling: the bus keys on
`user_id` (an integer), and a future IdP swap changes how that integer is
minted, not how notifications route. The single-user install already runs
Telegram-free at runtime via Tier-A auto-bind; with a `webhook` or
`desktop` sink it would be Telegram-free for notifications too — the last
remaining TG dependency there would be the historical user id itself.

## 7. Phasing (when implemented)

1. **Bus + persistence + Telegram sink** — pure refactor of the six
   emitters; behavior identical, notifications table appears. Fixes
   nothing visible yet but everything after this is additive.
2. **Web sink** — WS broadcast + unread badge + inbox panel. Fixes the
   web-silence gap.
3. **MCP mailbox** — `check_notifications` tool + unread items appended to
   condor tool results + one server-instructions line. Fixes the
   same-host gap for Claude Code/OpenClaw.
4. **Webhook sink + per-user `notify:` config** — covers phones without
   Telegram, desktops, anything (ntfy/Pushover/Slack).

Each phase is independently shippable; 1+2 alone would have made today's
experiment pings visible in the dashboard where the work was started.
