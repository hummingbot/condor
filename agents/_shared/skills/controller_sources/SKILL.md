---
name: controller_sources
description: How to own, push and use Hummingbot controllers from your agent folder — the folder is the source of truth, the server is a copy, and drift is never overwritten blind.
when_to_use: Before any manage_agent_controllers call; whenever you deploy, backtest or upload a config for a controller listed in your CONTROLLERS section; when a user hands you a controller .py and sample configs to onboard; when you are tempted to call manage_controllers upsert.
source: builtin
---

# Controller sources

## Seat check
- **Condor (chat):** you onboard. Write a user's controller into the target agent with
  `agent="<slug>"`, or `pull` it from a server. You may publish to `_shared` with
  `shared=true` (no `agent`).
- **An agent:** you operate your own folder. You can read `_shared` controllers but cannot
  edit or delete them.

## The one rule
**Your folder is the source of truth. The server holds a copy.** Every change to controller
code goes into the folder first (`write`), then to the server (`sync`).
`manage_controllers(action="upsert", target="controller")` is refused on every agent seat:
it would bypass the folder, the drift check and the backup, and the next `status` would
report your own edit as drift. `manage_controllers(action="delete", target="controller")`
needs a human's confirmation and is refused in a dry run or a winddown. Saved configs
(`target="config"`) are unaffected. Don't route around the refusal with `run_code`
(`client.controllers.create_or_update_controller(...)`): it is out of bounds for the same
reason.

## The four copies
A controller exists in four places at once. Keep them apart. A `sync` changes exactly one of
them, the server copy; every impact message says which of the other three stay behind.

| Copy | Where it lives | When it changes |
|---|---|---|
| **folder** | `agents/{slug}/controllers/` or `.condor/agents/{slug}/controllers/` | `manage_agent_controllers write` |
| **server** | the Hummingbot API's controllers directory | `sync` (create or overwrite) |
| **running bots** | each bot container's imported class | only on stop → archive → redeploy |
| **backtests** | the API process's imported class | only when the API restarts |

The server copy is one per **name**, shared by every agent (and every Condor, and every
human) pushing to that server. The impact lists the agents on *this* Condor that own the same
name; it cannot see another Condor or a hand edit. Those show up as `drift` instead.

## Layout
```
controllers/<name>/<name>.py        # folder name == file name == controller_name in every config
controllers/<name>/CONTROLLER.md    # optional: description, `type:` when it can't be inferred
controllers/<name>/sample_configs/<style>.yml
```
Single-file controllers only. A style must not be named `config`, because it would be dropped
on publish. The type is inferred from the base class (`DirectionalTradingControllerBase`,
`MarketMakingControllerBase`, `ControllerBase`); when it can't be, write CONTROLLER.md first:
```
manage_agent_controllers(action="write", name="pmm_king",
    controller_md="---\ntype: market_making\ndescription: Maker around mid\n---\n")
```

## Before deploying or backtesting: status first
```
manage_agent_controllers(action="status", name="pmm_king")
```
| verdict | meaning | do |
|---|---|---|
| `in_sync` | the server runs exactly your file | proceed |
| `missing` | the server has never seen it | `sync`, then proceed |
| `drift` | the server's copy differs from yours | **stop**, see below |
| `unreachable` | the server didn't answer | **stop**; this is NOT in sync, retry later or report |

## Drift: never overwrite blind
1. Run `sync` without `overwrite`. It refuses (`refused: true`) and returns the `diff`
   (server copy → your folder) and the **impact**: `impact_text` (prose) and `impact`
   (the same as data). The impact names the other agents with a controller of this name
   (same or different code), the running bots whose deployed configs use it, and the
   backtest caveat. "Could not check running bots" means **unknown**, never "none".
2. Read the diff. Is the server's side an edit someone made on purpose (a hotfix, another
   agent's version of the same name)?
