# Notification relay — deliver Condor notifications into any harness's conversation

**The problem this fixes.** Condor's notifications are hardwired to *Condor's
own* Telegram bot. But when you drive Condor through **Hermes** or **OpenClaw**,
you're in *that harness's* conversation (its own bot / channel) — so an async
ping (session tick, delegation done) lands in a *different* chat than the one
you're looking at. Synchronous replies ride the MCP tool result back into your
conversation fine; async notifications have no open request to ride, so they
need their own delivery path.

**The fix (no harness privileged, applied to the output path).** This relay
tails the outbox (`store/notifications.jsonl`) and delivers each new entry
through *the harness's own send primitive*, addressed to the conversation you
drive it from. Zero Condor core changes — it only reads the outbox, exactly
like the [Claude Code channel](../claude_channel) does for Claude Code.

```
              store/notifications.jsonl  (the harness-agnostic spine)
                        │
   ┌────────────────────┼─────────────────────┬────────────────────┐
   ▼                    ▼                     ▼                    ▼
Condor TG bot     Claude Code channel     THIS relay            THIS relay
(Condor chat)     (push into session)   → openclaw message    → Hermes API
                                          (OpenClaw chat)       (Hermes chat)
```

## Configure

Set `CONDOR_NOTIFY_CMD` to a **JSON argv template**. Placeholders substitute as
*whole argv elements* — never shell-interpolated — so notification text cannot
inject arguments (verified in tests). Available: `{text}` `{agent_id}` `{kind}`
`{ts}` `{json}` (the full entry).

### OpenClaw

`openclaw message` sends to a target on a configured channel. Point it at the
Telegram (or Discord/Slack/…) conversation you use with OpenClaw:

```bash
export CONDOR_NOTIFY_CMD='["openclaw","message","send","--channel","telegram","--target","<YOUR_CHAT_ID>","--text","{text}"]'
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
`chat_id`, …) so a small Hermes-side handler can route it to the right chat.

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
- **Runs alongside** the Telegram mirror and any other consumers — the outbox
  fans out to all of them.

## "Same conversation" — the honest scope

For a single-user self-hosted setup (the common case), the target you
configure *is* the conversation you drive that harness from, so the
notification lands where you're looking. Making the target **dynamic** — so a
run started from harness X notifies back through X automatically without
per-relay config — means threading a reply-route from the MCP caller onto the
session/delegation. That's a deliberate follow-up (it reintroduces a scoped
version of the notification-route the framework originally left out); this
relay covers the self-hosted case today with zero core changes.
