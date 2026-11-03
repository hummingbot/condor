---
name: execution_data_with_code
description: Analyze bot/controller/executor performance — use run_code with verified
  schemas; never probe the API structure first.
when_to_use: User asks for controller PNL, volume, or performance; bot execution stats;
  how much a bot earned or traded; fleet-wide performance overview; executor counts;
  PNL breakdown (realized vs unrealized); portfolio distribution by asset type; portfolio
  value over time; controller PNL as a time series or chart; "how are my bots doing",
  "show performance", "what's the total PNL", "which controller made the most", "show
  portfolio evolution", "asset breakdown". Use run_code with the verified schemas
  here — NEVER make exploratory dir()/inspect.signature() calls for any method documented
  in this skill.
created: '2026-09-04T09:09:51Z'
source: chat
---

## Execution Data — `client.bot_orchestration.*` + `client.executors.*` + `client.portfolio.*` inside `run_code`

### ⚠️ STOP — self-audit before any tool call

1. **Did you call `manage_skill(action="read", name="execution_data_with_code")` before writing any code?**
   - NO → You are here now. Good. Read the schemas below; write the COMPLETE snippet on the NEXT call.
   - YES → Proceed. The schema is already in your context.

2. **Is your call covered by the API reference below?**
   - YES → Use it directly. Do NOT call `dir(client)`, `catalog()`, `inspect.signature`, or any raw probe first. Those calls waste turns — the schemas here are verified.
   - NO → A single raw probe is acceptable only for calls not listed here.

**The cost of skipping this read: 5+ wasted discovery calls.**

---

### First-call rule

**Write the complete, final snippet on the first `run_code` call.**
- Aggregate, sort, and format in ONE call — don't split into "fetch then format".
- The whole fleet is one `get_active_bots_status` call — never loop `get_bot_status` per bot.
- `asyncio.gather(..., return_exceptions=True)` + `isinstance(r, Exception)` per result — never catch and swallow silently.

**⚠️ stdout is clipped at ~4000 chars.** When outputting many rows (100+ controllers), only print the top N and rely on `result` for structured data. A second `run_code(action="get")` call to recover truncated output is the same wasted turn as splitting into fetch+format — avoid it by limiting print output upfront. Pattern:
```python
for r in rows[:20]:          # print only top 20
    print(...)
print(f"\n... {len(rows) - 20} more rows in result")
result = rows                # full data always in result
```

---

### When to use what

| Request | Tool | Notes |
|---|---|---|
| All active bots + controllers + PNL | `run_code` → `get_active_bots_status` | Full fleet snapshot — one call |
| One bot's controller performance | `run_code` → `get_bot_status(bot_name)` | Same shape as fleet |
| Controller PNL as time series | `run_code` → `get_controller_performance_history` | See verified schema below |
| Latest controller snapshot (all bots ever) | `run_code` → `get_latest_controller_performance` | ⚠️ flat list, includes stopped bots AND zero-volume zombies — filter both |
| Portfolio balances (formatted) | MCP `get_portfolio_overview` | Returns text — NOT programmatic |
| Spot balances in USD (programmatic) | `run_code` → `client.portfolio.get_state()` | JSON breakdown by token |
| Portfolio value time series | `run_code` → `client.portfolio.get_history()` | Spot balances only — see schema |
| Raw executor list (filter by type/pair/controller) | MCP `list_executors` | No run_code needed |
| Single executor detail + logs | MCP `get_executor` | No run_code needed |
| Aggregate executor PNL by controller_id | MCP `get_performance_report` | No run_code needed; ⚠️ see warning below |
| **Executor history / counts / closed PNL** | `run_code` → `client.executors.search_executors` | See schema below — NOT `search_history` |
| Historical orders | MCP `search_history(data_type="orders")` | CEX order records only |
| Historical perp positions | MCP `search_history(data_type="perp_positions")` | |
| Historical LP positions | MCP `search_history(data_type="clmm_positions")` | |

**⚠️ `search_history` does NOT have an executor data_type.** Executor history comes exclusively from `client.executors.search_executors()`.

