---
name: operate_your_loop
description: The lifecycle of a loop that is already running — finding your own instances,
  pausing and resuming, the difference between `stop` (halts the ticks, leaves positions
  open) and `shutdown` (winds the positions down first), and getting a DEX swap out of
  `SUBMITTED` without a redeploy. Almost every step is a `control_agent` call; nothing here
  needs a YAML edit or a restart.
when_to_use: A loop is already running and the user wants it stopped, paused, resumed, wound
  down, or listed — "stop the agent", "pause it", "kill it", "close everything and stop",
  "what is running" — or a swap of its is stuck waiting for confirmation ("the swap is
  stuck", "it has been SUBMITTED for ages", "should I redeploy to clear it"). Read this
  BEFORE improvising — `loop_builder` covers authoring and launching, this covers
  everything after the loop is live.
created: '2026-08-31T00:00:00Z'
source: builtin
---

# Operate your loop

`loop_builder` gets a loop running. This playbook is everything **after** that: the
running session is a `TickEngine` and `control_agent` is the whole control surface for it.
There is no other one.

> **You do not need to prove ownership, and you must not hand-edit anything.** A running
> loop is not a YAML file the user can fix in an editor — the config on disk is what the
> *next* start reads, and changing it does nothing to the session ticking right now. There
> is no ownership handshake to perform either: the API already enforces ownership on every
> call (see below). If you find yourself inventing a ritual, you are off this playbook.

## 1. Find the instance

Every other action takes an `agent_id`, and the list is where it comes from:

```
control_agent(action="list")
```

Each entry is one **running instance** with its `agent_id` (the form is
`"<agent_slug>.<loop_slug>"` plus a run suffix), its `status` — `running`, `paused` or
`stopped` — and its config. `{"agents": [], "message": "No agents running"}` means nothing
is live for you.

Two companions when you need the wider picture:

- `manage_agents(action="list")` — the agents and the loops they *own*, running or not.
- `trading_agent_journal_read(agent_id=…, section="summary")` — what this instance has
  actually been doing.

**The ownership rule (SEC-251).** A loop is operable only by the user who started it.
Someone else's run is filtered out of your list rather than refused, so a "not found" from
`control_agent` on an `agent_id` you did not get from your own `list` means *it is not
yours* — not that the tool is broken and not that the id is malformed. Do not retry it, do
not go looking for a back door: say the run belongs to another user.

## 2. A temporary hold — `pause` / `resume`

```
control_agent(action="pause",  agent_id="<agent_id>")
control_agent(action="resume", agent_id="<agent_id>")
```

`pause` stops the *ticks*. The engine stays alive, its session stays open, its journal keeps
its run number, and **every position and executor it opened stays exactly as it is, now
unattended.** Nothing is monitoring a stop-loss between the pause and the resume — that is
the whole cost of a pause, and it is the reason a pause is for minutes, not days.

`resume` puts it back on cadence at the next interval. Both are instant and neither touches
capital.

## 3. Ending it — `stop` vs `shutdown`

This is the only choice in this playbook that can lose money, because the two verbs differ
in exactly one way: **what happens to the positions.**

### `stop` — halt the ticks, keep the positions

```
control_agent(action="stop", agent_id="<agent_id>")
```

Cancels the tick task, reaps the model subprocess, releases the session's ownership window
and writes the run out. It **closes nothing.** Executors it created keep running, positions
stay open, and from this moment no agent is watching them.

Choose it when the position is meant to survive the session: the user wants to take over
manually, you are restarting the loop with a different config, or the executors carry their
own stops and are supposed to keep working.

### `shutdown` — wind down, then halt

```
control_agent(action="shutdown", agent_id="<agent_id>")
```

The escalation. Before halting, it runs the winddown governed by the loop's
`shutdown.md` policy: a deterministic baseline that stops **this session's** executors with
`keep_position` set per policy, then a bounded LLM cleanup pass, then a verify pass that
re-queries positions, retries once, and loudly alerts the user about anything the policy
said to close and could not. It always ends stopped.

The policy's `on_kill_switch` decides what "wind down" means, and it is per loop:

- `flatten_all` — close everything.
- `keep_spot_close_perp` — close perp exposure, keep spot. This is the shipped default.
- `keep_all` — close nothing; effectively a `stop` with the ceremony.

So `shutdown` is **not** a guarantee that the book is flat. Read the loop's
`shutdown.md` before promising the user it is, and never describe `shutdown` as "closes
everything" without checking `on_kill_switch`.

Choose it when the loop should leave the market: a risk breach, an emergency, "close
everything and stop", or the user walking away from capital nothing else is monitoring.

### Deciding, in one line

> Is anything the loop opened still supposed to be live after it stops?
> **Yes → `stop`. No → `shutdown`.**

Say which one you are doing, in words, before you do it — "I'll stop the ticks and your SOL
position stays open" and "I'll wind the position down and then stop" are different
outcomes, and the user cannot infer which from the word "stop".

## 4. What needs a human, and what does not

`control_agent(action="start", …)` is **confirmation-gated** (SEC-275): starting an
unattended loop pauses for a human, who sees a summary naming the loop and — when you
overrode them — the execution mode and the size. Expect that prompt on a start; it is not
an error, and the loop does not begin until it is approved.

`list`, `stop`, `pause`, `resume`, `shutdown`, `get_state` and `set_state` are **not**
gated. They run immediately. Putting a human in front of a brake would mean the user
needing permission to stop their own loop, and `shutdown` in particular is the emergency
exit — a prompt nobody is awake to answer is worse than an early exit from the market.

One practical consequence: **always pass `action` as an explicit literal.** The gate fails
closed, so a call whose action it cannot read is treated as dangerous and pushed in front of
a human — a `stop` that stalls waiting for approval because the argument was built
dynamically is a brake that did not work.

## 5. Verify what you left behind

Stopping the loop is not the end of the job — confirm the state, and confirm it from the
sources that would show a problem:

```
control_agent(action="list")                                   # gone, or status "stopped"
trading_agent_journal_read(agent_id="<agent_id>", section="summary")
list_executors()                                               # what is still running
list_positions_held()                                          # what is still open
```

After a `stop`, the executors and positions you see are **expected** — that was the choice.
After a `shutdown`, anything still open that the policy said to close is the failure the
winddown alerts about: report it to the user explicitly and resolve it yourself
(`stop_executor`, or the `recover_orphaned_position` playbook if an LP position was left
on-chain without its executor).

Either way, tell the user what is still live. A loop that stopped cleanly while leaving
funded positions unattended reads as "done" and is not.

## 6. A swap stuck on `SUBMITTED`

A DEX swap that will not leave `SUBMITTED` is **not** a reason to restart or redeploy the
loop. It was at the Agent Builder Cup, because nothing else could get the entry out of
that state, and it cost race time (GitHub #278). It is a swap-level problem with a
swap-level fix, and the loop above it keeps ticking.

**Why it happens.** A swap leaves `SUBMITTED` only after a status read *succeeds*. A
Gateway that cannot be pinged, a `None` status, an unparseable network string or an
exception all leave the row exactly as it was — on purpose, because a poll that could not
check the chain must never be allowed to age a swap out. The cost is that the row stays
`SUBMITTED` for as long as the condition lasts.

**So `SUBMITTED` means unverifiable, not failed.** Nobody has been able to check. The
swap may well have landed on-chain and the tokens may already be in the wallet. Never
tell the user a stuck swap failed, never re-submit it as if it had not happened, and never
size the next order as though the funds were still unspent — check first.

The steps, in order:

```
search_swaps(status="SUBMITTED")             # how long has each been unverifiable?
get_swap_status(transaction_hash="<hash>")   # the duration, for one hash
```

Both report **how long** a swap has been unverifiable and raise an alert past the
threshold. That duration is the whole decision: seconds or a minute is an ordinary
confirmation still in flight, and anything past the alert is stuck.

1. **Read the chain yourself.** The explorer link on the swap is the only thing that can
   say what actually happened. Do that before saying anything to the user about the
   outcome.
2. **Check the wallet.** `get_portfolio_overview()` against the expected tokens tells you
   whether the swap landed, independently of what the swap row says.
3. **Resolve it.**

   ```
   resolve_swap(transaction_hash="<hash>")
   ```

   This asks the **server** to read the chain and record the swap's real outcome. It
   signs nothing, moves nothing, and Condor records no state of its own — the server's
   answer is the swap's state. A server too old to offer the route says so plainly and
   nothing changes; that is a capability answer, not an outage, and retrying it will not
   help.
4. **If the route is unavailable**, you are down to step 1 and step 2: report what the
   chain shows, say plainly that the swap row is unverifiable, and continue from the
   wallet balance rather than from the swap record. Still do not redeploy — a redeploy
   does not resolve the swap either, it only hides the row from this session.

Tell the user which of these happened. "The swap is unverifiable after 40 minutes, the
explorer shows it confirmed, and I have resolved the record" and "the swap is
unverifiable and I cannot tell yet" are different states, and the second one is an honest
answer.

## Reference

- `control_agent(action="get_state"|"set_state", agent_id=…, key=…, value=…, expires_in=…,
  clear=…)` — the instance's own scratch namespace, derived from `agent_id` and never
  readable across instances. Useful for cursors a loop carries between ticks; irrelevant to
  its lifecycle.
- Restarting is not a lifecycle action: end the current instance, then start a fresh one
  through `loop_builder`.
- Editing a loop's playbook or config (`manage_loops(action="update")`) does not reach a running
  instance. Stop it and start it again for the change to take effect.

## Rules

- Get the `agent_id` from `control_agent(action="list")`. Never construct one.
- Name the verb and its consequence for the positions before you call it.
- `stop` keeps positions. `shutdown` applies `shutdown.md`, which is not always "close all".
- "Not found" means someone else's run (SEC-251) — say so, don't work around it.
- Never tell a user to edit a deployed config, YAML or controller to stop a loop. It does
  nothing to the running session.
- A swap stuck on `SUBMITTED` is unverifiable, never failed. Read the chain, then
  `resolve_swap` — never redeploy the loop to clear one.
- Verify afterwards, and report anything still open.
