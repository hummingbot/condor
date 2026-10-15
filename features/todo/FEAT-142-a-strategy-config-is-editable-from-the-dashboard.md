---
id: FEAT-142
title: "A strategy's stored config is editable from the dashboard, and the save says what it reached"
summary: >-
  Nothing calls `PUT /agents/{slug}/strategies/{sslug}/config`. The dashboard
  renders a strategy's config read-only apart from the `restart_on_boot`
  toggle, so the only durable edit is hand-writing `config.yml` or curl. Give
  `ConfigCard` a YAML editor over that route, printing what the save reached.
status: todo
effort: M
risk: medium
brain: developer
new_files:
  - frontend/src/components/agent/workspace/StrategyConfigEditor.tsx
  - frontend/src/components/agent/workspace/StrategyConfigEditor.test.tsx
touched_files:
  - frontend/src/lib/api.ts
  - frontend/src/components/agent/workspace/PlaybookView.tsx
  - frontend/src/components/agent/workspace/PlaybookView.test.tsx
depends_on: []
commits: []
created: 2026-10-15
---

## Objective

An operator can change a strategy's stored config — the one the *next* session
starts on — from the dashboard, and is told in the same gesture how many
running sessions still hold the old one.

## Context and constraints

[[CORR-732]]'s closing notes already wrote this item's problem down, as the
reason it narrowed its own third acceptance criterion:

> There is no dashboard caller of `PUT /{slug}/strategies/{sslug}/config` to
> render anything … Adding an unused `updateStrategyConfig` client method, or
> building the strategy-config editor the criterion presupposes, would both be
> scope this item did not ask for.

So this is the decision that was deferred, not a new discovery. Re-measured at
`fae40bdd`: `git grep` over the whole tree for the route path, for
`update_strategy_config` and for any `strategies/.../config` URL matches the
route definition (`condor/web/routes/agents.py:2860-2861`) and **three test
files** — `tests/test_agent_create_server_pin_access.py:195,210`,
`tests/test_agent_loop_lifecycle_ownership.py:318` and
`tests/test_config_write_reports_what_it_reached.py` — and nothing else.
`frontend/src/lib/api.ts` has `getStrategy`, `updateStrategyMd`,
`updateStrategyLearnings`, `deleteStrategy` and `setRestartOnBoot` (`:3475`)
for this strategy, and no config write. The MCP agent toolset builds
`/start` (`mcp_servers/condor/tools/trading_agent.py:459`), the lifecycle verbs
(`:481`) and `/state` (`:699`) — never `/config`.

**What the dashboard can do with a config today, counted.**

| surface | keys | durable? |
| --- | --- | --- |
| `ConfigCard` read-only rows (`PlaybookView.tsx:402-419` names 16 + 6 nested risk limits, plus an `Other` catch-all) | 22 named | read only |
| Start New Session dialog (`AgentControls.tsx:67-82`) | 6 + 3 nested | **no** — `_start` does `config_dict.update(req.config)` (`agents.py:3098`) and never calls `save_full_config` |
| "Resume after restart" toggle (`PlaybookView.tsx:446`) | 1 | yes |

So the dashboard reads 22 keys, lets an operator *type* 9 of them for one
session, and durably writes exactly one. The only writers of a strategy's
`config.yml` in the whole repo are `create_strategy` (`agents.py:2748`, at
creation), `update_strategy_config` (`:2879`, no caller) and
`set_restart_on_boot` (`:3432`); the engine's `save_full_config` at
`condor/agents/engine.py:282` writes the *session* copy, not the strategy's.
After creation there is therefore no durable edit gesture anywhere — not in the
dashboard, not in Telegram (`handlers/` imports neither `load_full_config` nor
`save_full_config`; its `key=value` bulk editors are bot **controller** configs),
and not in the agent's own toolset.

Constraints:

- **The route merges, it does not replace.** `config_dict.update(req.config)`
  (`agents.py:2878`) means a key *absent* from the body keeps its stored value,
  so an editor that round-trips the whole mapping can set and change keys but
  can never remove one. The merge semantics of this route are already owned by
  [[SEC-715]] (open, declaring `agents.py:2869,2875,2876`); this item must not
  change them, and must not be built on the assumption that they change.
  Sending the **complete** mapping is also what makes the editor immune to the
  partial-body loss SEC-715 describes: every nested block is present, so the
  shallow merge has nothing to drop.