**⚠️ `get_portfolio_overview` (MCP tool) returns formatted text, not JSON.** For programmatic access (compute percentages, aggregate types) use `client.portfolio.get_state()` inside `run_code`.

---

### API reference

#### `get_active_bots_status()` — all running bots

```python
resp = await client.bot_orchestration.get_active_bots_status()
data = resp["data"]   # ⚠️ MUST unwrap: resp has {"status": "success", "data": {...}}

# data shape:
# {
#   "bot-name-xyz": {
#     "status": "running",
#     "performance": {
#       "ctrl_name_1": {
#         "status": "running",           # or "stopped"
#         "performance": {
#           "global_pnl_quote":     268.84,   # = realized + unrealized
#           "realized_pnl_quote":   276.99,
#           "unrealized_pnl_quote": -8.16,
#           "realized_pnl_pct":     0.1412,   # FRACTION — multiply by 100 for %
#           "unrealized_pnl_pct":  -0.00416,
#           "volume_traded":        196127.66, # in QUOTE currency of the pair
#           "active_executors":     0,
#           "total_executors":      0,
#         }
#       },
#     }
#   }
# }
```

#### `get_bot_status(bot_name)` — one bot (same payload, single bot)

```python
resp = await client.bot_orchestration.get_bot_status("my-bot-name")
data = resp["data"]  # same shape as above but keyed to one bot only
```

---

#### `get_latest_controller_performance()` — latest snapshot, all bots ever ✅ verified

```
get_latest_controller_performance(bot_name: Optional[str] = None) -> Dict[str, Any]
```

```python
resp = await client.bot_orchestration.get_latest_controller_performance()
# ⚠️ data is a FLAT LIST — NOT a bot-name dict like get_active_bots_status
# ⚠️ includes ALL bots ever (stopped + running) — filter by status == "running"
# ⚠️ includes zombie instances with volume_traded == 0 — filter by vol > 0
# ⚠️ timestamps may be STALE for stopped bots (last-run timestamp, not current)
# ⚠️ the SAME controller_id can appear in MULTIPLE bot rows (one row per bot instance, not per controller)
#    — if you want per-controller totals, aggregate by controller_id with a defaultdict

# resp["data"] shape:
# [
#   {
#     "timestamp":     "2026-09-04T09:22:02.112930+00:00",  # may be stale
#     "bot_name":      "my-bot-name-...",
#     "controller_id": "my_controller_v2",
#     "status":        "running",                            # filter on this
#     "performance": {
#       "realized_pnl_quote":   -269.28,
#       "unrealized_pnl_quote": -45.75,
#       "unrealized_pnl_pct":   -0.00151,    # FRACTION
#       "realized_pnl_pct":     -0.00891,
#       "global_pnl_quote":     -315.03,
#       "global_pnl_pct":       -0.01043,
#       "volume_traded":         3020366.34,  # in QUOTE currency of the pair
#       "positions_summary":    [...],        # per-pair breakdown — MAY BE EMPTY
#       "close_type_counts":    {"CloseType.EARLY_STOP": 6748, ...}
#     },
#     "custom_info": {}
#   }, ...
# ]
# NO pagination key

rows = [
    r for r in resp["data"]
    if r.get("status") == "running"
    and (r.get("performance", {}).get("volume_traded") or 0) > 0   # drop zombies
]
```

**Aggregating by controller_id** (when you want per-controller totals, not per-bot-instance):
```python
from collections import defaultdict

by_ctrl = defaultdict(lambda: {"vol": 0.0, "pnl": 0.0, "instances": 0})
for r in rows:
    p = r["performance"]
    ctrl = r["controller_id"]
    by_ctrl[ctrl]["vol"]       += p.get("volume_traded",    0) or 0
    by_ctrl[ctrl]["pnl"]       += p.get("global_pnl_quote", 0) or 0
    by_ctrl[ctrl]["instances"] += 1
```

---

#### `get_controller_performance_history()` — time series ✅ verified