3. **Show the user the diff and `impact_text` verbatim, then ask.** Interactive: ask in the
   chat. Loop: send a notification and skip the deploy this tick. Don't block and don't
   retry-overwrite.
4. Only with a go-ahead (or a loop playbook that explicitly authorises it) call
   `sync(overwrite=true)`. The server's copy is saved under `.server_backups/` first. The
   refusal in step 1 is the **preview** that makes the overwrite possible: without one from
   the last 15 minutes, or if the server or folder copy changed since, the overwrite is
   refused with `preview_required` and nothing is sent. That refusal is itself a fresh
   preview; show it and ask again. The human approving the call sees the same impact.
5. If the server's version is the right one, `pull` it into your folder instead
   (`pull` with `overwrite=true` replaces your differing file).

## After an overwrite: running bots and backtests are behind
The result carries `impact_text` again, now describing what happened.
- **Running bots:** tell the user which bots are now on the OLD class. They keep it until
  they are stopped, archived and redeployed. That is the user's call. Never restart a bot.
- **Backtests** (`backtest_cache_stale: true`): the API keeps the OLD class for backtests
  until it restarts. **Do not backtest that controller and trust the numbers.** Tell the user
  it needs an API restart. Never restart it yourself: a restart reaps running executors.
- Bots deployed after the sync start a fresh container and do use the new code.

## Styles: references, not live configs
- Sample configs are **starting points**. `upload_config` publishes one as
  `<controller>__<style>` (e.g. `pmm_king__aggressive`).
- To trade your own variant: read the style (`read` with `sample=`), change only what the
  situation needs (pair, `total_amount_quote` within the user's budget, spreads), and
  upload it under a new `config_name`. Never edit a style in place to tune one deployment.
- A config needs its controller on the server, so run `sync` first. `upload_config` refuses
  otherwise.
- Validation errors come back verbatim. Fix the field; don't retry blindly. The usual
  causes: a number sent as a string (`"5"`), a typo'd field (`extra="forbid"`), an enum
  written `PositionMode.ONEWAY` instead of `ONEWAY`, a `controller_name` that doesn't match
  the folder. Directional controllers: the field rules in `controller_development` apply.
- To change a **running** bot's config, use `manage_bots(action="update_config")`. Uploading
  a saved config (even with `overwrite=true`) does not touch running bots: each keeps the
  config it was deployed with.

## Editing a controller
- Read it (`read`), rewrite the **whole** file, and `write` it. Then `status` shows `drift`
  against the server. That's expected: it's your edit, so `sync(overwrite=true)` is correct
  here. Say so in your report.
- Every saved config referencing it keeps its old fields. Re-check and re-upload styles
  whose fields changed.
- **A shared controller:** you cannot edit it. If you need different behaviour, copy it
  under a **new name** into your own folder. Reusing the shared name would put two
  different files under one server name, and they would drift against each other forever.

## Onboarding a user's controller folder (Condor)
1. Review read-only: class names, controller type, config class fields, and for each
   sample: the `controller_name` matches, the fields exist, numbers are numbers. Report the
   problems before writing anything.
2. `write` the `.py` into the agent (`agent="<slug>"`, `name`, `code`), then each sample
   with `sample=` (`code` = the YAML text).
   Already on a server and nowhere else? `pull` it instead
   (`name`, `controller_type`, `configs=["<config id>", ...]`).
3. `status` → `sync` if missing. If it's in drift, follow the drift section; the user may
   have an older copy on the server.
4. `upload_config` each style the user wants available, and confirm it appears with
   `manage_controllers(action="describe", config_name=…)`.
5. Report: controller (created / in sync / drift pending), styles uploaded, open issues.

## Never
- `manage_controllers upsert target="controller"` for a controller that lives in a folder.
- `overwrite=true` without showing the user the diff and `impact_text` and having a go-ahead.
- Reading `unreachable` as fine.
- Backtesting right after a replacing sync and reporting the result as current.
- Restarting the API.
