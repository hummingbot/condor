# Notification relay — deliver Condor notifications into any harness's conversation

Condor writes asynchronous events to a durable, harness-neutral outbox. This
relay tails that outbox and forwards each new entry to the conversation or
notification service selected by the operator. Synchronous replies ride the
MCP result back to the caller; asynchronous events need this separate delivery
path.

**The fix (no harness privileged, applied to the output path).** This relay
tails the outbox (`store/notifications.jsonl`) and delivers each new entry
through *the harness's own send primitive*, addressed to the conversation you
drive it from. Zero Condor core changes — it only reads the outbox, exactly
like the [Claude Code channel](../claude_channel) does for Claude Code.

```
              store/notifications.jsonl  (the harness-agnostic spine)
                        │
   ┌────────────────────┼─────────────────────┬────────────────────┐
   ▼                    ▼                     ▼
Dashboard        Claude Code channel     THIS relay
history          (push into session)   → OpenClaw/Hermes/etc.
```

## Configure

Set `CONDOR_NOTIFY_CMD` to a **JSON argv template**. Placeholders substitute as
*whole argv elements* — never shell-interpolated — so notification text cannot
inject arguments (verified in tests). Available: `{text}` `{agent_id}` `{kind}`
`{ts}` `{json}` (the full entry).

### OpenClaw

`openclaw message` sends to a target on a configured channel. Point it at the
conversation you use with OpenClaw:

```bash
export CONDOR_NOTIFY_CMD='["openclaw","message","send","--channel","discord","--target","<YOUR_CHANNEL>","--text","{text}"]'
python -m integrations.notify_relay.relay
```

### Hermes

Hermes exposes an API-server / webhook ingress. POST the notification to the
chat you use with Hermes (adjust host/path/body to your `hermes-api-server`):

```bash
export CONDOR_NOTIFY_CMD='["curl","-sS","-X","POST","http://localhost:8000/message","-H","Content-Type: application/json","--data-binary","{json}"]'
python -m integrations.notify_relay.relay
```

`{json}` delivers the whole entry (`text`, `agent_id`, `kind`, `ts`,
and routing metadata) so a small Hermes-side handler can route it correctly.

### Anything else

Any CLI that can post a message works — Slack `curl`, `ntfy`, a shell script.
The relay doesn't know or care which harness; it just runs your command per
entry.

## Behavior

- **Tails from end-of-file** at startup — only notifications emitted *after*
  the relay starts are delivered (history: `mcp__condor__get_notifications`
  or `GET /api/v1/notifications`).
- **Fail-soft**: a delivery that errors or exits non-zero is logged and
  skipped; the relay never dies on a bad send. Poll interval:
  `CONDOR_NOTIFY_POLL_S` (default 2s).
- **Runs alongside** any other consumers — the outbox fans out to all of them.

## "Same conversation" — the honest scope

For a single-user self-hosted setup (the common case), the target you
configure *is* the conversation you drive that harness from, so the
notification lands where you're looking. Making the target **dynamic** — so a
run started from harness X notifies back through X automatically without
per-relay config — means threading a reply-route from the MCP caller onto the
session/delegation. That's a deliberate follow-up (it reintroduces a scoped
version of the notification-route the framework originally left out); this
relay covers the self-hosted case today with zero core changes.