```
get_controller_performance_history(
    bot_name:      Optional[str] = None,
    controller_id: Optional[str] = None,
    limit:         Optional[int] = None,    # default 100
    cursor:        Optional[str] = None,    # pagination
    start_time:    Optional[str] = None,    # ISO-8601 string (NOT unix int)
    end_time:      Optional[str] = None,    # ISO-8601 string
    interval:      str = "5m"              # ⚠️ ALWAYS use "5m" — coarser silently drops controllers
) -> {"status": "success", "data": [...], "pagination": {...}}
```

**Response:**
- `data`: same item shape as `get_latest_controller_performance` — flat list, one row per controller per time bucket
- `pagination`: `{next_cursor, has_more, limit, interval}` — cursor-paginate when `has_more=True`
- Rows are ordered by timestamp ascending

**⚠️ Interval warning:** intervals coarser than `5m` silently drop controllers from the result — always use `interval="5m"`.

---

#### `client.portfolio.*` — portfolio state and history ✅ verified

```python
# Spot balance state — all accounts/connectors/tokens
state = await client.portfolio.get_state()
# Shape:
# {
#   "account_name": {
#     "connector_name": [
#       {"token": str, "units": float, "price": float,
#        "value": float, "available_units": float}
#     ]
#   }
# }

# Portfolio value history (spot balances only, ~5m snapshots)
hist = await client.portfolio.get_history(
    limit=100,            # default 100
    cursor=None,          # pagination
    start_time=None,      # ⚠️ UNIX timestamp (int), NOT ISO string
    end_time=None,        # UNIX timestamp (int)
    interval=None,        # optional bucket interval
    account_names=None,
    connector_names=None,
)
# Shape:
# {
#   "data": [
#     {
#       "timestamp": "2026-09-04T09:23:00+00:00",  # ISO-8601 UTC
#       "state": {                                   # same shape as get_state()
#         "account_name": {"connector_name": [{token, units, price, value, available_units}]}
#       }
#     }, ...
#   ],
#   "pagination": {"limit": int, "has_more": bool, "next_cursor": str|null, "total_count": int}
# }
```

**⚠️ `client.portfolio.get_history()` captures spot balances only.** Perp unrealized PNL and CLMM LP value are not included in the snapshots.

**⚠️ `start_time`/`end_time` in `get_history()` are UNIX ints, not ISO strings.**

**Other `client.portfolio` methods:** `get_distribution()` (token % distribution), `get_portfolio_summary()`, `get_total_value()`, `get_accounts_distribution()`.

---

#### `client.executors.search_executors()` — executor history ✅ verified

```
search_executors(
    account_names:   List[str] | None = None,
    connector_names: List[str] | None = None,
    trading_pairs:   List[str] | None = None,
    executor_types:  List[str] | None = None,   # e.g. ["grid_executor", "order_executor"]
    status:          str | None = None,          # "RUNNING" or "TERMINATED"
    controller_ids:  List[str] | None = None,
    cursor:          str | None = None,
    limit:           int = 50                    # max 200 per page
) -> {"data": [...], "pagination": {...}}
```

**Per-record fields:** `executor_id`, `executor_type`, `account_name`, `connector_name`, `trading_pair`, `side`, `status`, `close_type`, `is_active`, `is_trading`, `created_at`, `closed_at`, `close_timestamp` (Unix float), `controller_id`, `net_pnl_quote` (realized net of fees), `net_pnl_pct` (fraction), `cum_fees_quote`, `filled_amount_quote` (volume), `config`, `custom_info`.

**Known `executor_type`:** `grid_executor`, `order_executor`, `position_executor`, `dca_executor`, `lp_executor`

**Known `close_type`:** `TAKE_PROFIT`, `POSITION_HOLD`, `EARLY_STOP`, `SYSTEM_CLEANUP`

**⚠️ No date filter.** Results are newest-first. Filter client-side using `close_timestamp` (Unix float) and break early when past the window.

---

#### `client.executors.get_performance_report(controller_id=None)` — aggregate stats ⚠️

```python
{
  "total_executors":      121,
  "by_status":           {"TERMINATED": 121},
  "pnl_total_quote":     870.07,
  "unrealized_pnl_quote": 884.99,
  "global_pnl_quote":    1755.06,
  "fees_total_quote":    263.49,
  "volume_total_quote":  9483019.50,
  "win_rate":            0.5606,   # fraction
  "sharpe_ratio":        0.24,
  "by_type": [...],                # ⚠️ UNRELIABLE COUNTS — see trap below
  "active_positions":    7,
}
```

