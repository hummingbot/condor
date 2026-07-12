# Refactor 10 — onboarding by harness selection: `condor init`

Status: **proposed** (2026-07-12). The frame that replaced refactor-09:
Condor keeps its own harness, and no harness is privileged. Installing
Condor ends with one question — *"how will you talk to it?"* — answered
by selecting existing harnesses (Claude Code, OpenClaw, Hermes) and/or
Condor's own. Assumes condor + hummingbot-api on the same box.

## 1. The gap today

The installer (`hummingbot/deploy` setup.sh) assumes the answer is
Telegram: `TELEGRAM_TOKEN` and `ADMIN_USER_ID` are the only `.env` keys
and the installer walks through both. A user who lives in Claude Code
must still create a Telegram bot to get through install. Meanwhile the
MCP-host path already *works* — repo-root `.mcp.json` (Claude Code picks
it up as workspace config), `.claude/skills` symlinks, OpenClaw scan,
the Hermes tap layout, and Tier-A identity auto-bind were all built in
the harness spike — but it is reachable only if you already know it
exists. Onboarding and capability have diverged; this refactor closes
that gap with a selection step, not new architecture.

## 2. The command: `condor init`

One interactive, idempotent, re-runnable command (flag form for
scripts: `condor init --harness claude-code,hermes`). Harness choice is
a **multi-select** — the options are not exclusive; the same Tier 2
serves all of them concurrently (proven live in the harness spike).
Ship it as a `condor` console script (`[project.scripts]` in
pyproject); `init` is the first subcommand, leaving room for later ones
(`login-token`, `tui`).

### Step 1 — identity anchor (the one question that matters)

Ask: **"Do you use Telegram?"**

- **Yes** → use the Telegram user id as the approved user id, even if
  no Condor-harness is selected now. This future-proofs everything: a
  TG id today means pings, `human_gate` buttons, and `/login` can be
  switched on later with one `.env` line, because in DMs
  `chat_id == user_id`.
- **No** → mint a random integer id. The id is an identifier, not a
  credential (`settings.ensure_identity` rationale: the OS user
  launching things already holds every secret on disk). Loud caveat in
  the output: enabling the Telegram sink later requires the id to BE a
  TG id — that is a config.yml edit, not a migration, but say it now.

Write the sole approved user into `config.yml` via config_manager. This
single entry is what makes Tier-A auto-bind fire for every MCP host.

### Step 2 — Tier 2 wiring

Same-box assumption: register `servers.local = localhost:8000` (the
existing config.yml shape), probe it, and **fail loudly** if
hummingbot-api is not reachable — with the exact install command to fix
it. No silent "configure later" path.

### Step 3 — harness selection (multi-select)

| Choice | What init does | Identity bootstrap |
|---|---|---|
| **Claude Code** | Nothing to write — repo already carries `.mcp.json` + `.claude/skills`. Verify both, print "open this repo in Claude Code and ask it to `list_agents`". Offer `claude mcp add` for use outside the repo. | Tier-A auto-bind (stdio subprocess, no identity args) |
| **OpenClaw** | Emit its workspace/MCP config snippet pointing at `uv run python -m mcp_servers.condor`; skills found by its directory scan. | Tier-A auto-bind |
| **Hermes** | Emit the MCP config + `hermes skills tap add <this repo>` (our repo-root `skills/` is already tap-layout). Label **supported pending the refactor-09 §7 spike** — the approval-posture test still gates *recommending* it, just no longer gates anything of ours. | Tier-A auto-bind |
| **Condor's own** (TG chat + web chat) | Require `TELEGRAM_TOKEN` (walk through BotFather if missing) and require the Step-1 id to be a real TG id. Start/verify the bot; print the `/start` first-steps. | TG id + approved-users check; web via `/login` |
| **None of the above** | Valid: Tier 2 + dashboard only. Print the dashboard URL and a login token (below). | Tier-A / terminal token |

### Step 4 — print "first thing to try" per selection

One line each; no docs-hunting. Re-running `init` adds a harness
without touching the others.

## 3. Web login without Telegram (the one new mechanism)

Today the dashboard's only door is `/login` in the TG chat (mints a
one-time token). Under selection, TG may not exist, so add
`condor login-token`: mint the same one-time token from the terminal
and print the login URL. Same trust model as Tier-A auto-bind — whoever
can run commands on the box already owns config.yml and the JWT
secret. A few lines reusing the existing token-mint path; no new auth
system.

## 4. What this deliberately does NOT include

- **The TUI** (refactor-09 §9) — rides separately; if built, it becomes
  the zero-config surface offered under "Condor's own / None".
- **Notifications changes** — refactor-08 §8 stands (Telegram-first;
  the chat_id=0 fallback bug fix is still pending, and under Step-1's
  "yes" branch it benefits every harness path).
