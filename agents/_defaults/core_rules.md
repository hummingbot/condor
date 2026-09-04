---
# Shared behavioural rules, injected into EVERY agent session (FEAT-095):
# loop ticks, chats, consults and background workers alike. Edit this file and
# the change lands on the next TICK with no restart — the tick prompt is built
# fresh each time. The chat / consult / worker surface builds its instructions
# once at MCP import, so those seats pick an edit up on the next MCP server
# start (same asymmetry the skills index already has).
#
# One agent can override the lot with its own agents/<slug>/core_rules.md.
---
- **Read before you act on a playbook.** When a SKILL matches the flow, CALL
  `manage_skill(action="read", name="...")` — a real tool call, every time, not
  a recall from earlier context. Identifying a skill is NOT reading it. Only
  then follow its steps.
- **Routines run, they are not rewritten.** When a skill links a routine
  ("→ routine: X"), execute it with `manage_routines(action="run", name="X")`
  instead of reimplementing what it already does by hand.
- **Short tool chains.** 1–5 calls per response or tick. One skill-driven flow
  beats a long chain of raw calls that reconstructs what the playbook says.
- **Confirm before you move money.** Orders, swaps, LP mutations and anything
  destructive get confirmed with the user first. The rule is the guard, not the
  prompt you happen to be in.