- **The response is already right.** CORR-732 shipped `applies_to_running`,
  `live_engines` and a `message` of either `"Saved; restart N session(s) to
  apply."` or `"Saved. No session is running, so the next one starts on it."`.
  This item renders it; it adds no backend field and touches no Python file.
- **Both server-side gates stay as they are.** `_require_no_foreign_live_run`
  (`:2869`) and `_gate_pin_change` (`:2873`, SEC-693) are shared helpers with 8
  and 7 call sites, and this route's wiring of them is pinned by the three test
  files above. The editor must surface their 403, not duplicate their logic.
- The key set is open-ended by design: `ConfigCard`'s `Other` group exists
  because "a config the reader cannot see is a config they cannot debug", so
  any editor has to handle keys this build has no name for.

## Alternatives considered

**A. Keep the route API-only and document it** (the other disposition this item
was raised to settle). One paragraph in the agent-builder skill or an API note,
no code. Rejected: it leaves hand-editing `config.yml` on the host as the only
edit gesture, which is the thing [[FEAT-131]] independently complains about
("the only ways to set them are hand-editing the agent's `config.yml`"); and a
live, authenticated write route that no product path exercises is surface that
drifts — CORR-732 had to change its response shape once already, and no client
would have noticed if it had broken it.

**B. Typed form fields for every key, mirroring the Start dialog.** Rejected on
two measured grounds: FEAT-131 is already doing the typed-control work for the
six risk limits *in the Start dialog*, so this would be a second place holding
field-by-field knowledge of the same keys; and a typed form cannot edit the
`Other` group at all, which is exactly the "a newer build writes keys this
dashboard has no name for" case `ConfigCard` already renders.

**C. A YAML editor in `ConfigCard`, reusing the repo's config-editor idiom.**
Chosen — see below.

**D. Make `_start` persist the dialog's config** (one `save_full_config` call in
`agents.py:3096-3098`). Tempting and wrong: it silently turns "start a session
with these settings" into "change this strategy's defaults", which is a
different act with different consequences; [[CORR-387]] established that the
dialog's fields are a per-open snapshot of the stored config precisely so an
open dialog cannot overwrite a setting written since; and it would still give
no way to edit a config without starting a live trading session.

**E. Expose it as an MCP tool so the loop tunes its own config.** The toolset
already reaches this API for four routes, so it is the cheapest build. Rejected:
`risk_limits` are the brakes on the loop, and a session that can raise its own
`max_position_size_quote` is a session with no limit. If an agent-facing config
tool is ever wanted it is a separate decision about which keys, not a client for
this route.

## Decision

**C.** `ConfigCard` gains the edit affordance `DocCard` already established in
the same file (`:275-366`), and in edit mode renders the stored config as YAML using the
helpers the repo's three existing config editors share. No backend change.

Why YAML rather than controls: `frontend/src/lib/configYaml.ts` exists for
exactly this shape — its `configToYaml` docstring names its default policy
"round-trip-safe, used by the editable config editors" (strip only `id`,
preserve key order, `lineWidth: -1`), and a strategy config has no `id` key, so
the default policy is a clean round-trip. `validateYamlMapping` (`:45`) is
described as "what the editors call on every keystroke", and `CodeEditor`
already has a YAML mode (`frontend/src/components/editor/CodeEditor.tsx:48`).
Three call sites already use this idiom (`perf/YamlConfigEditor.tsx`,
`editor/EditorDialogs.tsx:467-470`, `pages/BotDetail.tsx:204-207`), and the
nested `risk_limits` block is a mapping YAML shows plainly and no flat form
does.

## Design

1. **`frontend/src/lib/api.ts`** — add beside `setRestartOnBoot` (`:3475`):

   ```ts
   updateStrategyConfig: (slug: string, sslug: string, config: Record<string, unknown>) =>
     apiFetch<{
       updated: boolean;
       config: Record<string, unknown>;
       applies_to_running: boolean;
       live_engines: number;
       message: string;
     }>(`/api/v1/agents/${encodeURIComponent(slug)}/strategies/${encodeURIComponent(sslug)}/config`,
        { method: "PUT", body: JSON.stringify({ config }) }),
   ```

   The body shape is `UpdateConfigRequest` (`agents.py:809-810`): a single
   `config` mapping. All five response fields are typed, so the `message`
   CORR-732 added cannot be dropped by a client that only reads `updated`.

