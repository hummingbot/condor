---
id: CORR-737
title: "A tick resolves which server it trades on twice, from different keys, so the loop's data and its trading tools can be two different fleets"
summary: >-
  The tick resolves its server twice: _resolve_server keys the data client on
  user_id (SEC-164) while build_gated_client's toolset re-resolves from
  engine.chat_id (SEC-178). Measured on this install, every dashboard-launched
  loop reads one fleet and trades on another. Resolve once per run.
category: correctness
impact: high
effort: S
risk: medium
status: todo
brain: developer
files:
  - condor/agents/engine.py:1877
  - tests/test_pinned_server_refusal.py:319
  - agents/_shared/skills/loop_builder/SKILL.md:128
  - agents/condor/skills/agent_builder/SKILL.md:286
depends_on: []
commits: []
created: 2026-10-15
---

## Problem

A `TickEngine` run answers *"which Hummingbot server am I on?"* twice, through
the same rule but from different inputs, and nothing compares the two answers.

**The data leg.** `_resolve_server` (`condor/agents/engine.py:1651`) calls
`resolve_stored_server(cm, self.user_id, self.config.get("server_name"),
pinned=self.server_pinned, chat_id=self.user_id)` — `engine.py:1686-1692`.
`chat_id=self.user_id` is deliberate: SEC-164 removed the request-body `chat_id`
from this leg because `chat_defaults` is a global map and naming someone else's
chat picked up that chat's default server. The name it returns keys
`cm.get_client(server_name)` (`engine.py:1728`), which is the client `_tick`
passes into `_gather_tick_context` and `_run_model` (`engine.py:734-744`) — so
every provider reading, the risk state and the PnL series come from it.

**The tools leg.** `build_gated_client` (`engine.py:1857`) calls
`toolsets.build_mcp_servers_for_session(engine.user_id, engine.chat_id, ...)`
(`engine.py:1877-1885`), and that builder re-resolves with
`chat_id=chat_id` — the *actual* `engine.chat_id` (`condor/runtime/toolsets.py:401-408`).
The name it returns is baked into the mcp-hummingbot subprocess's
`--server-name` and API url (`toolsets.py:497`) and into its
`HUMMINGBOT_API_USERNAME`/`PASSWORD` env (`toolsets.py:499-500`) — so it decides
where the model's trading tools act.

`resolve_stored_server`'s chat-default candidate is reached whenever the
configured name is absent or unusable (`config_manager.py:1544-1552`), and
`get_effective_server` reads `chat_defaults[chat_id]` — a map that two writers
key differently: the web dashboard writes `set_chat_default_server(user.id, ...)`
(`condor/web/routes/settings.py:146`) and Telegram writes it keyed on the chat
(`handlers/config/servers.py:448`). One map, two namespaces, read by this one
run under both keys.

**Reproduced** at `d8f2bf31`, driving the real `resolve_stored_server` with a
stub `cm` carrying this install's own `config.yml` rows (owner `481175164`,
`chat_defaults` `{481175164: brigado_2, -1003055388363: local}`, both reachable
by the owner, `get_accessible_servers` in `server_access` order so `[0] == local`):

    server_name=''  (every shipped loop, after CORR-729)
      web launch, chat_id=0            data=brigado_2  trading=local      DIVERGE
      private chat, chat_id==user_id   data=brigado_2  trading=brigado_2  AGREE
      group chat                       data=brigado_2  trading=local      DIVERGE
    server_name='local'  (the AgentConfig default before CORR-729)
      web launch, chat_id=0            data=local      trading=local      AGREE
      group chat                       data=local      trading=local      AGREE

So the two legs agree only when the configured name resolves, or when
`chat_id == user_id` — i.e. a loop started from a private Telegram chat. Two
measurements say that is not the normal case:

* **No shipped loop configures a server.** `AgentConfig.server_name` defaults to
  `""` (`condor/agents/config.py:105`) and none of the six `agents/*/loops/*/loop.md`
  declares `server_name` in its `default_config`, so every one of them takes the
  resolution path. The house guidance says to keep it that way — "leave
  `server_name` empty unless the user pins it"
  (`agents/_shared/skills/loop_builder/SKILL.md:128`) and "Leave `server_name`
  empty" (`agents/condor/skills/agent_builder/SKILL.md:286`).
