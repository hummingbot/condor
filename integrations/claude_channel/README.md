# Condor notifications → Claude Code channel

Pushes Condor agent notifications into a running Claude Code session, so when
you drive Condor from Claude Code, session/delegation/executor pings land in
the **same** session.

It's a one-way [Claude Code channel](https://code.claude.com/docs/en/channels):
a small MCP server that tails the notifications outbox
(`store/notifications.jsonl`, written by `condor/notifications.py`) and emits
each new entry as a `notifications/claude/channel` event. The outbox supports
multiple independent consumers, so adding this channel does not move or hide
events from the dashboard or another relay.

## Requirements

- [Bun](https://bun.sh) (`bun --version`)
- Channels are a **research preview**. On a Team/Enterprise Claude account an
  Owner must enable channels at
  [claude.ai → Admin settings → Claude Code → Channels](https://claude.ai/admin-settings/claude-code).
  Pro/Max personal accounts need no toggle.

## Setup

```bash
cd integrations/claude_channel && bun install
```

The server is already registered in the repo's `.mcp.json` as
`condor-notifications`. Start Claude Code from the repo root with the
development flag (custom channels aren't on the preview allowlist):

```bash
claude --dangerously-load-development-channels server:condor-notifications
```

A dim startup notice confirms it registered. From then on, every new outbox
entry appears in-session as:

```
<channel source="condor-notifications" agent_id="lp_rebalancer_2" kind="session">
Memecoin Trender — Tick 3: opened FEBU position, TP +1% / SL -1% / TTL 10m
</channel>
```

## Behavior

- **Tails from end-of-file at startup** — only notifications emitted *after*
  the session starts are pushed. To read history, call
  `mcp__condor__get_notifications` in-session.
- **One-way.** The session already has the full `mcp__condor__*` toolset to
  act on anything it reads; there's no reply path back through the channel.
- **Fail-soft.** A bad poll logs to stderr and retries; it never crashes the
  session. Outbox truncation/rotation is handled (offset resets).

## How it fits

```
agent tick / delegation / executor event
        │  condor.notifications.notify()
        ▼
store/notifications.jsonl  ──► dashboard / other relays
        │
        └──► this channel (tail) ──► Claude Code session (<channel> event)
```

The outbox is the channel-agnostic spine: this channel serves Claude Code, and
a Hermes/OpenClaw webhook adapter or the dashboard's notification API can
consume the same file.
