---
id: CORR-729
title: A pinned server silently resolves to a different server, and server_name defaults to "local"
summary: toolsets.py and _resolve_server both try the pin, then the chat default, then accessible[0], so a session whose pinned server is unusable trades elsewhere on one log line. If nothing resolves the agent starts with no mcp-hummingbot, and server_name defaults to "local".
category: correctness
impact: high
effort: M
risk: medium
status: done
brain: developer
files:
  - condor/runtime/toolsets.py:396
  - condor/runtime/toolsets.py:454
  - condor/agents/engine.py:1365
  - condor/agents/config.py:58
  - condor/web/routes/agents.py:3080
depends_on: []
commits:
  - "b38a3206 fix(servers): a pinned server resolves to itself or refuses (CORR-729)"
created: 2026-10-14
closed: 2026-10-15
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

- [x] A session with an explicit `server_name` that is not usable refuses to start, naming the server, instead of resolving to another one
- [x] A session with no `server_name` still resolves through the existing default chain, so an unpinned agent is not broken by the change
- [x] An agent that would start with no mcp-hummingbot refuses instead, naming why
- [x] `AgentConfig` no longer carries `"local"` as a silent default, and an existing config with `server_name: local` on an install whose server is named `local` keeps working
- [x] `_resolve_server` and `toolsets.py` resolve through one shared rule, asserted by a test exercising both call sites
- [x] No test that passed at the merge base fails - `uv run pytest` run on the merge base and on the change in the same turn, compared

## What shipped
`config_manager.resolve_stored_server` is the one rule, next to
`may_use_stored_server` for the layering reason that function's docstring gives.
Two flags carry the distinction the two open-coded chains could not express:

- `pinned` — the name was *chosen* for this run. It resolves or raises
  `ServerUnavailable`, which carries the name so a caller reports it without
  re-deriving it.
- `required` — the seat cannot run without a server at all. An empty chain
  raises instead of returning `None` for the caller to log a warning about.

Neither flag set is byte-for-byte the previous lenient chain, including its
laziness (resolving the chat default writes back into `user_data`, so it must
not run when a pin already answered).

Pinned-ness is **carried, not re-guessed**. `_start` already computed
`asked_for_it` and `_restart` `"server_name" in pins`; both now also set
`TickEngine.server_pinned`, so the bar the entrance applied once holds for the
whole life of the loop rather than only at the moment it was created. An
*inherited* name still falls through, which is the leniency `_start` and
`_check_restartable` deliberately allow and what criterion 2 protects.

The refusal lands where someone is watching: `_get_client` re-raises
`ServerUnavailable` instead of folding it into its best-effort `None`, `_tick`
propagates it, and `_run_ticks` journals it, notifies the owner once per distinct
error and settles the first tick — so `wait_first_tick` makes the start route
answer 502 and `reconcile_boot` report the restart as failed, each naming the
server.

Criterion 3 is enforced per seat rather than globally. `build_gated_client` (the
tick) declares `server_required=True`; the chat binding and a delegated agent run
do not, because a serverless chat is a valid outcome — the user with no grants yet
goes to the chat to get one, and `test_session_server_access` already pins that as
desired behaviour. The same criterion's other half is `_tick`: no API client used
to be journalled and returned cleanly, which is exactly how a loop that could do
nothing reported itself as ticking.

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
- **Files beyond the declared `files:`.** `condor/runtime/binding.py` and
  `condor/agents/agent_run.py` are the other two callers of
  `build_mcp_servers_for_session` that pass a pin (`agent.server_required` +
  `agent.server_name`); leaving them out would have meant the new flag existed
  but no chat or delegation used it. `condor/runtime/loops.py` is the restart
  path the fourth bullet of the proposed solution names — [[CORR-728]] added the
  up-front check, this carries the pin into the resolver as that bullet asks.
- **`server_required` is not read from `Agent.server_required`.** That field
  defaults to `True` on *every* agent, the chat's `condor` included, so wiring it
  straight through would refuse the chat for any user with no grants — the
  regression criterion 2 warns about, in the other direction. The flag is set by
  the seat that genuinely cannot work without a server (the tick), and the pin
  half is keyed on `bool(agent.server_name)`, which is the same line
  `SessionBinding.server_pinned` has always drawn.
- **Six test stand-ins gained the new constructor kwarg** (`RecordingEngine`,
  `RouteEngine`, two `FakeEngine`s, `StubEngine`, and the `SimpleNamespace`
  engine in `test_shutdown`). Those are stand-in updates, not expectation
  changes; the two real expectation changes are
  `test_create_strategy_config` (`server_name` now defaults to `""`, which is
  criterion 4 itself) and `test_session_config_refresh`, whose made-up pin
  `"somewhere-else"` now has to name a reachable server because a pin that does
  not is refused — the digest it asserts on is unchanged.
- **Gate**: `uv run pytest` = **6536 passed, 2 skipped, 0 failed in 100.66s,
  EXIT=0** on this change, with `frontend/dist` present (so no web-route tests
  skipped silently). Nothing fails, so there is no failure set to re-measure at
  the base; the doc-write provenance of 6427 at `45e60d60` is lower because the
  plan has landed ten further items since and this one adds 17 tests.
  `uv run black --check .` and `uv run isort --check .` are both clean.
- **Found while here, filed rather than fixed**: the tick engine resolves its
  server *twice* from different inputs — `_resolve_server` passes the owner's
  user id as the chat key (deliberately, SEC-149/SEC-164), while
  `build_gated_client` passes `engine.chat_id` down to the toolset builder. For a
  loop launched from a Telegram *group*, those two can legitimately resolve to
  different servers, so the loop's data client and its MCP trading tools could be
  pointed at different fleets. Not a credential leak (both ends are gated by
  `may_use_stored_server`), and out of this item's scope.
