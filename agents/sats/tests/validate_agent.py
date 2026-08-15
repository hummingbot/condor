"""Validate the SATS agent against Condor's real loaders (no network, no trading)."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

ok = True

# 1. Routine discovery via Condor's own loader.
from routines.base import discover_routines_from_path, RoutineResult  # noqa: E402

rdir = REPO / "agents/sats/routines"
found = discover_routines_from_path(rdir, agent_slug="sats")
print("=== ROUTINE DISCOVERY ===")
for name in sorted(found):
    info = found[name]
    cat = getattr(info, "category", None) or getattr(getattr(info, "module", None), "CATEGORY", "?")
    fields = list(info.config_class.model_fields) if getattr(info, "config_class", None) else []
    print(f"  [OK] {name:<14} category={cat:<12} config_fields={fields[:6]}...")
if "sats_scan" not in found:
    print("  [FAIL] sats_scan NOT discovered")
    ok = False

# 2. Contract checks: async run + Config + valid CATEGORY.
import inspect  # noqa: E402

print("\n=== ROUTINE CONTRACT ===")
VALID = {"Market Data", "Analysis", "Arbitrage", "Monitoring"}
for name, info in sorted(found.items()):
    has_run = inspect.iscoroutinefunction(info.run_fn)
    cat = info.category
    has_cfg = info.config_class is not None
    good = has_run and has_cfg and cat in VALID
    ok &= good
    print(f"  [{'OK' if good else 'FAIL'}] {name:<14} async_run={has_run} Config={has_cfg} CATEGORY={cat!r} valid={cat in VALID}")

# 3. Agent + strategy frontmatter via Condor's stores.
print("\n=== AGENT / STRATEGY LOADING ===")
import os  # noqa: E402

os.chdir(REPO)
from condor.agents.agent import AgentStore  # noqa: E402
from condor.agents.strategy import StrategyStore  # noqa: E402

agent = AgentStore().get("sats")
if not agent:
    print("  [FAIL] agent 'sats' not loaded")
    ok = False
else:
    print(f"  [OK] agent slug={agent.slug} key={agent.agent_key}")
    print(f"       tools={len(agent.tools)} server_required={agent.server_required}")
    print(f"       when_to_consult={bool(agent.when_to_consult)} (consultable when non-empty)")

strats = [s for s in StrategyStore().list_all() if s.agent_slug == "sats"]
if not strats:
    print("  [FAIL] no strategy loaded for sats")
    ok = False
for s in strats:
    print(f"  [OK] strategy key={s.key} name={s.name}")
    print(f"       freq={s.default_config.get('frequency_sec')}s risk={s.default_config.get('risk_limits')}")
    print(f"       context={s.default_trading_context!r}")

# 4. Folder name must equal slugified strategy name.
from condor.agents.strategy import _slugify  # noqa: E402

for s in strats:
    exp = _slugify(s.name)
    d = (REPO / "agents/sats/strategies" / exp).is_dir()
    ok &= d
    print(f"  [{'OK' if d else 'FAIL'}] folder '{exp}' matches slugified name")

print("\n=== RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT", "===")
sys.exit(0 if ok else 1)
