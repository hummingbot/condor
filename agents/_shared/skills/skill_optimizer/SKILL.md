---
name: skill_optimizer
description: Automated iterative skill improvement — generates test scenarios, evaluates
  them against a skill, and edits the skill in a loop up to max_optimizations rounds.
when_to_use: 'User asks to automatically optimize, test, red-team, or iteratively
  improve a skill. Triggers: "optimize skill X", "auto-improve skill Y", "run the
  optimization loop for skill Z", "make skill X handle edge cases automatically",
  "iterate and improve skill Y".'
created: '2026-09-04T10:04:59Z'
source: chat
---

## Skill Optimizer — Automated Iterative Skill Improvement

> **Scope**: proactive, background, multi-round. For *reactive* in-conversation edits after a single feedback moment, use `self_improve` instead. Use this when the user asks to systematically improve a skill or when `self_improve` has been applied several times and the skill still has recurring gaps.

### What this does
Runs a background optimization loop for any skill:
1. Reads the current skill
2. Generates realistic test scenarios
3. Evaluates each scenario against the skill (via consult)
4. Synthesizes targeted improvements
5. Edits the skill in-place
6. Repeats up to `max_optimizations` rounds, then stops

Each round sends a progress notification. Stops early if a round produces no changes (skill converged).

---

### Interactive intake (when the user triggers this)

**Step 1 — Confirm the skill exists:**
```
manage_skill(action="read", name=skill_name)
```
If missing, tell the user and stop.

**Step 2 — Collect parameters (ask if not provided):**
- `skill_name` — the skill to optimize
- `requirements` — what the skill must reliably do (success criteria, in plain language)
- `max_optimizations` — rounds (default: 3)
- `scenarios_per_round` — test cases per round (default: 3)
- `agent` — which agent evaluates scenarios (default: "condor")

**Step 3 — Build and start the delegation:**
```
delegate(
  action="start",
  agent="condor",
  on_complete="notify",
  timeout_sec=1800,
  task=<see template below, with all params interpolated>
)
```

**Step 4 — Tell the user it is running in the background and END YOUR TURN.**

---

### Delegation task template

Interpolate all `{placeholders}` before passing to `delegate`:

```
You are running an automated skill optimization loop. Follow these steps exactly.

Target skill: "{skill_name}"
Requirements (what success looks like): "{requirements}"
Max optimization rounds: {max_optimizations}
Scenarios per round: {scenarios_per_round}
Evaluating agent: "{agent}"

===== OPTIMIZATION LOOP =====

Repeat the following block for round = 1, 2, … up to {max_optimizations}.
Stop early if a round produces no changes.

--- Round start ---

1. READ the current skill (do this at the start of EVERY round — a previous round may have changed it):
   manage_skill(action="read", name="{skill_name}")
   Save the body as `current_body`.

2. GENERATE test scenarios:
   consult(
     agent="{agent}",
     task="You are a skill tester. Read this skill and generate {scenarios_per_round} concrete, realistic test scenarios. Each scenario is a specific user request that an agent would need to handle using this skill. Number them. Be adversarial — include edge cases and ambiguous inputs.\n\nRequirements: {requirements}\n\nSkill:\n{current_body}"
   )
   Parse the numbered scenarios from the response.

3. EVALUATE each scenario (run sequentially, one consult per scenario):
   For each scenario N:
   consult(
     agent="{agent}",
     task="You are evaluating a skill. Imagine a user just said this to you: '{scenario}'. Follow the skill's guidance as closely as possible to handle it. Then report:\n(a) What steps the skill directed you to take\n(b) What worked well\n(c) What was incomplete, ambiguous, or forced you to improvise OUTSIDE the skill\n(d) Specific gaps or missing steps\n\nSkill:\n{current_body}"
   )
   Collect all evaluation reports.

4. SYNTHESIZE improvement:
   consult(
     agent="condor",
     task="You are a skill editor. Given these evaluation reports, identify recurring gaps and improve the skill body. Rules: make surgical edits only — add missing steps, clarify ambiguous ones, remove what caused failures. Do NOT bloat the skill. Do NOT change the description, when_to_use, or name fields — only the body. Return the COMPLETE improved body (full markdown, ready to write).\n\nRequirements: {requirements}\n\nEvaluation reports:\n{all_evals}\n\nCurrent skill body:\n{current_body}"
   )

5. APPLY the update (only if body meaningfully changed):
   If the improved body differs from current_body:
     manage_skill(action="edit", name="{skill_name}", body=improved_body)
     changed = True
   Else:
     changed = False

6. NOTIFY round result:
   send_notification(
     f"🔄 *Skill optimizer* — `{skill_name}` round {round}/{max_optimizations}\n\n"
     f"Scenarios tested: {N}\n"
     f"{'✏️ Skill updated — ' + one_line_summary_of_changes if changed else '✅ No changes — skill already handled all scenarios'}\n\n"
     f"{'Starting next round…' if more_rounds_and_changed else 'Stopping — skill converged.' if not changed else '✅ Optimization complete!'}"
   )

7. If more rounds remain AND changed: asyncio.sleep(30), then continue loop.
   If NOT changed: stop (converged).

===== END LOOP =====

FINAL NOTIFICATION:
send_notification(
  f"✅ *Skill optimization complete* — `{skill_name}`\n\n"
  f"Rounds completed: {rounds_done}/{max_optimizations}\n"
  f"Total scenarios tested: {total_scenarios}\n"
  f"Outcome: {brief summary — what kinds of gaps were found and addressed, or 'skill was already solid'}"
)
```

---

### Rules

- **Read the skill at the start of every round** — not once before the loop; previous rounds change it
- **Never change `description`, `when_to_use`, or `name`** — optimizer touches only `body`
- **Never delete the skill** — only edit
- **Skip failed consults** — log the failure in the notification but continue the round
- **Stop early on convergence** — a round with no edits means the skill is stable against this scenario set; stop rather than burning more credits
- **Budget awareness** — 3 rounds × 3 scenarios × ~30s per consult ≈ 5 min; well inside the 30 min worker budget
- **One optimization at a time** — don't start a second loop on the same skill while one is running

---

### Example trigger

> "Optimize the `run_a_grid` skill. It should handle edge cases around narrow ranges and high-volatility markets. Run 3 rounds with 3 scenarios each."

→ Read skill → delegate the loop → end turn.
