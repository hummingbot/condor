# Tool style

How a tool on either MCP server (`mcp_servers/hummingbot_api`, `mcp_servers/condor`)
is shaped, and what its docstring has to carry.

This file is **descriptive**. It was written alongside the executor split (FEAT-062)
by reading the tools that FEAT-063 (market data), FEAT-064 (swaps) and FEAT-068
(agents/strategies/instances) had already shipped, so it records the convention those
tools converged on rather than proposing a new one. When a rule below and a shipped
tool disagree, one of them is wrong — say which in the change that fixes it.

## The signature is the schema

A tool's parameters are what the MCP host validates before the call is made. Anything
expressible in a type goes in the signature, not in the prose:

- **One tool, one job.** A tool that dispatches on an `action` literal is one tool
  wearing several hats, and every session pays for all of them. Split when the hats
  have different shapes (`quote_swap` vs `execute_swap`) or different danger
  (`list_executors` vs `create_grid_executor`). Keep an `action` literal only when the
  actions genuinely share a parameter set — `manage_clmm`, `manage_bots`.
- **`Literal` for every enum.** `side: Literal[1, 2]`, `execution_strategy:
  Literal["MARKET", "LIMIT", "LIMIT_MAKER", "LIMIT_CHASER"]`. A wrong value is then a
  client-side error naming the parameter, not a server round trip.
- **Never `dict[str, Any]` for something with a known shape.** A config blob is
  unvalidatable by the protocol and forces a schema-fetch round trip before the real
  call. Flatten it into typed parameters; assemble the nested payload inside the impl.
  `extra_params` is the exception — it is genuinely open, connector-defined, and named
  so.
- **Optional means `None`.** Give an optional parameter a `None` default and drop it in
  the impl rather than repeating the backend's default in the signature. Repeating it
  freezes a value the backend owns, and — for the executor family — it would clobber
  the user's saved defaults, which merge underneath whatever the call actually sent.
  State the backend's default in the `Args:` line instead.
- **Name for grep.** A tool name is an identifier in prompts, skills, agent markdown,
  per-seat tool profiles and the danger gate. `create_grid_executor` is greppable;
  `grid` is not.

## Danger is carried by the name

`condor/runtime/danger.py` classifies a call before a human ever sees it. Prefer a name
the gate can classify on its own:

- A tool that always moves funds goes in `DANGEROUS_TOOLS` by name — `execute_swap`,
  every `create_*_executor`, `stop_executor`. No argument has to be read, so there is no
  unreadable-argument ambiguity to fail closed on.
- A tool that only sometimes moves funds is gated on one literal field, and the gate
  fails closed when that field cannot be read — `manage_clmm` and `manage_amm` on
  `action`, `manage_gateway_config` on `resource_type`.
- **A read tool must be safe by name.** If a name can be both, it is two tools.
- Every gated name must resolve to a really-registered tool, and every gated literal to
  a real member of that tool's `Literal` — `tests/test_dangerous_gate_names_resolve.py`
  asserts exactly this, and `tests/test_acp_permission_gate.py` makes a newly registered
  action-gated tool fail CI until it is classified.

## The docstring

Written for a model with no other context, in this order. Only the first line is
mandatory; a small read tool legitimately stops after `Args:`.

1. **One imperative line**, ending in a period, carrying the sharpest thing about the
   tool. Not "Tool for swaps" — `"Sign and submit a one-shot DEX swap on-chain. Spends
   real funds — quote first."`
2. **When to use it, and when not.** Prose or bullets. The "not" half must name the tool
   that is right instead: a model that reads only this paragraph should still route
   correctly. State the cost asymmetry where there is one ("a quote costs nothing and
   moves nothing").
3. **Mechanics the types cannot express**, under ALL-CAPS leads when there are several
   (`CONNECTOR FORMAT`, `POOL RESOLUTION`, `DIRECTION RULES`). This is the place for
   traps: which currency an amount is in, which field is the network and not the DEX,
   what an error message actually means. Say what a failure means when the wording
   misleads — "No pool found" means unknown, not unsafe.
4. **`Actions:`** bullets, for the tools that legitimately keep an `action` literal.
5. **`Args:`** one entry per parameter. Units, enum meanings, and the backend default
   for anything defaulted to `None` ("OMIT to use the connector's configured slippage;
   '0' is a real value").
6. **`Example:` / `Examples:`** one or two real calls. Worth more than another paragraph
   for a small model.
7. **`Returns:`** when the shape is not obvious from the summary.

**Length is set by the mechanics, not by a line budget.** `get_prices` is nine lines and
complete; `quote_swap` is fifty and every one of them is a trap somebody hit. What does
*not* belong at any length is **workflow knowledge** — how to choose between strategies,
what to do with the result, a multi-tool procedure. That is a skill
(`agents/*/skills/`). The rule of thumb: *types teach the call, skills teach the
decision.* If a paragraph would still be true with a different tool, it is a skill.

## Impl split

`server.py` holds the registration, the typed signature and the docstring; `tools/*.py`
holds the work. The registered function builds its arguments, calls one impl, and
formats. Keep the impl free of MCP types so it is directly testable, and keep the
docstring in `server.py` where the host reads it.