**⚠️ `by_type` is unreliable for counts** — verified: showed 66 of 121 real executors. Use `search_executors` pagination for accurate type counts.

**`get_summary()` is active-only** — returns zeros when fleet is idle. Not useful for history.

---

### Canonical snippets

#### Active controllers — PNL + volume table (verified, single call)

```python
resp = await client.bot_orchestration.get_active_bots_status()
rows = []
for bot_name, bot_data in resp["data"].items():
    for ctrl_name, ctrl_info in bot_data.get("performance", {}).items():
        if not isinstance(ctrl_info, dict):
            continue
        if ctrl_info.get("status") != "running":
            continue
        p = ctrl_info["performance"]
        rows.append({
            "bot":        bot_name,
            "controller": ctrl_name,
            "pnl":        round(p.get("global_pnl_quote",     0) or 0, 4),
            "realized":   round(p.get("realized_pnl_quote",   0) or 0, 4),
            "unrealized": round(p.get("unrealized_pnl_quote", 0) or 0, 4),
            "volume":     round(p.get("volume_traded",         0) or 0, 2),
            "active_exc": p.get("active_executors", 0) or 0,
            "total_exc":  p.get("total_executors",  0) or 0,
        })

rows.sort(key=lambda r: r["pnl"], reverse=True)
print(f"Active controllers: {len(rows)}")
for r in rows[:20]:   # ⚠️ limit print to top 20 — stdout clips at ~4000 chars
    print(f"{r['controller'][:42]:<44}| PNL={r['pnl']:>10.4f} | Vol={r['volume']:>14.2f} | "
          f"R={r['realized']:>10.4f} | U={r['unrealized']:>9.4f} | Exc={r['active_exc']}/{r['total_exc']}")
if len(rows) > 20:
    print(f"... {len(rows) - 20} more rows in result")
result = rows   # full data always in result
```

#### All-time controller volume comparison — aggregated by controller_id (verified)

```python
from collections import defaultdict

resp = await client.bot_orchestration.get_latest_controller_performance()

by_ctrl = defaultdict(lambda: {"vol": 0.0, "pnl": 0.0, "instances": 0})
for r in resp.get("data", []):
    if r.get("status") != "running":
        continue
    p = r.get("performance", {})
    vol = p.get("volume_traded", 0) or 0
    if vol == 0:
        continue   # skip zombie instances
    ctrl = r["controller_id"]
    by_ctrl[ctrl]["vol"]       += vol
    by_ctrl[ctrl]["pnl"]       += p.get("global_pnl_quote", 0) or 0
    by_ctrl[ctrl]["instances"] += 1

rows = [{"controller": k, **v} for k, v in by_ctrl.items()]
rows.sort(key=lambda r: r["vol"], reverse=True)

print(f"{'Controller':<44} {'Inst':>4} {'Volume (quote)':>16} {'PNL':>12}")
print("-" * 80)
for r in rows[:20]:
    print(f"{r['controller'][:42]:<44} {r['instances']:>4}  {r['vol']:>15,.0f}  {r['pnl']:>10.2f}")
if len(rows) > 20:
    print(f"... {len(rows) - 20} more in result")
result = rows
```

> Note: `volume` is in **quote currency** of each pair. If your fleet mixes USD, USDT and other quote assets, add a price lookup to normalize before comparing across pairs.

#### Controller PNL time series — 24h chart (verified)