* **The dashboard sends no chat.** `startStrategy` posts only
  `{config, trading_context}` (`frontend/src/lib/api.ts:3437-3446`), so
  `StartStrategyRequest.chat_id` keeps its default `0`
  (`condor/web/routes/agents.py:840`) and reaches the engine as `chat_id=0`
  (`agents.py:3159`). The data leg then reads the *owner's own* chat default
  while the tools leg finds nothing under `0` and falls to `accessible[0]`.

That **corrects the finding as handed**, which said a web launch agrees because
both fall through to `accessible[0]`. Only the tools leg does; the data leg
never sees `chat_id=0` at all, because `_resolve_server` substitutes
`self.user_id`. On this install the divergence is therefore live for every
dashboard-launched loop, not only for group-chat ones, and the group default
being set is not a precondition — the group leg falls to `accessible[0]` either
way — measured with the `-1003055388363` row deleted, `data=brigado_2
trading=local`, still `DIVERGE`.

Consequences, all for a loop that is live:

* the model's prompt (balances, positions, executors, drift) describes fleet A
  while its `create_executor`/`deploy` calls land on fleet B — the ARCH-324
  "two numbers about two fleets" shape, applied to a trading loop;
* `risk_state.total_exposure` and `executor_count` are computed from A, so the
  brakes meter exposure on a fleet the session is not opening exposure on;
* a graceful stop cancels resting orders through `_get_client()`
  (`engine.py:427`, the leg CORR-727 just made actually execute) on A, while the
  emergency winddown's own LLM cleanup goes through the same
  `build_gated_client` (`condor/agents/shutdown.py:593`) and acts on B;
* nothing records either answer. The status dict publishes
  `self.config.get("server_name", "")` (`engine.py:1835`) — the *configured*
  value, which for every shipped loop is `""` — and `_get_client` logs only when
  resolution fails. See [[FEAT-136]] for surfacing the effective values.

This is not a defect in either ancestor; it is the decision neither of them
owned. **SEC-164** (`done`) removed `chat_id` from the engine's leg. **SEC-178**
(`done`) hit the same map on the session-spawn path and *deliberately kept*
`chat_id`, in its own words: "Do **not** drop `chat_id` from resolution the way
engine.py did — `handlers/config/servers.py:448` writes
`set_chat_default_server(chat_id, ...)` for Telegram group chats, so a group's
default is legitimately keyed by chat id. The access check preserves that case
and closes the escalation." Both are right about their own escalation; what
neither considered is a single run with both legs. **CORR-729** (`done`) then
unified the *rule* — one `resolve_stored_server` — and said so in
`toolsets.py:396-400`: "The subject is always `user_id` ... the same rule
resolves `TickEngine._resolve_server`." It left each call site's inputs alone,
which was in scope for it and is the whole of this item. Its fix is verified
still holding (the shared function exists, both callers use it, a pinned name
raises).

CORR-729 also widened the population without changing this code: it moved
`AgentConfig.server_name` from `"local"` to `""` so that "unset" stops
masquerading as a configured server. Correct — and the reproduction's second
block shows that on an install whose server is actually named `local`, the old
default made both legs agree, while `""` sends them down the diverging path.

Finally, the invariant is already asserted — one layer below where it breaks.
`tests/test_pinned_server_refusal.py:319`,
`test_both_call_sites_answer_the_same_way_on_the_same_inputs`, is CORR-729's own
"one rule, both call sites" test and it passes. It calls
`build_mcp_servers_for_session(OWNER, OWNER, ...)` — choosing `chat_id == user_id`
by hand rather than going through `build_gated_client` — against a fixture whose
`chat_defaults` map is empty for every test in the file ("a chat-default map
that is never set", `:87`; `grep chat_defaults` finds only the fixture's own two
lines). Its `_Engine` stub even sets `self.chat_id = 0` (`:114`) and the test
still passes `OWNER` as the toolset's chat id. The two inputs that make the
claim unfalsifiable are exactly the two the production wiring does not supply.

