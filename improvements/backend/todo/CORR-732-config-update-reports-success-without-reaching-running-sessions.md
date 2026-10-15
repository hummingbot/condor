---
id: CORR-732
title: update_strategy_config reports success without saying it does not reach running sessions
summary: The route writes config.yml and reports updated, while a running engine keeps the config dict it copied at start — only restart_on_boot is pushed to live engines. An operator who edits a config and sees success has no way to learn the running agent still has the old one.
category: correctness
impact: medium
effort: S
risk: low
status: todo
brain: developer
files:
  - condor/web/routes/agents.py:2858
  - condor/web/routes/agents.py:3386
  - condor/agents/engine.py:238
depends_on: []
commits: []
created: 2026-10-14
---

## Problem

GitHub issue [#275](https://github.com/hummingbot/condor/issues/275): "Several
times during the race, an operator edited a config, saw 'updated', and assumed
the running agent had picked it up. It hadn't … 'I fixed it' and 'it's fixed'
drifted apart silently."

Confirmed at `45e60d60`. `update_strategy_config`
(`condor/web/routes/agents.py:2858-2876`) loads the full config, gates a pin
change, saves, and returns:

```python
save_full_config(strategy.home, config_dict)
return {"updated": True, "config": config_dict}
```

A running engine holds the `config` dict it copied at start
(`condor/agents/engine.py:238`), and the only key pushed to live engines is
`restart_on_boot` (`agents.py:3386-3393`). So `{"updated": True}` is true of the
file and says nothing about the process, and no caller — UI, agent or MCP tool —
is given anything to say otherwise.

The behaviour itself is defensible: a live trading loop should not have its
config swapped under it mid-tick. The defect is that the response cannot be
distinguished from one that did take effect.

## Proposed solution

Report what the write actually reached, rather than changing when it applies:

1. Return `applies_to_running: false` and `live_engines: N` from
   `update_strategy_config`, counting live engines for the strategy through
   `for_strategy()`.
2. Have the UI and the agent-facing surfaces render that as "saved; restart N
   sessions to apply", so the operator reads the state rather than inferring it.
3. Surface the `restart_required` flag hummingbot-api already returns from a
   connector write, which Condor currently passes through and drops.

The richer half of #275 — a per-engine **effective vs file** diff, the process
start time, and badging drifted sessions — is [[FEAT-136]], which builds on the
count this item exposes.

## Acceptance criteria

- [ ] `PUT /{slug}/strategies/{sslug}/config` returns `applies_to_running: false` and a `live_engines` count matching `for_strategy()`
- [ ] With no live engine the response says so, rather than reporting the same thing as a drifted save
- [ ] The dashboard renders "saved; restart N sessions to apply" when `live_engines > 0`
- [ ] A connector write whose upstream response carries `restart_required` surfaces it instead of dropping it
- [ ] `restart_on_boot` still reaches live engines as it does today
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Notes

- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60` with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0 failed in 98.1s, EXIT=0**. Quoted as provenance, not as a threshold — unrelated items add tests.
- Verified at `45e60d60` with `git show HEAD:<path>`; the route body is quoted
  above from the committed tree.
- Split from the rest of #275 as the operator asked - this item is the
  correctness half (a response that cannot be told apart from a different
  outcome), [[FEAT-136]] is the new capability (effective vs file, process start
  time, drift badges).
- Deliberately does **not** make config edits hot-apply to a running engine.
  Swapping a trading loop's config mid-run is a behaviour change with its own
  risk, and #275 does not ask for it.
- `condor/web/routes/agents.py` is also declared by the open [[SEC-715]] and by
  [[CORR-729]] - real file overlap for plan ordering.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.22
  `SEC-715 [todo]`, below the 0.50 fallback bar; it concerns which risk limits
  the route persists, not what the response reports.