2. **`StrategyConfigEditor.tsx`** (new) — `YamlConfigEditor.tsx:36-74` one route
   over, as its own module rather than 130 more lines in a 621-line file, which
   is the reason [[ARCH-300]] gave for lifting the first one out. It owns:
   `originalYaml = useMemo(() => configToYaml(config), [config])`; a buffer
   seeded from it and re-seeded when the *string* changes (so a refetch that
   yields identical content does not discard unsaved edits); `isDirty`;
   `parseError` from `validateYamlMapping` on each keystroke; and a save
   mutation that parses with `parseYamlMapping`, calls
   `api.updateStrategyConfig`, invalidates `["strategy", slug, sslug]` and
   renders the response's `message` verbatim.

3. **Removal is refused, not silently ignored.** Before saving, the editor
   compares the parsed mapping's top-level keys against the stored config's. A
   key the buffer dropped would be kept by the route's merge, so the save is
   refused client-side with the keys named and the instruction to set the key's
   off value (`-1` for the four limits that read a negative as "no limit",
   `""` for `server_name`) instead of deleting the line. This is the one place
   the editor has to say something the route does not: it is a consequence of
   the merge, and SEC-715 owns whether that merge ever changes.

4. **`PlaybookView.tsx`** — `ConfigCard`'s header gains the Edit/Done button
   `DocCard` uses (`:313-328`), with the same dirty-aware discard confirmation;
   edit mode swaps the read-only groups for `StrategyConfigEditor`. The
   "Resume after restart" toggle stays exactly where it is and keeps going
   through `setRestartOnBoot`: it is the one write that reaches live engines,
   and routing it through this editor would quietly make it stop doing that.

