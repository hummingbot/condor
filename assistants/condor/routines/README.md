# condor routines

condor's **own** routines, co-located with its skills (`../skills/`) and store
(`../store/`). They're layered on top of the shared library in the repo-root
`routines/`: condor sees **both** the shared base and these.

- Created/edited at runtime via `manage_routines(action="create_routine"/"edit_routine", ...)`
  (no `strategy_id` → lands here) and discovered the same way as the shared base.
- Domain experts have the equivalent at `trading_agents/<slug>/routines/`.
- Generic, cross-domain tools belong in the shared `routines/`; condor-specific
  ones belong here.

Files are plain routine modules (`Config` + `async def run(config, context) -> str`),
loaded by path — same anatomy as any routine.