```python
from datetime import datetime, timezone, timedelta

end_time = datetime.now(timezone.utc)
start_time = end_time - timedelta(hours=24)

resp = await client.bot_orchestration.get_controller_performance_history(
    start_time=start_time.isoformat(),
    end_time=end_time.isoformat(),
    interval="5m",   # ⚠️ never coarser — intervals >5m silently drop controllers
)
data = resp.get("data", [])
# If resp["pagination"]["has_more"], fetch next page with cursor=resp["pagination"]["next_cursor"]

rows = []
for row in data:
    p = row["performance"]
    rows.append({
        "time":       row["timestamp"],
        "controller": row["controller_id"],
        "bot":        row["bot_name"],
        "pnl":        round(p.get("global_pnl_quote",     0) or 0, 4),
        "realized":   round(p.get("realized_pnl_quote",   0) or 0, 4),
        "unrealized": round(p.get("unrealized_pnl_quote", 0) or 0, 4),
        "volume":     round(p.get("volume_traded",         0) or 0, 2),
    })
rows.sort(key=lambda r: (r["controller"], r["time"]))
result = rows
# → render as ```chart with type="line", x="time", series per controller
```

#### Today vs yesterday — controller comparison (verified)

```python
from datetime import datetime, timezone, timedelta
from collections import defaultdict

now = datetime.now(timezone.utc)
today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
yesterday_start = today_start - timedelta(days=1)

resp = await client.bot_orchestration.get_controller_performance_history(
    start_time=yesterday_start.isoformat(),
    end_time=now.isoformat(),
    interval="5m",
)
data = resp.get("data", [])

daily = defaultdict(lambda: defaultdict(list))  # "today"/"yesterday" → ctrl → [rows]
for row in data:
    ts = datetime.fromisoformat(row["timestamp"])
    day = "today" if ts >= today_start else "yesterday"
    daily[day][row["controller_id"]].append(row)

def last_pnl(rows):
    p = sorted(rows, key=lambda r: r["timestamp"])[-1]["performance"]
    return round(p.get("global_pnl_quote", 0) or 0, 4)

all_ctrls = set(daily["today"].keys()) | set(daily["yesterday"].keys())
comparison = []
for ctrl in all_ctrls:
    t = last_pnl(daily["today"][ctrl])     if daily["today"].get(ctrl)      else None
    y = last_pnl(daily["yesterday"][ctrl]) if daily["yesterday"].get(ctrl)  else None
    comparison.append({
        "controller": ctrl,
        "today":     t,
        "yesterday": y,
        "delta":     (t - y) if (t is not None and y is not None) else None,
    })
comparison.sort(key=lambda r: r["today"] or 0, reverse=True)

print(f"{'Controller':<44} {'Today':>10} {'Yesterday':>10} {'Delta':>10}")
print("-" * 78)
for r in comparison[:20]:
    t = f"{r['today']:>10.4f}"   if r['today']     is not None else f"{'—':>10}"
    y = f"{r['yesterday']:>10.4f}" if r['yesterday'] is not None else f"{'—':>10}"
    d = f"{r['delta']:>+10.4f}"  if r['delta']     is not None else f"{'—':>10}"
    print(f"{r['controller'][:42]:<44} {t} {y} {d}")
if len(comparison) > 20:
    print(f"... {len(comparison) - 20} more in result")
result = comparison
```

#### Portfolio distribution by asset type (verified)

```python
import asyncio

spot_task = client.portfolio.get_state()
perp_task = client.trading.get_open_positions()
spot_state, perp_resp = await asyncio.gather(spot_task, perp_task, return_exceptions=True)

spot_value = 0.0
spot_breakdown = {}
if not isinstance(spot_state, Exception):
    for account_data in spot_state.values():
        for connector_data in account_data.values():
            for token in connector_data:
                v = token.get("value") or 0
                spot_value += v
                spot_breakdown[token["token"]] = spot_breakdown.get(token["token"], 0) + v

perp_positions = []
if not isinstance(perp_resp, Exception):
    perp_positions = perp_resp.get("data", [])

total = spot_value
print(f"Spot:  ${spot_value:>12,.2f}")
print(f"\nTop tokens:")
for tok, val in sorted(spot_breakdown.items(), key=lambda x: x[1], reverse=True)[:10]:
    pct = val / total * 100 if total else 0
    print(f"  {tok:<8} ${val:>12,.2f}  ({pct:.1f}%)")
