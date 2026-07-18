# Installing Condor

One command installs Condor and asks a single product question — *how
will you talk to it?* You pick one or more harnesses: a coding agent
you already use (Claude Code, Codex, OpenClaw, Hermes), Condor's own chat
(Telegram + web, the default), or none at all (backend + monitoring
dashboard only). Nothing here requires a Telegram bot unless you
choose the Telegram surface.

## Quick start

```bash
curl -fsSL https://condor.hummingbot.org/install.sh | bash
```

That's the whole flow for most people: the script bootstraps, then
drops you into an interactive `condor init` that finishes setup in
under a minute.

## How the installer works

The installer ([`install.sh`](../install.sh)) follows the same
architecture as the OpenClaw and Hermes installers: **bash does
bootstrap only, and every product question lives in the product's own
CLI**, which runs with your terminal attached even under `curl | bash`.

| Stage | What happens | On failure |
|---|---|---|
| `deps` | requires `git`; installs `uv` if missing | stops with the fix |
| `clone` | clones `hummingbot/condor` into `~/condor` (or `--dir`); an existing checkout is fast-forwarded, a dirty one is left untouched | stops rather than overwrite |
| `python` | `uv sync` | stops |
| `env` | copies `.env.example` → `.env` if missing — template only, no values | — |
| `hummingbot-api` | probes `localhost:8000`; prints the [hummingbot/deploy](https://github.com/hummingbot/deploy) install command if nothing answers | continues — init reminds you again |
| `init` | hands off to `condor init` over `/dev/tty` | prints the command to run later if no terminal |

Because all answers live in `condor init` (not in bash), you can change
any of them later by re-running `make init` — without touching the
installer again.

### Installer flags

```bash
curl -fsSL https://condor.hummingbot.org/install.sh | bash -s -- [flags]

--dir <path>        install directory                (default: ~/condor)
--branch <name>     git branch                       (default: main)
--harness <list>    preseed the harness selection    (e.g. claude-code,hermes)
--user-id <int>     preseed identity (enables fully non-interactive runs)
--no-init           bootstrap only, print the init command
--non-interactive   never prompt (requires --user-id unless --no-init)
--stage-json        emit one JSON line per stage: {"ok":true,"stage":"deps"}
```

`--stage-json` exists because Condor's audience is agent harnesses:
"ask Claude Code to install Condor" is a supported path. An agent runs
the installer with flags, watches the stage lines, and attaches you
only when `init` needs a human.

## What `condor init` asks

### 1. Identity — "Do you use Telegram?"

This is the one question that shapes everything else.

- **Yes** → your Telegram user id (from [@userinfobot](https://t.me/userinfobot))
  becomes the approved user id — even if you don't set up the Telegram
  surface today. That future-proofs phone notifications, trade-approval
  buttons, and `/login`, all switchable on later with one token.
- **No** → init mints a random integer id. It's an identifier, not a
  credential — whoever can run commands on the box already owns every
  secret on disk. The trade-off: enabling the Telegram surface later
  requires the id to *be* your Telegram id.

Single-user installs get **identity auto-bind**: any MCP harness that
spawns Condor's server without identity arguments is bound to the sole
approved user automatically. Choosing a multi-user install (extra
approved ids) deliberately disables auto-bind, and init prints the
explicit identity arguments each user's harness must pass instead.

### 2. The execution layer

Init registers the same-box Hummingbot API (`localhost:8000`) and
probes it. If nothing answers you get a loud message with the exact
install command — nothing can trade until that stack runs:

```bash
curl -fsSL https://raw.githubusercontent.com/hummingbot/deploy/main/setup.sh | bash -s -- --hummingbot-api
```

### 3. Harness selection

Init detects what's already on your box (`claude` on PATH, `codex`,
`openclaw`, `~/.hermes`, …) and preselects it alongside the default.
Selection is multi-select — the same backend serves all of them at once.

Coding-agent harnesses are **repo-scoped by design**: Condor's skills and
MCP servers load only when the harness runs from the repo directory —
init never installs anything user-wide (see
[harness-skill-setup.md](harness-skill-setup.md)).

| Choice | What init does | First thing to try |
|---|---|---|
| **Condor harness** (Telegram + web — *selected by default*) | Web works immediately, no token needed. Telegram is offered as a skippable walk: paste a [@BotFather](https://t.me/BotFather) token, init validates it before writing. | `make run`, then `make login-token` and open the URL |
| **Claude Code** | Verifies the repo's `.mcp.json` and repairs the `.claude/skills` mirror (one symlink per skill). | run `claude` in the repo, try `/condor status` |
| **Codex** | Verifies the repo's `.codex/config.toml` and repairs the `.agents/skills` mirror. First `codex` run asks you to trust the directory. | run `codex` in the repo, try `$condor status` |
| **OpenClaw** | Skills come from the workspace scan of `skills/`; prints the (global, but inert outside the repo) `openclaw mcp add` command. | open the workspace at the repo, `/condor` |
| **Hermes** | Prints the MCP config + `hermes skills tap add <repo>` (Hermes has no repo scope — its config is global and just points here). If Hermes isn't installed, init prints *their* installer command and stops there — it never installs another project's software. | ask it to `list_agents` |
| **None** | Backend + monitoring dashboard only, driven entirely from external harnesses. | `make login-token` |

Init is idempotent: re-run `make init` any time to add a harness, add
the Telegram token, or add users. It never destroys existing answers.

## Logging into the dashboard without Telegram

Historically the web dashboard's only door was the `/login` command in
the Telegram chat. Now the box itself can mint one:

```bash
make login-token
# → http://localhost:8088/login?token=eyJ...   (valid 5 minutes)
```

The token is signed with the same secret as web sessions, tagged so it
is *only* good for the login exchange (it is rejected as a session
bearer), and stateless — it works even though the CLI and the web
server are separate processes. Trust model: same as identity auto-bind;
terminal access to the box already implies ownership of the secrets.

## Where the pieces live

- [`install.sh`](../install.sh) — Stage-A bootstrap + handoff
- [`condor/cli.py`](../condor/cli.py) — `init` and `login-token`
  (`make init` / `make login-token`, or `uv run python -m condor.cli …`)
- [changes-from-main.md](changes-from-main.md) — how this flow came to
  be, including the field study of the OpenClaw and Hermes installers it
  is modeled on; [simpler-agent-framework.md](simpler-agent-framework.md)
  — the architecture the install sets up
