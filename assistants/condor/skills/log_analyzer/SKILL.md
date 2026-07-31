---
name: log_analyzer
description: AI-driven log analysis for active bots, executors, and gateway — anomaly detection and failure pattern recognition over Hummingbot logs, for both real-time monitoring and retrospective diagnostics.
when_to_use: ANY request about the state/health of bot or system logs, errors, or warnings — checking, summarizing, triaging, finding recurring failure patterns, diagnosing why a bot is failing, or live log monitoring. Run the logs_summary routine for these, do NOT hand-roll with raw manage_bots. Triggers — "how are the logs", "how are the bots' logs", "any errors in the logs", "logs summary", "what's failing", "why is my bot erroring", "diagnose this bot", "watch the logs"; ES — "cómo están los logs", "hay errores", "resumen de logs", "por qué falla el bot".
created: 2026-06-26
source: builtin
references_routine: logs_summary
---

You are doing **AI-driven log analysis** over Hummingbot logs surfaced by the
Backend API. Goal: turn a wall of raw log lines into a diagnosis — *what* is
failing, *how often*, *since when*, *which component*, and *what to do*. Two
modes: **retrospective** (a one-shot summary / root-cause triage) and
**real-time** (a continuous watch that flags new anomalies as they happen).

This is not grep. The value is in **clustering** noisy messages into a handful of
failure *patterns*, **ranking** them by frequency/recency/blast-radius, and
**reasoning** about likely cause — that's the "AI" part: NLP-style message
templating + anomaly detection on top of the structured logs.

## Where logs come from (data sources)

All via `client = await get_client(context._chat_id, context=context)`:

| Source | Call | What you get |
|--------|------|--------------|
| All active bots | `client.bot_orchestration.get_active_bots_status()` | `data{bot: {error_logs, general_logs, status, performance, ...}}` |
| One bot | `client.bot_orchestration.get_bot_status(bot_name)` | same shape for a single bot (perf + logs + activity) |
| One executor | `client.executors.get_executor_logs(executor_id, limit=100, level="ERROR")` | per-executor log entries |
| Gateway (DEX) | `client.gateway.get_logs(tail=100)` | gateway process logs |

**Log entry shape** (each item in `error_logs` / `general_logs`):
```python
{"level_name": "ERROR",        # INFO | WARNING | NETWORK | ERROR | CRITICAL
 "level_no": 40,                # >=40 is a failure
 "msg": "Open order failed x-nbQe1H39BSLUC6552... Retrying 0/10",
 "timestamp": 1782477690.27,    # unix epoch float (UTC)
 "logger_name": "hummingbot.strategy_v2.executors.position_executor.position_executor"}
```
`error_logs` is the curated failure stream; `general_logs` also carries
`WARNING`/`NETWORK` lines worth scanning.

## The default move: run the `logs_summary` routine

For "any errors?", "logs summary", "what's failing across my bots" — **run the
routine, don't hand-roll it.** It already does the clustering, incident
detection, and report generation, tested against live bots.

```
manage_routines(action="run", name="logs_summary",
                config={"bot_name": "", "include_warnings": True,
                        "top_patterns": 8, "recent_incident_min": 15})
```
- `bot_name=""` → all active bots; set a substring to focus one bot.
- It returns a per-bot table (errors, warns, last-error age, top failure, source
  logger), the top cross-bot failure patterns, and flags **active incidents**
  (last error within `recent_incident_min`). It also writes a persistent report.

Read its output, then **add the diagnosis the routine can't** — explain the
patterns, judge severity, and recommend a fix. The routine surfaces the *what*;
you provide the *why* and *what next*.

## Technique — how to analyze (when going deeper than the routine)

1. **Normalize → cluster.** Raw messages differ only by IDs/numbers. Collapse
   them to a template before counting: strip order ids (`x-…`), hashes (`0x…`),
   UUIDs, timestamps, and numbers to placeholders, then group identical
   templates. 200 lines usually collapse to 3–5 real patterns. (The routine's
   `_normalize()` is the reference implementation — reuse its regexes.)
2. **Rank patterns** by `count` (severity), `recency` (last seen — is it still
   happening?), and `blast radius` (how many bots/executors hit it).
3. **Anomaly detection** — flag what's abnormal, not just present:
   - **Recency spike**: errors in the last N minutes ⇒ *active incident*.
   - **Rate**: errors-per-hour vs the bot's baseline; a sudden jump matters more
     than a steady trickle.
   - **New pattern**: a template not seen in earlier windows ⇒ regression.
   - **Correlated failure**: the same pattern across many bots at once ⇒ a shared
     cause (exchange outage, key/permission issue, gateway down) — not the bot.
4. **Attribute** via `logger_name` — it names the failing component
   (`position_executor`, `mqtt`, `connector…`). Group by it to localize.
5. **Diagnose & recommend.** Map the pattern to a likely cause and a concrete
   next step (see triage table), then say it plainly.

## Real-time mode (continuous watch)

For "watch the logs" / live monitoring, build a **continuous routine**
(`CONTINUOUS = True`) that polls `get_active_bots_status()` on an interval,
keeps a `seen` set of pattern fingerprints, and alerts via
`context.bot.send_message` only on **new or spiking** patterns (don't re-report
the same steady error every tick). Use a `LiveReport` to keep one always-current
incident board. Use the `routine_builder` skill for the mechanics; reuse the
`logs_summary` normalization/clustering logic as the core.

## Triage reference (common Hummingbot patterns)

| Pattern (normalized) | Source logger | Likely cause | Action |
|----------------------|---------------|--------------|--------|
| `Open order failed … Retrying N/10` | `position_executor` | Exchange reject (insufficient margin, price band, rate limit) | Check balance/leverage; if retries exhaust, inspect connector & symbol filters |
| `Take profit order failed … Retrying` | `position_executor` | Same as above on the TP leg | Verify position still open; check min-notional/tick size |
| `NETWORK …` / connection lost | `connector` / `mqtt` | Transient exchange/network blip | Tolerable if isolated & self-recovers; alarming if sustained/correlated |
| Auth / key / permission errors | `connector` | Bad/expired API key, missing permission | Re-check `/keys`; confirm trade & futures perms |
| Same error across many bots | any | Shared infra (exchange outage, gateway down, key) | Treat as one incident at the source, not per-bot |

## Reporting rules (non-negotiable)

- **Always re-fetch** — never reuse a prior run's log counts; logs move every
  second. Re-run before answering.
- Lead with the verdict: healthy vs. how many errors / active incidents. Then the
  top patterns, then the recommendation. `key: value`, not prose.
- Quote the **normalized pattern** and a **count + last-seen age**, never a single
  raw line as if it were the whole story.
- Don't guess a runtime or invent a cause — if a pattern is unfamiliar, say so and
  show the raw exemplar.

## Rules

- Be direct and concise. Run `logs_summary` first for any summary/triage ask;
  only hand-roll analysis when the routine's output isn't enough.
- One routine per task; if you build a real-time watcher, test it before handing
  it over (use `routine_builder`).
- Diagnosis is the deliverable — the counts are evidence, the cause + fix is the
  answer.