result = {"spot": spot_value, "breakdown": spot_breakdown}
```

#### Portfolio value time series (verified — spot only)

```python
hist = await client.portfolio.get_history(limit=100)
# ⚠️ Captures spot balances only — perp/LP not included
rows = []
for record in hist["data"]:
    total = sum(
        token.get("value", 0) or 0
        for account_data in record["state"].values()
        for connector_data in account_data.values()
        for token in connector_data
    )
    rows.append({"time": record["timestamp"], "value": round(total, 2)})
rows.sort(key=lambda r: r["time"])
# 100 snapshots ≈ 8.5h of 5m cadence
# → render as ```chart with type="area", x="time", series=[{key:"value"}]
result = rows
```

#### Total executor count by type — accurate via cursor pagination (verified)

```python
from collections import defaultdict

all_execs = []
for status in ("TERMINATED", "RUNNING"):
    cursor = None
    while True:
        resp = await client.executors.search_executors(status=status, cursor=cursor, limit=200)
        all_execs.extend(resp.get("data", []))
        pag = resp.get("pagination", {})
        if not pag.get("has_more"):
            break
        cursor = pag["next_cursor"]

by_type     = defaultdict(int)
by_type_pnl = defaultdict(float)
for e in all_execs:
    t = e["executor_type"]
    by_type[t] += 1
    by_type_pnl[t] += e.get("net_pnl_quote") or 0

type_label = {
    "grid_executor":     "Grid",
    "position_executor": "Position",
    "dca_executor":      "DCA",
    "lp_executor":       "LP",
    "order_executor":    "Order",
}
print(f"Total executors ever run: {len(all_execs)}")
print(f"\n{'Type':<14} {'Count':>7} {'Net PNL':>14}")
print("-" * 38)
for t, cnt in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
    label = type_label.get(t, t)
    print(f"{label:<14} {cnt:>7}   {by_type_pnl[t]:>12.2f}")
result = dict(by_type)
```

#### Closed executors this week — profitable/losing breakdown (verified)

```python
from datetime import datetime, timezone, timedelta

now = datetime.now(timezone.utc)
week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
week_start_ts = week_start.timestamp()

this_week = []
cursor = None
while True:
    resp = await client.executors.search_executors(status="TERMINATED", cursor=cursor, limit=200)
    batch = resp.get("data", [])
    for e in batch:
        if (e.get("close_timestamp") or 0) >= week_start_ts:
            this_week.append(e)
    pag = resp.get("pagination", {})
    oldest_ts = min((e.get("close_timestamp") or 0) for e in batch) if batch else 0
    if oldest_ts < week_start_ts or not pag.get("has_more"):
        break
    cursor = pag["next_cursor"]

this_week.sort(key=lambda e: e.get("net_pnl_quote") or 0, reverse=True)
profitable = [e for e in this_week if (e.get("net_pnl_quote") or 0) > 0]
flat_loss   = [e for e in this_week if (e.get("net_pnl_quote") or 0) <= 0]

print(f"Closed this week: {len(this_week)}  |  ✅ {len(profitable)} profitable  |  ❌ {len(flat_loss)} flat/loss\n")
print(f"{'':2} {'type':<18} {'pair':<14} {'net_pnl':>10} {'volume':>14} {'close_type':<20} closed")
print("-" * 95)
for e in this_week[:20]:   # ⚠️ limit print to avoid stdout clip
    pnl  = e.get("net_pnl_quote") or 0
    vol  = e.get("filled_amount_quote") or 0
    mark = "✅" if pnl > 0 else ("❌" if pnl < 0 else "·")
    closed = (e.get("closed_at") or "")[:16].replace("T", " ")
    print(f"{mark}  {e['executor_type']:<18} {e['trading_pair']:<14} {pnl:>10.2f} {vol:>14.0f} {e.get('close_type',''):<20} {closed}")
if len(this_week) > 20:
    print(f"... {len(this_week) - 20} more in result")

total_pnl = sum((e.get("net_pnl_quote") or 0) for e in this_week)
total_vol  = sum((e.get("filled_amount_quote") or 0) for e in this_week)
print(f"\n{'TOTAL':<38} {total_pnl:>10.2f} {total_vol:>14.0f}")
result = this_week
```

#### Group by bot — fleet-level aggregation

```python
from collections import defaultdict

