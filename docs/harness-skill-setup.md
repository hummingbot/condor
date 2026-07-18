# Loading the `/condor` skill in your harness

The `/condor` skill ([`skills/condor/SKILL.md`](../skills/condor/SKILL.md))
turns a coding harness into a Condor front-end: routing rules, execution verbs
(run / consult / delegate / dry run), run tracking, and the ops CLI.

It is the **single place that defines what Condor does**. The same file is
loaded three ways:

1. **Chat brain** — `condor/agents/context.py` injects its body as the system
   prompt of Condor's own Telegram/web sessions (the file formerly known as
   `CONDOR.md`; the chat's default model rides in the skill's
   `condor-agent-key` metadata).
2. **/condor skill** — external harnesses (Claude Code, Codex, OpenClaw,
   Hermes) load it natively; this doc covers that wiring.
3. **MCP fallback** — the Condor MCP server's connect-time instructions are
   just a pointer ("read the `condor` skill") plus the live skills/agents
   indexes, so even a host with no skill support routes correctly after one
   `manage_skill(action="read", name="condor")` call.

## Scoping policy: repo-only

Condor's skills and MCP servers load **only when the harness runs from this
directory**. Nothing is installed user-wide. The wiring is committed to the
repo and `condor init` verifies/repairs it — run
`condor init --harness claude-code,codex,openclaw,hermes` (or interactively)
and follow its output. What each harness uses:

| Harness | Skills | MCP servers | Truly repo-scoped? |
|---|---|---|---|
| Claude Code | `.claude/skills/` (symlinks into `skills/`) | `.mcp.json` | yes |
| Codex | `.agents/skills/` (symlinks into `skills/`) | `.codex/config.toml` | yes (requires trusting the dir) |
| OpenClaw | `~/.openclaw/skills/condor` symlink (auto-fixed); workspace scan of `skills/` when the workspace is the repo | global config (`openclaw mcp add …`) | no — a gateway install runs from its own workspace, so the /condor skill is linked globally (OpenClaw-only dir; deliberately not `~/.agents/skills`, which Codex reads) |
| Hermes | `~/.hermes/skills/condor` symlink (auto-fixed; a "tap" is a GitHub *source*, useless for a local checkout) | global, via `hermes mcp add` (auto-fixed) | no — Hermes has no repo scope; the links/config just *point* here |

Everything symlinks back to `skills/` — one copy, so `manage_skill` edits and
`git pull` updates propagate to every harness at once.

## Per-harness usage

**Claude Code** — run `claude` inside the repo. `.mcp.json` autoloads the
`condor` + `mcp-hummingbot` servers; `.claude/skills` exposes `/condor`.
Verify: `/condor` appears in the slash-command list; "list my agents" calls
`mcp__condor__list_agents`.

**Codex** — run `codex` inside the repo and trust the directory when asked
(project MCP config requires trust). `.codex/config.toml` carries the MCP
servers; `.agents/skills` is scanned from `$CWD` up to the repo root. Verify:
`/skills` lists `condor`; invoke explicitly with `$condor` in a prompt.

**OpenClaw** — a gateway install runs from `~/.openclaw/workspace`, so the
repo's skills are invisible to it; `condor init` / `condor doctor --fix` link
`~/.openclaw/skills/condor → <repo>/skills/condor` (the OpenClaw-only managed
dir — not `~/.agents/skills`, which would leak the skill to Codex user-wide).
A workspace opened at the repo additionally scans the full `skills/` set. MCP
registration is global-only — doctor warns with the `openclaw mcp add`
command when missing. Verify in a fresh session: `/condor` works as a slash
command; `openclaw mcp tools condor` lists `list_agents`.

**Hermes** — no repo-scoped loading exists; skills and MCP config are global.
`condor init` / `condor doctor --fix` register the MCP server via
`hermes mcp add` (the tool-enable prompt is answered and the result verified
against `hermes mcp list` — a cancelled add exits 0!) and link
`~/.hermes/skills/condor → <repo>/skills/condor`, Hermes' primary skills dir,
where it becomes a native skill/slash command. Do NOT use
`hermes skills tap add` for this — a tap is a GitHub install *source* and a
local-path tap can never be fetched. Verify: `hermes skills list | grep
condor`; `/skill condor` loads it; tools appear as the `mcp-condor` toolset.

## Notes

- **Identity.** Single-user installs auto-bind: any harness spawning the MCP
  server without identity arguments is bound to the sole approved user.
  Multi-user installs disable auto-bind — `condor init` prints the explicit
  identity args each harness must add to the server command.
- **Editing the skill.** Edit `skills/condor/SKILL.md` directly, or via
  `manage_skill(action="edit"|"patch", name="condor")` from any connected
  harness. Keep frontmatter values single-line or OpenClaw drops the skill,
  and preserve the `condor-agent-key` metadata — it sets the chat surface's
  default model. Because the file is the chat brain, an edit here changes
  Condor's own chat behavior too — that is the point (no drift), but treat
  edits with system-prompt care.
- **Anti-drift enforcement.** `condor/cli/commands/_wiring.py` defines the
  canon: `.claude/skills` and `.agents/skills` mirror `skills/`
  one-symlink-per-skill, and `.mcp.json` / `.codex/config.toml` carry the
  canonical `condor` + `mcp-hummingbot` entries. `condor init` repairs all of
  it; `condor doctor` reports drift and `condor doctor --fix` repairs it
  (extra servers you add to `.mcp.json` are preserved; a customized
  `.codex/config.toml` is never clobbered — you get a "fix manually" note).
  Hermes is auto-fixed too, via Hermes' own CLI (`hermes mcp add` +
  `hermes skills tap add`, condor entries only — the add prompt is answered
  and the result verified against `hermes mcp list`). OpenClaw's config is
  checked read-only with the remedy command printed.
  `tests/test_skill_conformance.py` enforces the Claude Code mirror in CI.
- **Health.** If a harness connects but tools fail, run `condor doctor` — it
  checks the server, DB, and Hummingbot API before you debug harness config.
- **Uninstall / clean-install testing.** `condor uninstall` is the inverse of
  init: it strips every *global* condor trace — harness skill links, MCP
  registrations (via each harness's own CLI; only condor entries), and the
  `~/.local/bin/condor` PATH link — so a fresh `condor init` or the curl
  installer can be tested as a new user would experience it. Repo-scoped
  wiring stays (a new user gets it by cloning); `rm -rf` the checkout for a
  fully clean box. Foreign symlinks/config are reported and left alone.
  Remember `condor doctor --fix` re-registers OpenClaw/Hermes wiring — don't
  run it mid-experiment.