5. **A refusal is printed, not swallowed.** A 403 from either gate renders in
   the card next to Save, the way the restart toggle's refusal already does
   ("a switch that snaps back silently reads as a click that did not
   register").

## Implementation plan

1. Add `updateStrategyConfig` to `api.ts`; assert URL, method and body in
   `PlaybookView.test.tsx`'s existing `api` mock shape.
2. Write `StrategyConfigEditor.tsx` by adapting `YamlConfigEditor.tsx`, and its
   test file alongside.
3. Wire the Edit toggle and edit-mode swap into `ConfigCard`.
4. Extend `PlaybookView.test.tsx` for the toggle and the `message` rendering.
5. `npx vitest run`, `npx tsc -b --force`, `npm run lint:ci`, `uv run pytest`.

## Acceptance criteria

- [ ] `api.updateStrategyConfig(slug, sslug, config)` exists, PUTs to
      `/api/v1/agents/<slug>/strategies/<sslug>/config` with `{ config }`, and
      its return type names all five fields the route answers
      (`updated`, `config`, `applies_to_running`, `live_engines`, `message`)
- [ ] `ConfigCard` renders an Edit control; in edit mode the buffer round-trips
      the stored config — `parseYamlMapping(configToYaml(config))` is deep-equal
      to `strategy.config` for a fixture carrying the nested `risk_limits`
      block **and** a key the `NAMED` set does not list (the `Other` case)
- [ ] Saving posts the parsed mapping and renders the response's `message`
      **verbatim**, including the `live_engines > 0` wording "Saved; restart N
      sessions to apply." — this is [[CORR-732]]'s narrowed third criterion,
      landed
- [ ] An unparseable buffer disables Save and shows the `validateYamlMapping`
      message; no request is made (assert the mocked client was not called)
- [ ] A buffer missing a top-level key the stored config has is refused before
      any request, with the dropped keys named in the message
- [ ] A 403 from the route is printed in the card, and the read-only rows still
      show the stored values when edit mode is closed
- [ ] "Resume after restart" still writes through `api.setRestartOnBoot` and not
      through the new editor — a regression lock on the one config write that
      reaches live engines
- [ ] No test that passed at the merge base fails: `uv run pytest` and
      `npx vitest run` run on the merge base and on the change in the same turn
      and compared. Provenance, measured at `fae40bdd` in a detached worktree
      with `.venv` symlinked, `.env` and `frontend/node_modules` linked in:
      pytest = **6547 passed, 2 skipped, 0 failed in 110.42s, EXIT=0**;
      `npx vitest run` = **218 files, 2263 tests passed, EXIT=0**. Quoted as
      provenance, never as a threshold — unrelated items add tests
- [ ] `npx tsc -b --force` exits 0 and `npm run lint:ci` reports no new lint
      errors

## Risks and notes

- **`risk: medium` is about what the surface can set, not about the diff.** The
  diff is frontend-only. But the editor can write `server_name`, `agent_key`
  and every risk limit, which decide what the next session may do with money.
  Both server-side gates stay in force, and the one bypass worth stating is
  already closed on the other side: a hand-edited `server_name` escapes the
  write-time pin gate, but `_start` still runs `check_server_access` on an
  inherited name that resolves to a real server (`agents.py:3115-3118`,
  SEC-164/[[CORR-729]]), so the money-moving step is gated either way. The cost
  of the status quo is therefore usability and auditability, not an open hole —
  and this item reduces it by moving the edit onto the gated path.
- **The scope that raised this overstated one thing, and it is worth recording:**
  "a regression in that gate would be invisible to every product path" is not
  true. The route's two gates are directly covered —
  `test_update_strategy_config_with_a_foreign_server_is_refused`, a parametrized
  `test_update_strategy_config_with_stored_or_own_pin_works`, and the route's
  row in `STRATEGY_WRITES` — plus CORR-732's own 11-test file. What is actually
  missing is the capability, not the test.
- **[[FEAT-136]] is the read half and is held by a live plan.** It exposes
  effective-vs-file drift, process identity and the effective model, and adds no
  config write; it `depends_on` CORR-732 for the count. Plan `89937873-9f5` was
  `running`, chunk 13 of 22, holding FEAT-136 and 20 other docs when this was
  filed, so nothing was written into it. If both land, FEAT-136's drift badge is
  what tells the operator the restart this item's `message` asked for has not
  happened yet.
- **[[FEAT-131]] is the same gap from the session side** and does not close this
  one: it adds the three missing risk-limit fields to the Start dialog, which is
  a session-only write (see the table above). Two notes on it: it `depends_on`
  [[SEC-715]] because a *partial* `risk_limits` block loses the keys it omits,
  which this item's full-mapping editor is not exposed to; and its sentence
  "the only ways to set them are hand-editing the agent's `config.yml` or the
  Telegram `key=value` config editor" is wrong about the second — `handlers/`
  imports no strategy-config function at all, and its `key=value` bulk editors
  (`handlers/bots/controller_handlers.py:702,877`, `handlers/bots/menu.py:1704`)
  edit a Hummingbot **bot controller** config, a different object. FEAT-131
  lives on `main` and not on this branch, so it was cited rather than amended.
- **`frontend/src/lib/api.ts` is the hottest file on this branch** — seven open
  docs declare it (FEAT-133, FEAT-135, FEAT-136, FEAT-137, FEAT-138, FEAT-141
  and this one, plus [[CORR-737]]), and FEAT-135's chunk was editing it in the
  shared checkout while this doc was being filed. Real file overlap for plan
  ordering; commit with a pathspec.
  `frontend/src/components/agent/workspace/PlaybookView.tsx` is declared by no
  other doc on this branch (FEAT-131 and SEC-715 mention it in prose but declare
  `AgentControls.tsx`), so the card itself is uncontended.
- **No post-merge task is owed.** Every criterion above is observable in the
  implementing session with `vitest` and the two mocked gates; none needs the
  live stack, a port the running install holds, or a service restart, so none of
  it has to be split onto a `tasks/` doc.
- `find_similar_backlog` ran in token-overlap fallback (ollama
  `nomic-embed-text` `ConnectError`) over 702 docs; top hit **0.26 CORR-732
  `[done]`**, then SEC-693 0.26 and CORR-635 0.24 — nothing near the 0.50
  fallback bar. The two decisive neighbours, FEAT-131 (0.20) and SEC-715 (not
  ranked at all), were found by reading the ranked list's bodies and by grepping
  the route's own line numbers in the backlog.
- Verified at `fae40bdd` with `git show HEAD:<path>`; every line number above
  was re-read from the committed tree, not from the shared checkout. This
  branch's worktree carries only the force-tracked backlog docs
  (`.gitignore:186-187` ignores `/improvements/` and `/features/`), so FEAT-131
  and SEC-715 resolve to no file here and are cited by id by the house
  convention.