resp = await client.bot_orchestration.get_active_bots_status()
by_bot = defaultdict(lambda: {"pnl": 0, "volume": 0, "controllers": 0})

for bot_name, bot_data in resp["data"].items():
    for ctrl_name, ctrl_info in bot_data.get("performance", {}).items():
        if not isinstance(ctrl_info, dict) or ctrl_info.get("status") != "running":
            continue
        p = ctrl_info["performance"]
        by_bot[bot_name]["pnl"]         += p.get("global_pnl_quote", 0) or 0
        by_bot[bot_name]["volume"]      += p.get("volume_traded",     0) or 0
        by_bot[bot_name]["controllers"] += 1

for bot, agg in sorted(by_bot.items(), key=lambda x: x[1]["pnl"], reverse=True):
    print(f"{bot[:50]:<52}| PNL={agg['pnl']:>10.2f} | Vol={agg['volume']:>14.0f} | Ctrlrs={agg['controllers']}")
result = dict(by_bot)
```

---

### Common traps

| Trap | Fix |
|---|---|
| `resp["data"]` not unwrapped on `get_active_bots_status` | Always `resp["data"]` — `resp` is `{"status": "...", "data": {...}}` |
| Iterating `get_latest_controller_performance` as a dict | `data` is a **flat list** — iterate `resp["data"]`, not `resp["data"].items()` |
| `get_latest_controller_performance` includes stopped bots | Filter `r["status"] == "running"` — it covers ALL bots ever |
| `get_latest_controller_performance` includes zero-volume zombies | Filter `(r.get("performance", {}).get("volume_traded") or 0) > 0` |
| Same `controller_id` in multiple bot rows | One row per BOT INSTANCE, not per controller — aggregate with `defaultdict` when you want per-controller totals |
| Stale timestamps in `get_latest_controller_performance` | A stopped controller keeps its last-run timestamp — check `status` before trusting the time |
| Using `interval` coarser than `5m` in history | Silently drops controllers — always use `interval="5m"` |
| `get_portfolio_overview` for programmatic use | It returns formatted text — use `client.portfolio.get_state()` inside `run_code` |
| `client.portfolio.get_history()` start/end_time as ISO strings | They are UNIX int timestamps, not ISO strings |
| Portfolio history includes perp/LP | It captures spot balances only |
| Using `search_history` for executor history | `search_history` has no executor type — use `client.executors.search_executors()` |
| Trusting `get_performance_report().by_type` for counts | Verified to undercount: showed 66 of 121 real executors. Use paginated `search_executors` |
| Filtering `search_executors` by date | No date filter — paginate newest-first and break early when `close_timestamp < cutoff` |
| `get_summary()` when nothing running | Returns all zeros — not useful for history |
| `ctrl_info.get("status") != "running"` | Skip stopped/killed controllers or you double-count |
| `p.get("...")` returns `None` | Use `or 0` after every `.get()` |
| Calling `get_bot_status()` without bot_name | Requires bot_name arg; use `get_active_bots_status()` for the fleet |
| Reporting `realized_pnl_pct` / `net_pnl_pct` as-is | Both are fractions (e.g. `0.14`); multiply by 100 to display as `14%` |
| `volume_traded` is in quote currency | Volumes are per-pair quote — normalize via price lookup if comparing across different quote assets |
| Printing all rows when fleet has 100+ controllers | stdout clips at ~4000 chars — print top N, put full data in `result` |

---

### Tips
- **`get_active_bots_status` gives the full active fleet in one call** — use it for anything live.
- **`get_controller_performance_history` is the time-series tool** — always `interval="5m"`, paginate when `has_more`.
- **`client.portfolio.get_history()` is spot-only** — caveat this when reporting portfolio evolution.
- **`net_pnl_quote` = realized PNL net of fees.** `custom_info.realized_pnl_quote` is before fees.
- **MCP tools are fine for single-executor queries** — reach for `run_code` only when you need aggregation, math, or fleet-wide comparison.
- Chart time series with a ` ```chart ` fence; persist with `ReportBuilder`.
- Same snippet 3× → promote to a routine.
