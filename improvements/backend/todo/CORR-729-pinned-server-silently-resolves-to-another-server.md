---
id: CORR-729
title: A pinned server silently resolves to a different server, and server_name defaults to "local"
summary: toolsets.py and _resolve_server both try the pin, then the chat default, then accessible[0], so a session whose pinned server is unusable trades elsewhere on one log line. If nothing resolves the agent starts with no mcp-hummingbot, and server_name defaults to "local".
category: correctness
impact: high
effort: M
risk: medium
status: todo
brain: developer
files:
  - condor/runtime/toolsets.py:396
  - condor/runtime/toolsets.py:454
  - condor/agents/engine.py:1365
  - condor/agents/config.py:58
  - condor/web/routes/agents.py:3080
depends_on: []
commits: []
created: 2026-10-14
---

## Problem

GitHub issue [#279](https://github.com/hummingbot/condor/issues/279) reports two
silent fallbacks that cost diagnostic time during the race, "in both, a missing
or wrong setting kept working as *something else* instead of failing". This item
is the server half; the credential half is [[SEC-730]].

Confirmed at `45e60d60`. Both resolution chains try three candidates in order
and take the first usable one:

`condor/runtime/toolsets.py:396-403`

```
def candidates():
    yield server_name                             # the pin
    yield get_effective_server(chat_id, user_data) # the chat default
    accessible = cm.get_accessible_servers(user_id)
    yield accessible[0] if accessible else None   # whatever is reachable
```

`_resolve_server` (`condor/agents/engine.py:1365-1373`) is the same three steps
written out. Neither distinguishes "no pin was given, choose sensibly" from "a
pin was given and it is not usable", so a session pinned to a server that is
down, renamed or revoked trades on a different server's accounts with one
warning in the log.

Three details make it worse:

- if nothing resolves, the agent starts **without** mcp-hummingbot and only logs
  a warning (`toolsets.py:454-470`). A trading agent with no trading tools is
  started rather than refused.
- `server_name` defaults to `"local"` (`condor/agents/config.py:58`), and
  `setup-environment.sh:313-334` writes `"local"` as the default server. So a
  missing pin is not absent — it is a concrete name that then falls through the
  chain.
- `condor/web/routes/agents.py:3080-3105` *does* reject an explicitly requested
  unknown name, which is why this looks guarded. An inherited `"local"` is not
  explicitly requested, so it still falls through.

## Proposed solution

1. Distinguish a pin from a default at the type level — an explicitly chosen
   `server_name` resolves or raises; an absent one may choose. Both
   `toolsets.py:396` and `engine.py:1366` then share one rule instead of each
   open-coding the same three-step chain.
2. Refuse to start a trading agent with no MCP server rather than warning
   (`toolsets.py:454`). A session that cannot trade should fail where someone is
   watching, which is the reasoning `_resolve_spec` already applies to schedules.
3. Stop defaulting `server_name` to `"local"` in `AgentConfig`
   (`config.py:58`): require it, or resolve it once at agent creation and store
   the resolved name, so there is no name that means "unset".
4. Apply the same refusal in the restart path, which is where #274 saw "a
   restart bring back a server name that no longer existed". [[CORR-728]] adds
   the check to `_restart`; this item makes the resolver it calls refuse.

## Acceptance criteria

- [ ] A session with an explicit `server_name` that is not usable refuses to start, naming the server, instead of resolving to another one
- [ ] A session with no `server_name` still resolves through the existing default chain, so an unpinned agent is not broken by the change
- [ ] An agent that would start with no mcp-hummingbot refuses instead, naming why
- [ ] `AgentConfig` no longer carries `"local"` as a silent default, and an existing config with `server_name: local` on an install whose server is named `local` keeps working
- [ ] `_resolve_server` and `toolsets.py` resolve through one shared rule, asserted by a test exercising both call sites
- [ ] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## Notes

- Gate baseline, measured at doc-write time in a sibling worktree at `45e60d60` with `frontend/dist` linked: `uv run pytest` = **6427 passed, 2 skipped, 0 failed in 98.1s, EXIT=0**. Quoted as provenance, not as a threshold — unrelated items add tests.
- Verified at `45e60d60` with `git show HEAD:<path>`; both chains are quoted
  above from the committed tree.
- The second criterion is the one that makes this safe to ship. The naive
  version of this fix refuses every agent that never had a pin, which is most of
  them — "fail hard on a pin" and "fail hard on an absent pin" are different
  changes and only the first is wanted.
- `condor/web/routes/agents.py` is also declared by the open [[SEC-715]] and by
  [[CORR-732]]; the third criterion of this doc touches `config.py`, which
  [[SEC-715]] does not. Real file overlap for plan ordering.
- `find_similar_backlog` (token-overlap fallback, 196 docs) top hit 0.24
  `SEC-715 [todo]` — the risk-limit shallow-merge item, which is the same
  *shape* of defect (an omitted setting silently becoming a permissive value)
  on a different setting. Read it; it does not cover server resolution and its
  files only partly overlap, so this is filed new and cites it here.