## Proposed solution

Resolve the server **once per run and hand the name down**, so the tick has one
answer rather than two that happen to agree. In `build_gated_client`
(`engine.py:1877`), pass the name `_resolve_server` already returns instead of
re-deriving one from `engine.chat_id`:

    resolved, _ = engine._resolve_server()
    mcp_servers = toolsets.build_mcp_servers_for_session(
        engine.user_id,
        engine.chat_id,                 # still the chat the condor MCP posts into
        server_name=resolved,
        agent_slug=engine.agent.slug,
        tick=True,
        server_pinned=bool(resolved),   # a name resolved for THIS run must stick
        server_required=True,
    )

`server_pinned=True` is what makes the handed name behave as chosen rather than
inherited: if it has since become unusable the toolset raises `ServerUnavailable`
naming it (CORR-729's rule) instead of quietly resolving to a second server, and
when nothing resolved at all `server_required=True` raises as it does today.
Deliberately **not** cached on the engine: `_resolve_server` is called per tick
today and re-checks existence and reach every time, which is CORR-729's "a name
that matched nothing at start stays unusable" property for a long-running loop.
The chain is dict lookups, so a second call per tick costs nothing measurable.

This is the repo's own named pattern, one layer over:
`config_manager.get_client`'s `server` parameter — "`server` names it outright,
for a caller that has already resolved which server the work belongs to and must
not have that answer re-derived. A routine is the case: it files its results
under the server it was launched against, so resolving the client from the chat's
ambient preferences instead is how a run gets executed on one box and recorded
under another" (`config_manager.py:1620-1630`). The tick engine is that sentence
with the two halves swapped.

The behaviour change this commits to, and it is the point of the item: a loop
launched from a group chat, or from the dashboard, trades on the server its
**owner** resolves, not on the group's ambient default. SEC-178's reason for
keeping the chat default stands for a *chat session*, where the chat is the
principal's current context; it does not reach an unattended loop whose own data
provider already ignores the chat by SEC-164's deliberate decision. Both skill
docs must stop promising a single ambient answer: `loop_builder/SKILL.md:128`
says "A loop you own runs on whichever server your agent resolves" (singular,
and currently false) and `agent_builder/SKILL.md:286` says an empty
`server_name` "means 'follow the chat's server'" (true of one leg only).

### Alternatives considered and rejected

* **Pass `chat_id=engine.user_id` into the toolset builder.** Measured dead:
  `chat_id` is also consumed by `_condor_mcp_args(chat_id, ...)`
  (`toolsets.py:228`, `effective_chat_id` at `:265`) as the chat the condor MCP
  subprocess posts into and resolves its session from, so overriding it would
  mis-target notifications and session resolution. It would need a second
  parameter — a worse spelling of the chosen fix.
* **Detect the disagreement and log or refuse it.** Leaves two resolutions and
  turns a silent wrong venue into a run that refuses for a configuration the
  operator cannot see — nothing surfaces either name today (`engine.py:1835`).
  Passing the resolved name down makes the disagreement unrepresentable, which
  is strictly stronger than reporting it.
* **Split `chat_defaults` into a user map and a chat map.** A migration of live
  `config.yml` for a defect that is one argument wide, and it would still leave
  the tick resolving twice.

## Acceptance criteria

- [ ] A test drives **`build_gated_client`** (not `build_mcp_servers_for_session`
      directly) with an engine whose `chat_id` is a group id different from
      `user_id`, against a `chat_defaults` map where the two keys name different
      servers both reachable by the owner, and asserts the emitted
      mcp-hummingbot entry's `--server-name` argv **and** its
      `HUMMINGBOT_API_USERNAME` match `engine._resolve_server()[0]`. Red at
      `d8f2bf31`, green after.
- [ ] The same assertion for `chat_id=0` — the body the dashboard actually sends
      — with the owner's own chat default naming a server that is not
      `get_accessible_servers(user_id)[0]`. Red at `d8f2bf31`.
- [ ] A test asserts that when `_resolve_server` resolves nothing, the tick seat
      still raises `ServerUnavailable` rather than mounting a serverless
      trading agent (CORR-729's `server_required` property, unchanged).
- [ ] A test asserts that a name handed down which has since become unusable
      raises `ServerUnavailable` naming it, rather than resolving to a second
      server.
- [ ] `condor/agents/shutdown.py:593` builds its winddown client through the
      same `build_gated_client`, so a test asserts the shutdown seat's
      `--server-name` equals the server `engine._get_client()` would reach.
- [ ] `tests/test_pinned_server_refusal.py`'s existing 14 tests still pass, and
      `test_both_call_sites_answer_the_same_way_on_the_same_inputs` is either
      extended or joined by a sibling that supplies the fixture's `chat_defaults=`
      parameter (used by no test today) and a `chat_id != user_id`. Measured at
      `d8f2bf31`: `.venv/bin/python -m pytest -q tests/test_pinned_server_refusal.py`
      = 14 passed in 0.48 s.
- [ ] `agents/_shared/skills/loop_builder/SKILL.md:128` and
      `agents/condor/skills/agent_builder/SKILL.md:286` are reworded to say a
      loop's trading venue follows its owner, not the chat it was started from.
- [ ] No test that passed at the merge base fails: run the repo's own gate
      (`make test`, i.e. `uv run pytest`) on the merge base and on the change in
      the same turn and compare. Provenance, not a threshold: at `d8f2bf31` in
      the `ws/issues-of-agent-builder-cup` worktree,
      `.venv/bin/python -m pytest -q` read **6536 passed, 2 skipped, 0 failed,
      exit 0 in 99.55 s**. Re-measure — the plan running over this workstream
      adds tests, so the count has already moved.

## Notes

Filed from a finding handed over by CORR-729's ship run, at `b38a3206`. Every
reading was taken at `d8f2bf31` (CORR-729's own closing commit); HEAD then moved
to `77b2e26c` (the running plan closing CORR-732) just before this doc was
committed. `git diff --name-only d8f2bf31 77b2e26c` touches six files, of which
this doc cites three: the `chat_id: int = 0` default
(`condor/web/routes/agents.py:840`), `set_chat_default_server(user.id, ...)`
(`settings.py:146`) and `startStrategy` (`api.ts:3437-3446`) all hold at both
shas, and the one anchor that moved — `chat_id=req.chat_id` in `_start`, `:3140`
to `:3159` — is cited above at the later sha. Every other reading is in a file
neither commit touched.

The divergence pre-dates CORR-729: at `b38a3206^` the engine already used
`get_effective_server(self.user_id)` while the toolset used
`get_effective_server(chat_id, user_data)`. What CORR-729 changed is which
installs reach it, by moving the `AgentConfig` default off `"local"`.

The two live `chat_defaults` group rows on this install are
`-1003055388363 -> local` and `-4615022999 -> brigado_server`. The second names
a server absent from the `servers` map, so `get_effective_server`'s own
existence check (`config_manager.py:1601`) drops it and that chat's loops fall to
`accessible[0]` — still a divergence from the data leg, by a different route.

The other two callers of `build_mcp_servers_for_session`
(`condor/agents/agent_run.py:174`, `condor/runtime/binding.py:204`) are
interactive seats with a single resolution and no data client of their own, so
they are unaffected and are deliberately out of scope here.

Adjacent and deliberately not folded in: [[FEAT-136]] (`planned`, plan
`89937873-9f5`) surfaces effective-versus-file config and would be where the
resolved server name becomes visible; [[CORR-735]] (`todo`) is the same tick's
exposure under-count from adopted executors, a different cause on the same
`risk_state`; [[FEAT-139]] (`planned`) adds a per-agent trading preflight and
would read whichever server this item settles on. `find_similar_backlog` over
219 Condor docs ranked CORR-729 top at 0.29 — below the 0.50 token-overlap bar
(the embedding provider was unreachable, `ollama:nomic-embed-text` ConnectError)
and the ancestor rather than coverage. `list_backlog(file="condor/agents/engine.py",
status="all")` returned all 78 docs on this file and none covers it.