- **setup.sh changes** — that installer lives in `hummingbot/deploy`.
  Sequence: ship `condor init`, make setup.sh's last step "now run
  `condor init`", then strip the installer's Telegram questions. Until
  then the two coexist (init is idempotent over setup.sh's output).

## 5. Why this beats both prior frames

Refactor-09 solved onboarding by making another project the front door
and paying for it with the approval surface. The pre-09 status quo kept
the approval surface by making Telegram a toll booth in front of users
who never wanted it. Selection keeps our harness (approval surface,
reference implementation, funnel) while making the Telegram bot a
*choice* — and it costs one CLI command plus a terminal token mint,
because every underlying path already exists.

## 6. Field study: how OpenClaw and Hermes install themselves

Read in full 2026-07-12 (`openclaw.ai/install.sh`, ~3,500 lines;
`hermes-agent.nousresearch.com/install.sh`, ~3,100 lines). Different
codebases, same converged architecture:

1. **Bash is bootstrap only.** OS/arch detection, dependency install
   with auto-repair (OpenClaw detects npm build-tool failures and
   installs compilers; Hermes installs uv/python/node/git itself),
   fetch code, venv, PATH, config *templates* (never overwrite user
   config). Not one product question is asked in bash.
2. **Product questions live in the product's own CLI.** OpenClaw ends
   with `exec </dev/tty; exec openclaw onboard`; Hermes runs
   `hermes setup < /dev/tty`. The `/dev/tty` rebind is what makes
   prompts work under `curl | bash` — and both probe by actually
   *opening* /dev/tty first (a bare existence test passes inside Docker
   builds where the open then fails).
3. **Idempotent via config-presence branching.** Config present →
   skip onboarding, run `doctor`/verify, restart the daemon if one is
   loaded. Fresh → onboard. Re-running the installer is always safe.
4. **Every prompt has a flag** (`--no-onboard`, `--skip-setup`,
   non-interactive detection); no TTY never hangs — it prints the
   command to finish setup later.
5. **Post-install actions key off wizard outcomes.** Hermes starts its
   gateway only if messaging tokens were actually configured.
6. **Hermes extra: a machine-readable stage protocol** — each stage
   emits `{"ok":true,"stage":"deps","skipped":false}` JSON lines, with
   `stage_needs_user_input` marking where a driver must attach a TTY.
   The installer is designed to be driven *by a program*.

## 7. Condor's current installer, measured against that bar

Today (`make install` → `setup-environment.sh`, 1,101 lines; the
hummingbot/deploy setup.sh wraps the same flow; docs at
condor.hummingbot.org/getting-started/installing): one bash script does
**both halves** — bootstrap (uv sync, node/nvm, the ACP bridge npm
package, TypeScript, optional hummingbot-api Docker stack, Tailscale)
*and* product questions (Telegram token + admin id via `/dev/tty`
prompts, `.env` and config.yml writes). BotFather and @userinfobot are
mandatory documented steps before anything runs.

What it already does right: `/dev/tty` prompts, idempotent `.env`
updates (sed-replace vs append), Docker/Tailscale treated as infra.
What breaks the studied pattern: product questions in bash mean
changing an answer requires re-running the installer, `condor init`
cannot exist while bash owns the config writes, Telegram is assumed
rather than chosen, and there is no non-interactive or
machine-drivable path.

## 8. The Condor install script

```
curl -fsSL https://condor.hummingbot.org/install.sh | bash
# flags after `bash -s --`:
#   --harness claude-code,openclaw,hermes,condor   preseed selection
#   --user-id <int>          identity for non-interactive runs
#   --hummingbot-api / --no-hummingbot-api
#   --no-init                bootstrap only
#   --non-interactive        fail rather than prompt
#   --stage-json             Hermes-style stage lines for agent drivers
```

**Stage A — bash bootstrap** (a refactor of setup-environment.sh, not
a rewrite): detect OS, ensure uv/node, clone-or-update the repo
(config.yml present → upgrade path: sync deps, verify, restart the
tmux session), optional hummingbot-api Docker + Tailscale (infra stays
in bash), copy `.env.example` → `.env` if missing. **All Telegram
prompts removed from bash.**

**Stage B — handoff**: `exec </dev/tty; uv run condor init [--harness …]`
— everything in §2 runs here, in Python, re-runnable forever without
the installer.

**Harness detection makes selection concrete**: before showing the
menu, probe for evidence of each harness on the box — `claude` on PATH,
`openclaw` on PATH / its config dir, `~/.hermes` or `hermes` on PATH —
and preselect what's found ("Found Claude Code and Hermes on this
machine"). Then the §2 writers run per selection:

- **claude-code** — repo `.mcp.json` already works in-repo; offer
  `claude mcp add --scope user condor -- uv run --directory <repo>
  python -m mcp_servers.condor` for use outside it.
- **openclaw** — write the server into its MCP config when installed
  (exact config path pinned during implementation); else print the
  snippet.
- **hermes** — append the MCP server to its config +
  `hermes skills tap add <repo>`. If not installed, offer their
  one-liner and run it only on explicit yes — never silently install
  someone else's software.
- **condor's own** — the BotFather walk (validate the token with
  `getMe` before writing it), start the tmux session.
- **none** — dashboard URL + `condor login-token`.

**Why `--stage-json` earns its place here** when it might look like
gold-plating: Condor's audience *is* agent harnesses. "Ask Claude Code
to install Condor" becomes a real path — the agent runs the installer
with flags, parses the stage lines, and attaches the human only at the
`init` stage. The two projects we studied ship this for exactly that
reason.

**Sequencing**: split setup-environment.sh (product prompts → `condor
init`, the rest stays); ship `install.sh` at condor.hummingbot.org;
point hummingbot/deploy's setup.sh at it; update the docs page so
"Create a Telegram Bot" moves *inside* the Condor-harness branch
instead of being a prerequisite for everyone.

## 9. Open questions

1. Does the web **chat** panel stay part of "Condor's own harness"
   bundled with TG, or become selectable alone? (It shares the session
   machinery; alone-selectable implies web-native `human_gate` is the
   only approval surface — it exists, but is the least exercised.)
2. Should init offer to install Hermes (`pipx install hermes-agent`?)
   or only emit config for an existing install? Lean: config only —
   installing other people's software is the installer's job, not ours.
3. Where does `condor init` document the multi-user path (Tier B+)?
   Out of scope here; the tiered-identity doc owns it.
